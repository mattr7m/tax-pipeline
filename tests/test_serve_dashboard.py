"""Tests for serve_dashboard.py — DashboardHandler routing, file serving, and security."""

import io
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest.mock import patch

import pytest

from serve_dashboard import DashboardHandler, RELOAD_SCRIPT


def _make_handler(tmp_path, path="/", inject_reload=True, dashboard_content=None):
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
