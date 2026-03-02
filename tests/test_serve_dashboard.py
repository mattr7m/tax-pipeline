"""Tests for serve_dashboard.py — DashboardHandler routing, file serving, and security."""

import base64
import io
from email.parser import Parser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest.mock import patch

import pytest

from serve_dashboard import DashboardHandler, RELOAD_SCRIPT


def _make_handler(tmp_path, path="/", inject_reload=True, dashboard_content=None,
                  auth_username=None, auth_password=None, auth_header=None):
    """Construct a DashboardHandler without starting a real server.

    Uses object.__new__ to skip BaseHTTPRequestHandler.__init__ (which
    expects a real socket), then manually sets the attributes needed
    for response generation and calls do_GET().
    """
    dashboard_path = tmp_path / "tax-dashboard.html"
    if dashboard_content is not None:
        dashboard_path.write_text(dashboard_content)

    wfile = io.BytesIO()

    handler = object.__new__(DashboardHandler)
    # DashboardHandler-specific attributes
    handler.dashboard_path = dashboard_path
    handler.project_root = tmp_path
    handler.inject_reload = inject_reload
    handler.auth_username = auth_username
    handler.auth_password = auth_password
    # BaseHTTPRequestHandler attributes for response writing
    handler.request_version = "HTTP/1.1"
    handler.command = "GET"
    handler.path = path
    handler.requestline = f"GET {path} HTTP/1.1"
    handler.client_address = ("127.0.0.1", 12345)
    handler.close_connection = True
    handler._headers_buffer = []
    handler.wfile = wfile
    # Suppress stderr logging during tests
    handler.log_request = lambda *a, **kw: None
    # Build headers (needed for auth check)
    header_text = ""
    if auth_header:
        header_text = f"Authorization: {auth_header}\r\n"
    handler.headers = Parser().parsestr(header_text)

    handler.do_GET()
    wfile.seek(0)
    return handler, wfile


def _parse_response(wfile):
    """Parse raw HTTP response bytes into (status_code, headers_str, body_str)."""
    raw = wfile.read().decode("utf-8", errors="replace")
    parts = raw.split("\r\n\r\n", 1)
    header_block = parts[0]
    body = parts[1] if len(parts) > 1 else ""
    status_line = header_block.split("\r\n")[0]
    status_code = int(status_line.split(" ", 2)[1])
    return status_code, header_block, body


class TestDashboardRouting:
    """Tests for do_GET dispatch."""

    def test_root_serves_dashboard(self, tmp_path):
        """GET / returns 200 with HTML content."""
        _, wfile = _make_handler(tmp_path, "/", dashboard_content="<html>hi</html>")
        status, headers, body = _parse_response(wfile)
        assert status == 200
        assert "text/html" in headers
        assert "hi" in body

    def test_index_html_serves_dashboard(self, tmp_path):
        """GET /index.html returns same as /."""
        _, wfile = _make_handler(tmp_path, "/index.html", dashboard_content="<html>hello</html>")
        status, _, body = _parse_response(wfile)
        assert status == 200
        assert "hello" in body

    def test_mtime_endpoint(self, tmp_path):
        """GET /mtime returns 200 with numeric timestamp."""
        _, wfile = _make_handler(tmp_path, "/mtime", dashboard_content="<html></html>")
        status, headers, body = _parse_response(wfile)
        assert status == 200
        assert "text/plain" in headers
        # Should be a float-like string
        float(body.strip())

    def test_dashboard_not_found(self, tmp_path):
        """Dashboard file missing returns 404."""
        _, wfile = _make_handler(tmp_path, "/", dashboard_content=None)
        status, _, _ = _parse_response(wfile)
        assert status == 404


class TestReloadInjection:
    """Tests for auto-refresh script injection."""

    def test_reload_script_injected(self, tmp_path):
        """With inject_reload=True, response contains setInterval polling script."""
        html = "<html><body></body></html>"
        _, wfile = _make_handler(tmp_path, "/", inject_reload=True, dashboard_content=html)
        _, _, body = _parse_response(wfile)
        assert "setInterval" in body
        assert "/mtime" in body

    def test_no_reload_when_disabled(self, tmp_path):
        """With inject_reload=False, no script injected."""
        html = "<html><body></body></html>"
        _, wfile = _make_handler(tmp_path, "/", inject_reload=False, dashboard_content=html)
        _, _, body = _parse_response(wfile)
        assert "setInterval" not in body


