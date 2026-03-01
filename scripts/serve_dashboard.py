#!/usr/bin/env python3
"""
serve_dashboard.py - Lightweight dev server for tax-dashboard.html

Serves the generated dashboard with auto-refresh support so the browser
updates live as pipeline steps regenerate the HTML.

Usage:
    python scripts/serve_dashboard.py [--host 0.0.0.0] [--port 8000] [--no-reload]
"""

import functools
import mimetypes
import os
import socket
import sys
import urllib.parse
from http import HTTPStatus
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

import click
import yaml

# Resolve project root (parent of scripts/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_config():
    config_path = PROJECT_ROOT / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


RELOAD_SCRIPT = """
<script>
(function() {
  var lastMtime = null;
  setInterval(function() {
    fetch("/mtime").then(function(r) { return r.text(); }).then(function(t) {
      if (lastMtime === null) { lastMtime = t; return; }
      if (t !== lastMtime) { location.reload(); }
    }).catch(function() {});
  }, 2000);
})();
</script>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    """Serves the dashboard HTML, mtime endpoint, and linked project files."""

    def __init__(self, *args, dashboard_path, project_root, inject_reload=True, **kwargs):
        self.dashboard_path = dashboard_path
        self.project_root = project_root
        self.inject_reload = inject_reload
        super().__init__(*args, **kwargs)

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._serve_dashboard()
        elif self.path == "/mtime":
            self._serve_mtime()
        else:
            self._serve_file()

    def _serve_dashboard(self):
        if not self.dashboard_path.exists():
            self.send_error(
                HTTPStatus.NOT_FOUND,
                f"Dashboard not found: {self.dashboard_path.name}\n"
                "Run: python scripts/inventory.py --year 2025",
            )
            return

        html = self.dashboard_path.read_text(encoding="utf-8")

        if self.inject_reload:
            html = html.replace("</body>", RELOAD_SCRIPT + "</body>", 1)

        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _serve_mtime(self):
        try:
            mtime = str(os.path.getmtime(self.dashboard_path))
        except OSError:
            mtime = "0"

        body = mtime.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self):
        """Serve a project file referenced by the dashboard."""
        # Decode percent-encoded URL and strip query/fragment
        raw_path = urllib.parse.unquote(self.path.split("?")[0].split("#")[0])
        # Remove leading slash to get relative path
        rel = raw_path.lstrip("/")
        file_path = (self.project_root / rel).resolve()

        # Guard against path traversal
        if not str(file_path).startswith(str(self.project_root.resolve())):
            self.send_error(HTTPStatus.FORBIDDEN)
            return

        if not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        # Don't serve vault/age files over the network
        if file_path.suffix == ".age":
            self.send_error(HTTPStatus.FORBIDDEN)
            return

        content_type, _ = mimetypes.guess_type(str(file_path))
        if content_type is None:
            content_type = "application/octet-stream"

        try:
            body = file_path.read_bytes()
        except OSError:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Suppress per-request logging for the mtime polling endpoint
        if len(args) >= 1 and "/mtime" in str(args[0]):
            return
        super().log_message(format, *args)


def _get_local_ip():
    """Get the host's LAN IP address."""
    try:
        # Connect to a public address to determine which interface is used;
        # no data is actually sent.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("1.1.1.1", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


@click.command()
@click.option("--host", default="0.0.0.0", help="Address to bind to (default: 0.0.0.0)")
@click.option("--port", default=8000, help="Port to serve on (default: 8000)")
@click.option(
    "--no-reload",
    is_flag=True,
    default=False,
    help="Disable auto-refresh injection",
)
def main(host, port, no_reload):
    """Start a local server for the tax pipeline dashboard."""
    config = load_config()
    dashboard_rel = config.get("paths", {}).get("dashboard", "tax-dashboard.html")
    dashboard_path = PROJECT_ROOT / dashboard_rel

    handler = functools.partial(
        DashboardHandler,
        dashboard_path=dashboard_path,
        project_root=PROJECT_ROOT,
        inject_reload=not no_reload,
    )

    server = HTTPServer((host, port), handler, bind_and_activate=False)
    server.allow_reuse_address = True
    server.server_bind()
    server.server_activate()

    status = "found" if dashboard_path.exists() else "NOT FOUND"
    reload_status = "on" if not no_reload else "off"
    lan_ip = _get_local_ip()
    click.echo(f"Dashboard: {dashboard_path} ({status})")
    click.echo(f"Auto-reload: {reload_status}")
    if host == "0.0.0.0":
        click.echo(f"Serving at http://127.0.0.1:{port} (local)")
        click.echo(f"           http://{lan_ip}:{port} (network)")
    else:
        click.echo(f"Serving at http://{host}:{port}")
    click.echo("Press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        click.echo("\nStopped.")
        server.server_close()
        sys.exit(0)


if __name__ == "__main__":
    main()
