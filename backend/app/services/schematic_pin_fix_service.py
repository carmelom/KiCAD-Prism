"""Repair within-symbol pin-number collisions for the vendored ecad-viewer.

The viewer's parser resolves each *placed* pin's geometry and name via
``lib_symbol.pin_by_number(pin.number)``, backed by a map keyed on the pin
number *text*. Symbols whose pins share a number collapse in that map -- most
commonly functional / block symbols where every pin number is empty (``""``),
but also any symbol with duplicate numbers. All but one colliding pin then
resolve to the *same* definition, so they stack at one location and the rest
visually disappear.

We repair this in the served ``.kicad_sch`` text (no viewer change): for every
embedded lib symbol that has a within-symbol number collision we renumber ALL of
its pins to unique synthetic values and set ``(pin_numbers (hide yes))`` on the
symbol so those synthetic numbers are not drawn; the matching placed-instance
``(pin ...)`` refs are renumbered the same way (by declaration order).

Why an order-preserving bijection is enough: a pin's position and name always
come from the *library* definition, never from the placed instance. So mapping
"the k-th instance pin to the k-th library pin" renders every pin at its correct
place with its correct name regardless of the original per-pin identity. The
cost -- accepted deliberately -- is that any genuine pin number on an affected
symbol is hidden too (affected symbols are overwhelmingly unnumbered anyway).

All rewrites are scoped string edits on the raw text (only pin-number values
change, plus one ``(pin_numbers ...)`` node per affected symbol), applied from
the end of the file so earlier offsets stay valid. Stdlib-only and free of any
framework import so the security/format-critical logic is host-testable.
"""
from __future__ import annotations

import re
from typing import Dict, Iterator, List, Optional, Tuple

# ``(pin`` that starts a pin definition or instance ref -- but NOT ``(pin_names``
# or ``(pin_numbers`` (those have ``_`` after ``pin``, excluded by the lookahead).
_PIN_RE = re.compile(r"\(pin(?=[\s\"(])")
_LIB_SYMBOLS_RE = re.compile(r"\(lib_symbols(?=[\s()])")
_PIN_NUMBERS_RE = re.compile(r"\(pin_numbers(?=[\s()])")
_NUMBER_RE = re.compile(r'\(number\s+"')
_NESTED_SYMBOL_RE = re.compile(r'\(symbol(?=[\s"])')
_LIB_ID_RE = re.compile(r'\(lib_id\s+"((?:[^"\\]|\\.)*)"')
_SYMBOL_NAME_RE = re.compile(r'\(symbol\s+"((?:[^"\\]|\\.)*)"')

# One edit: replace text[start:end] with ``repl`` (start == end means insert).
Edit = Tuple[int, int, str]


def _match_paren(text: str, i: int) -> int:
    """Given ``text[i] == '('`` return the index of the matching ``')'`` (or -1)."""
    depth = 0
    in_str = False
    n = len(text)
    j = i
    while j < n:
        c = text[j]
        if in_str:
            if c == "\\":
                j += 2
                continue
            if c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return j
        j += 1
    return -1


def _head(text: str, open_paren: int) -> str:
    """Return the head token of the list opening at ``open_paren``."""
    j = open_paren + 1
    n = len(text)
    while j < n and text[j] in " \t\r\n":
        j += 1
    k = j
    while k < n and text[k] not in " \t\r\n()\"":
        k += 1
    return text[j:k]


def _direct_children(text: str, open_i: int, close_i: int) -> Iterator[Tuple[str, int, int]]:
    """Yield ``(head, child_open, child_close)`` for the direct child lists.

    ``open_i``/``close_i`` bracket a list; its head token is skipped so only
    genuine child lists are yielded (never the list itself).
    """
    i = open_i + 1
    # skip the head token of the enclosing list
    while i < close_i and text[i] in " \t\r\n":
        i += 1
    while i < close_i and text[i] not in " \t\r\n()\"":
        i += 1
    while i < close_i:
        c = text[i]
        if c == '"':
            # skip string
            i += 1
            while i < close_i and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if c == "(":
            ce = _match_paren(text, i)
            if ce < 0 or ce > close_i:
                return
            yield (_head(text, i), i, ce)
            i = ce + 1
            continue
        i += 1


