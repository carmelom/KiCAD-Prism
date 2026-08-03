"""
Helpers for generating KiCad comments REST source URLs.
"""

from __future__ import annotations

from fastapi import Request

from app.core.config import settings
from app.services.public_url_service import resolve_public_base_url


def _normalize_base_url(base_url: str | None) -> str:
    """Return normalized base URL (no trailing slash)."""
    raw = (base_url or "").strip()

    if not raw:
        raw = settings.COMMENTS_API_BASE_URL.strip()

    return raw.rstrip("/")


def resolve_comments_base_url(
    request: Request,
    explicit_base_url: str | None = None,
) -> str:
    """
    Resolve base URL for comments helper links.

    Precedence:
    1. Explicit endpoint query override.
    2. COMMENTS_API_BASE_URL from environment.
    3. PUBLIC_BASE_URL / forwarded headers / request.base_url
       (via resolve_public_base_url).
    """
    normalized = _normalize_base_url(explicit_base_url)
    if normalized:
        return normalized

    return resolve_public_base_url(request)


def build_comments_source_urls(project_id: str, base_url: str | None = None) -> dict:
    """
    Build URL set required by KiCad comments source settings.

    Returns both relative and absolute URLs.
    """
    root = f"/api/projects/{project_id}/comments"
    relative = {
        "list_url": root,
        "patch_url_template": f"{root}" + "/{id}",
        "reply_url_template": f"{root}" + "/{id}/replies",
        "delete_url_template": f"{root}" + "/{id}",
    }

    normalized = _normalize_base_url(base_url)

    if normalized:
        absolute = {
            "list_url": f"{normalized}{relative['list_url']}",
            "patch_url_template": f"{normalized}{relative['patch_url_template']}",
            "reply_url_template": f"{normalized}{relative['reply_url_template']}",
            "delete_url_template": f"{normalized}{relative['delete_url_template']}",
        }
    else:
        absolute = dict(relative)

    return {
        "base_url": normalized,
        "relative": relative,
        "absolute": absolute,
    }