class TestFileServing:
    """Tests for _serve_file with security focus."""

    def test_serves_valid_file(self, tmp_path):
        """GET /data/test.json returns 200 with correct content."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "test.json").write_text('{"ok": true}')

        _, wfile = _make_handler(
            tmp_path, "/data/test.json", dashboard_content="<html></html>"
        )
        status, headers, body = _parse_response(wfile)
        assert status == 200
        assert '{"ok": true}' in body

    def test_path_traversal_blocked(self, tmp_path):
        """GET /../../etc/passwd returns 403."""
        _, wfile = _make_handler(
            tmp_path, "/../../etc/passwd", dashboard_content="<html></html>"
        )
        status, _, _ = _parse_response(wfile)
        assert status == 403

    def test_age_file_blocked(self, tmp_path):
        """GET /data/vault/2025.age returns 403."""
        vault_dir = tmp_path / "data" / "vault"
        vault_dir.mkdir(parents=True)
        (vault_dir / "2025.age").write_text("encrypted-stuff")

        _, wfile = _make_handler(
            tmp_path, "/data/vault/2025.age", dashboard_content="<html></html>"
        )
        status, _, _ = _parse_response(wfile)
        assert status == 403

    def test_missing_file_404(self, tmp_path):
        """Nonexistent path returns 404."""
        _, wfile = _make_handler(
            tmp_path, "/data/nope.json", dashboard_content="<html></html>"
        )
        status, _, _ = _parse_response(wfile)
        assert status == 404

    def test_mime_type_json(self, tmp_path):
        """.json file gets application/json content type."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "test.json").write_text("{}")

        _, wfile = _make_handler(
            tmp_path, "/data/test.json", dashboard_content="<html></html>"
        )
        _, headers, _ = _parse_response(wfile)
        assert "application/json" in headers

    def test_mime_type_markdown(self, tmp_path):
        """.md file gets text/markdown content type."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "notes.md").write_text("# Hello")

        _, wfile = _make_handler(
            tmp_path, "/data/notes.md", dashboard_content="<html></html>"
        )
        _, headers, _ = _parse_response(wfile)
        assert "text/markdown" in headers


class TestLogSuppression:
    """Tests for log_message filtering."""

    def _make_bare_handler(self, tmp_path):
        """Create a handler without calling do_GET, for log testing."""
        dashboard_path = tmp_path / "tax-dashboard.html"
        dashboard_path.write_text("<html></html>")

        handler = object.__new__(DashboardHandler)
        handler.dashboard_path = dashboard_path
        handler.project_root = tmp_path
        handler.inject_reload = True
        handler.auth_username = None
        handler.auth_password = None
        handler.request_version = "HTTP/1.1"
        handler.client_address = ("127.0.0.1", 12345)
        handler.requestline = "GET / HTTP/1.1"
        return handler

    def test_mtime_suppressed(self, tmp_path):
        """/mtime requests don't call super().log_message."""
        handler = self._make_bare_handler(tmp_path)
        with patch.object(BaseHTTPRequestHandler, "log_message") as mock_log:
            handler.log_message("%s", "GET /mtime HTTP/1.1")
            mock_log.assert_not_called()

    def test_other_requests_logged(self, tmp_path):
        """Non-mtime requests do log."""
        handler = self._make_bare_handler(tmp_path)
        with patch.object(BaseHTTPRequestHandler, "log_message") as mock_log:
            handler.log_message("%s", "GET / HTTP/1.1")
            mock_log.assert_called_once()


def _encode_basic(username, password):
    """Encode credentials as a Basic auth header value."""
    creds = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {creds}"


class TestBasicAuth:
    """Tests for HTTP Basic Auth."""

    def test_no_auth_configured_allows_access(self, tmp_path):
        """When auth is disabled, all requests pass through."""
        _, wfile = _make_handler(tmp_path, "/", dashboard_content="<html>ok</html>")
        status, _, _ = _parse_response(wfile)
        assert status == 200

    def test_missing_credentials_returns_401(self, tmp_path):
        """Auth enabled but no credentials returns 401 with WWW-Authenticate."""
        _, wfile = _make_handler(
            tmp_path, "/", dashboard_content="<html>ok</html>",
            auth_username="admin", auth_password="secret123",
        )
        status, headers, _ = _parse_response(wfile)
        assert status == 401
        assert "WWW-Authenticate" in headers
        assert "Basic" in headers

    def test_valid_credentials_allow_access(self, tmp_path):
        """Correct credentials return 200."""
        _, wfile = _make_handler(
            tmp_path, "/", dashboard_content="<html>ok</html>",
            auth_username="admin", auth_password="secret123",
            auth_header=_encode_basic("admin", "secret123"),
        )
        status, _, body = _parse_response(wfile)
        assert status == 200
        assert "ok" in body

    def test_wrong_password_returns_401(self, tmp_path):
        _, wfile = _make_handler(
            tmp_path, "/", dashboard_content="<html>ok</html>",
            auth_username="admin", auth_password="secret123",
            auth_header=_encode_basic("admin", "wrongpass"),
        )
        status, _, _ = _parse_response(wfile)
        assert status == 401

    def test_wrong_username_returns_401(self, tmp_path):
        _, wfile = _make_handler(
            tmp_path, "/", dashboard_content="<html>ok</html>",
            auth_username="admin", auth_password="secret123",
            auth_header=_encode_basic("hacker", "secret123"),
        )
        status, _, _ = _parse_response(wfile)
        assert status == 401

    def test_malformed_auth_header_returns_401(self, tmp_path):
        """Non-Basic auth scheme returns 401."""
        _, wfile = _make_handler(
            tmp_path, "/", dashboard_content="<html>ok</html>",
            auth_username="admin", auth_password="secret123",
            auth_header="Bearer some-token",
        )
        status, _, _ = _parse_response(wfile)
        assert status == 401

    def test_invalid_base64_returns_401(self, tmp_path):
        """Corrupted base64 in auth header returns 401."""
        _, wfile = _make_handler(
            tmp_path, "/", dashboard_content="<html>ok</html>",
            auth_username="admin", auth_password="secret123",
            auth_header="Basic not-valid-b64!!!",
        )
        status, _, _ = _parse_response(wfile)
        assert status == 401

    def test_auth_protects_mtime(self, tmp_path):
        """Auth applies to /mtime endpoint."""
        _, wfile = _make_handler(
            tmp_path, "/mtime", dashboard_content="<html>ok</html>",
            auth_username="admin", auth_password="secret123",
        )
        status, _, _ = _parse_response(wfile)
        assert status == 401

    def test_auth_protects_file_serving(self, tmp_path):
        """Auth applies to file serving routes."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "test.json").write_text("{}")
        _, wfile = _make_handler(
            tmp_path, "/data/test.json", dashboard_content="<html>ok</html>",
            auth_username="admin", auth_password="secret123",
        )
        status, _, _ = _parse_response(wfile)
        assert status == 401
