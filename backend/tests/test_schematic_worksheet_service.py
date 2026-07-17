"""Tests for schematic_worksheet_service (embedded ``.kicad_wks`` recovery)."""

import base64
import os
import sys

import zstandard

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services import schematic_worksheet_service as wks  # noqa: E402


WKS_TEXT = (
    '(kicad_wks (version 20220228) (generator "prism-test")\n'
    '  (setup (textsize 1.5 1.5) (linewidth 0.15) (left_margin 10) (right_margin 10)\n'
    '         (top_margin 10) (bottom_margin 10))\n'
    '  (tbtext "© 2025-2026 ZuriQ" (name "company") (pos 2 2 lbcorner))\n'
    ')\n'
)


def _embed(text: str, name: str = "zuriq_sheet.kicad_wks", type_atom: str = "worksheet") -> str:
    """Build a schematic snippet with an embedded, zstd-compressed worksheet blob."""
    blob = zstandard.ZstdCompressor().compress(text.encode("utf-8"))
    data_b64 = base64.b64encode(blob).decode("ascii")
    # Wrap the base64 in KiCad's ``|...|`` data delimiter across a couple of lines.
    chunked = "\n\t\t\t\t".join(data_b64[i : i + 76] for i in range(0, len(data_b64), 76))
    return (
        "(kicad_sch\n"
        "\t(embedded_files\n"
        "\t\t(file\n"
        f'\t\t\t(name "{name}")\n'
        f"\t\t\t(type {type_atom})\n"
        f"\t\t\t(data |{chunked}|)\n"
        '\t\t\t(checksum "DEADBEEF")\n'
        "\t\t)\n"
        "\t)\n"
        ")\n"
    )


def test_extract_embedded_worksheet_roundtrip():
    sch = _embed(WKS_TEXT)
    out = wks.extract_embedded_worksheet(sch)
    assert out is not None
    assert "(kicad_wks" in out
    assert "© 2025-2026 ZuriQ" in out


def test_no_embedded_files_returns_none():
    assert wks.extract_embedded_worksheet("(kicad_sch (version 20250114))") is None


def test_embedded_non_worksheet_only_returns_none():
    # An embedded datasheet (not a worksheet) must not be mistaken for a template.
    sch = _embed("%PDF-1.4 not a worksheet", name="datasheet.pdf", type_atom="datasheet")
    assert wks.extract_embedded_worksheet(sch) is None


def test_named_wks_fallback_when_type_missing():
    # Some writers omit ``(type worksheet)``; fall back to the *.kicad_wks name.
    blob = zstandard.ZstdCompressor().compress(WKS_TEXT.encode("utf-8"))
    data_b64 = base64.b64encode(blob).decode("ascii")
    sch = (
        "(kicad_sch (embedded_files (file "
        '(name "custom.kicad_wks") '
        f"(data |{data_b64}|) "
        '(checksum "X"))))'
    )
    out = wks.extract_embedded_worksheet(sch)
    assert out is not None and "(kicad_wks" in out


def test_corrupt_data_returns_none():
    sch = (
        "(kicad_sch (embedded_files (file "
        '(name "z.kicad_wks") (type worksheet) '
        "(data |bm90LXZhbGlkLXpzdGQ=|) "  # base64 of "not-valid-zstd"
        '(checksum "X"))))'
    )
    assert wks.extract_embedded_worksheet(sch) is None


def test_resolve_prefers_external_path_when_readable():
    external = '(kicad_wks (version 1) (tbtext "EXTERNAL"))'
    pro = '{ "meta": {}, "schematic": { "page_layout_descr_file": "frame.kicad_wks" } }'
    out = wks.resolve_worksheet(
        _embed(WKS_TEXT), project_settings_content=pro, load_file=lambda p: external if p == "frame.kicad_wks" else None
    )
    assert out == external


def test_resolve_kicad_embed_ref_uses_embedded():
    pro = '{ "schematic": { "page_layout_descr_file": "kicad-embed://zuriq_sheet.kicad_wks" } }'
    out = wks.resolve_worksheet(_embed(WKS_TEXT), project_settings_content=pro, load_file=lambda p: None)
    assert out is not None and "© 2025-2026 ZuriQ" in out


def test_resolve_falls_back_to_embedded_without_pro():
    out = wks.resolve_worksheet(_embed(WKS_TEXT))
    assert out is not None and "(kicad_wks" in out
