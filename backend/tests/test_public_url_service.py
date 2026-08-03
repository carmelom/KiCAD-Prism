from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.comments_url_service import resolve_comments_base_url  # noqa: E402
from app.services.public_url_service import resolve_public_base_url  # noqa: E402


def _request(
    *,
    base_url: str = "http://backend:8000/",
    headers: dict[str, str] | None = None,
) -> MagicMock:
    request = MagicMock()
    request.base_url = base_url
    request.headers = headers or {}
    return request


class PublicUrlServiceTests(unittest.TestCase):
    def test_explicit_override_wins(self) -> None:
        request = _request(
            headers={
                "x-forwarded-proto": "https",
                "x-forwarded-host": "proxy.example.com",
            }
        )
        with patch("app.services.public_url_service.settings") as settings:
            settings.PUBLIC_BASE_URL = "https://env.example.com"
            self.assertEqual(
                resolve_public_base_url(request, explicit="https://explicit.example.com/"),
                "https://explicit.example.com",
            )

    def test_public_base_url_env_wins_over_headers(self) -> None:
        request = _request(
            headers={
                "x-forwarded-proto": "https",
                "x-forwarded-host": "proxy.example.com",
            }
        )
        with patch("app.services.public_url_service.settings") as settings:
            settings.PUBLIC_BASE_URL = "https://prism.example.com/"
            self.assertEqual(
                resolve_public_base_url(request),
                "https://prism.example.com",
            )

    def test_forwarded_proto_and_host(self) -> None:
        request = _request(
            headers={
                "x-forwarded-proto": "https",
                "x-forwarded-host": "prism.example.com",
            }
        )
        with patch("app.services.public_url_service.settings") as settings:
            settings.PUBLIC_BASE_URL = ""
            self.assertEqual(
                resolve_public_base_url(request),
                "https://prism.example.com",
            )

    def test_forwarded_proto_falls_back_to_host_header(self) -> None:
        request = _request(
            headers={
                "x-forwarded-proto": "https",
                "host": "prism.example.com",
            }
        )
        with patch("app.services.public_url_service.settings") as settings:
            settings.PUBLIC_BASE_URL = ""
            self.assertEqual(
                resolve_public_base_url(request),
                "https://prism.example.com",
            )

    def test_comma_separated_forwarded_proto_uses_leftmost(self) -> None:
        request = _request(
            headers={
                "x-forwarded-proto": "https, http",
                "x-forwarded-host": "prism.example.com, internal",
            }
        )
        with patch("app.services.public_url_service.settings") as settings:
            settings.PUBLIC_BASE_URL = ""
            self.assertEqual(
                resolve_public_base_url(request),
                "https://prism.example.com",
            )

    def test_forwarded_proto_rewrites_request_base_url_scheme(self) -> None:
        request = _request(
            base_url="http://prism.example.com/",
            headers={"x-forwarded-proto": "https"},
        )
        with patch("app.services.public_url_service.settings") as settings:
            settings.PUBLIC_BASE_URL = ""
            # No Host / X-Forwarded-Host → rewrite scheme on request.base_url.
            self.assertEqual(
                resolve_public_base_url(request),
                "https://prism.example.com",
            )

    def test_no_headers_uses_request_base_url(self) -> None:
        request = _request(base_url="http://127.0.0.1:8000/")
        with patch("app.services.public_url_service.settings") as settings:
            settings.PUBLIC_BASE_URL = ""
            self.assertEqual(
                resolve_public_base_url(request),
                "http://127.0.0.1:8000",
            )


class CommentsUrlOriginTests(unittest.TestCase):
    def test_comments_api_base_url_wins_over_public_base_url(self) -> None:
        request = _request(
            headers={
                "x-forwarded-proto": "https",
                "x-forwarded-host": "proxy.example.com",
            }
        )
        with (
            patch("app.services.comments_url_service.settings") as comments_settings,
            patch("app.services.public_url_service.settings") as public_settings,
        ):
            comments_settings.COMMENTS_API_BASE_URL = "https://comments.example.com"
            public_settings.PUBLIC_BASE_URL = "https://prism.example.com"
            self.assertEqual(
                resolve_comments_base_url(request),
                "https://comments.example.com",
            )

    def test_comments_falls_back_to_shared_public_resolver(self) -> None:
        request = _request(
            headers={
                "x-forwarded-proto": "https",
                "x-forwarded-host": "prism.example.com",
            }
        )
        with (
            patch("app.services.comments_url_service.settings") as comments_settings,
            patch("app.services.public_url_service.settings") as public_settings,
        ):
            comments_settings.COMMENTS_API_BASE_URL = ""
            public_settings.PUBLIC_BASE_URL = ""
            self.assertEqual(
                resolve_comments_base_url(request),
                "https://prism.example.com",
            )


if __name__ == "__main__":
    unittest.main()
