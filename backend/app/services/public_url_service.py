"""
Shared helpers for resolving Prism's public base URL behind reverse proxies.
"""

from __future__ import annotations

from fastapi import Request

from app.core.config import settings


def _normalize_base_url(base_url: str | None) -> str:
    """Return normalized base URL (no trailing slash)."""
    return (base_url or "").strip().rstrip("/")


def _first_forwarded_value(header_value: str | None) -> str:
    """Return the left-most value from a possibly comma-separated forwarded header."""
    if not header_value:
        return ""
    return header_value.split(",", 1)[0].strip()


def resolve_public_base_url(
    request: Request,
    explicit: str | None = None,
) -> str:
    """
    Resolve the externally visible origin for absolute URLs.

    Precedence:
    1. Explicit override argument
    2. PUBLIC_BASE_URL from environment
    3. X-Forwarded-Proto + (X-Forwarded-Host or Host)
    4. request.base_url, with scheme rewritten from X-Forwarded-Proto when present
    """
    normalized = _normalize_base_url(explicit)
    if normalized:
        return normalized

    normalized = _normalize_base_url(settings.PUBLIC_BASE_URL)
    if normalized:
        return normalized

    forwarded_proto = _first_forwarded_value(request.headers.get("x-forwarded-proto")).lower()
    forwarded_host = _first_forwarded_value(
        request.headers.get("x-forwarded-host") or request.headers.get("host")
    )

    if forwarded_proto in {"http", "https"} and forwarded_host:
        return f"{forwarded_proto}://{forwarded_host}".rstrip("/")

    base = str(request.base_url).rstrip("/")
    if forwarded_proto in {"http", "https"} and "://" in base:
        _, remainder = base.split("://", 1)
        return f"{forwarded_proto}://{remainder}"

    return base
