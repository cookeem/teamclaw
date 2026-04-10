from __future__ import annotations

import http.server
import json
import os
import socketserver
from pathlib import Path

ROOT = Path("/app/frontend/dist")
PORT = int(os.environ.get("TEAMCLAW_FRONTEND_PORT", "8080"))


class SpaHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        # Keep stdout clean and consistent with container logs style.
        print(f"[frontend] {self.address_string()} - {fmt % args}")

    def do_GET(self) -> None:
        request_path = self.path.split("?", 1)[0]
        if request_path == "/env.js":
            api_base = os.environ.get("TEAMCLAW_API_BASE", "").strip()
            ws_base = os.environ.get("TEAMCLAW_WS_BASE", "").strip()
            payload = (
                "window.__TEAMCLAW_RUNTIME__ = "
                + json.dumps({"API_BASE": api_base, "WS_BASE": ws_base}, ensure_ascii=False)
                + ";\n"
            )
            encoded = payload.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            return

        if self.path.startswith("/api/") or self.path.startswith("/ws/"):
            self.send_error(404, "Not Found")
            return

        target = self.translate_path(self.path)
        if os.path.exists(target) and not os.path.isdir(target):
            return super().do_GET()

        # SPA fallback: any unknown route returns index.html.
        self.path = "/index.html"
        return super().do_GET()


if __name__ == "__main__":
    ROOT.mkdir(parents=True, exist_ok=True)
    with socketserver.TCPServer(("0.0.0.0", PORT), SpaHandler) as httpd:
        print(f"[frontend] serving {ROOT} on 0.0.0.0:{PORT}")
        httpd.serve_forever()