def _pin_number_spans(text: str, start: int, end: int) -> List[Tuple[int, int]]:
    """Return ``(value_start, value_end)`` for every pin's number in ``[start, end)``.

    Handles both library pins (``(pin bidirectional line ... (number "N" ...))``)
    and instance refs (``(pin "N" (uuid ...))``), in file order. Pin nodes are
    skipped whole once handled, so inner lists are never mistaken for pins.
    """
    spans: List[Tuple[int, int]] = []
    i = start
    while True:
        m = _PIN_RE.search(text, i, end)
        if not m:
            break
        p_open = m.start()
        p_close = _match_paren(text, p_open)
        if p_close < 0 or p_close > end:
            break
        j = m.end()
        while j < p_close and text[j] in " \t\r\n":
            j += 1
        if j < p_close and text[j] == '"':
            # instance ref: the quoted token right after ``(pin`` is the number
            vs = j + 1
            ve = text.find('"', vs, p_close)
            if ve != -1:
                spans.append((vs, ve))
        else:
            # library pin: first ``(number "..."`` inside the pin node
            nm = _NUMBER_RE.search(text, m.end(), p_close)
            if nm:
                vs = nm.end()
                ve = text.find('"', vs, p_close)
                if ve != -1:
                    spans.append((vs, ve))
        i = p_close + 1
    return spans


def _has_collision(values: List[str]) -> bool:
    return len(values) != len(set(values))


def _synthetic_number(index: int) -> str:
    """Unique, order-stable synthetic pin number (hidden, so value is cosmetic)."""
    return str(index + 1)


def _pin_numbers_hide_edit(text: str, s_open: int, s_close: int) -> Edit:
    """Edit that forces ``(pin_numbers (hide yes))`` on the symbol at ``s_open``.

    Replaces an existing own-level ``(pin_numbers ...)`` node if present, else
    injects one right after the symbol name. Nested unit-child symbols are never
    touched (their pin-number visibility is irrelevant to the placed instance).
    """
    name_m = _SYMBOL_NAME_RE.match(text, s_open)
    name_end = name_m.end() if name_m else s_open + 1
    nested = _NESTED_SYMBOL_RE.search(text, name_end, s_close)
    header_end = nested.start() if nested else s_close

    existing = _PIN_NUMBERS_RE.search(text, name_end, header_end)
    if existing:
        po = existing.start()
        pc = _match_paren(text, po)
        if pc != -1:
            return (po, pc + 1, "(pin_numbers (hide yes))")

    line_start = text.rfind("\n", 0, s_open) + 1
    indent = text[line_start:s_open]
    return (name_end, name_end, "\n" + indent + "\t(pin_numbers (hide yes))")


def _apply_edits(text: str, edits: List[Edit]) -> str:
    for start, end, repl in sorted(edits, key=lambda e: e[0], reverse=True):
        text = text[:start] + repl + text[end:]
    return text


def fix_colliding_pin_numbers(text: str) -> str:
    """Return ``text`` with within-symbol pin-number collisions repaired.

    A no-op (returns the input unchanged) when there is no ``(lib_symbols ...)``
    block or no symbol collides.
    """
    ls = _LIB_SYMBOLS_RE.search(text)
    if not ls:
        return text
    ls_open = ls.start()
    ls_close = _match_paren(text, ls_open)
    if ls_close < 0:
        return text

    edits: List[Edit] = []
    # lib symbol name -> expected pin count (used to renumber its instances)
    affected: Dict[str, int] = {}

    for head, s_open, s_close in _direct_children(text, ls_open, ls_close):
        if head != "symbol":
            continue
        name_m = _SYMBOL_NAME_RE.match(text, s_open)
        if not name_m:
            continue
        name = name_m.group(1)
        spans = _pin_number_spans(text, s_open, s_close)
        if not spans:
            continue
        values = [text[a:b] for a, b in spans]
        if not _has_collision(values):
            continue
        affected[name] = len(spans)
        for k, (a, b) in enumerate(spans):
            edits.append((a, b, _synthetic_number(k)))
        edits.append(_pin_numbers_hide_edit(text, s_open, s_close))

    if not affected:
        return text

    # Renumber placed instances of affected symbols (direct children of root).
    root_open = text.find("(")
    if root_open != -1:
        root_close = _match_paren(text, root_open)
        for head, b_open, b_close in _direct_children(text, root_open, root_close):
            if head != "symbol" or b_open == ls_open:
                continue
            lib_id_m = _LIB_ID_RE.search(text, b_open, b_close)
            if not lib_id_m or lib_id_m.group(1) not in affected:
                continue
            for k, (a, b) in enumerate(_pin_number_spans(text, b_open, b_close)):
                edits.append((a, b, _synthetic_number(k)))

    return _apply_edits(text, edits)
