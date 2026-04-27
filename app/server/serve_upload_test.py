"""
Minimal test server for the Upload Data UI.
Serves dashboard.html and stubs /api/upload-walk — logs what it receives, no Drive/GCS needed.

Usage:
    python app/server/serve_upload_test.py
Then open: http://localhost:8765/dashboard.html
"""

import email
import email.policy
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SITE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "outputs" / "site"
PORT = int(os.environ.get("PORT", 8765))


def _parse_multipart(headers, body: bytes) -> tuple[dict, dict]:
    content_type = headers.get("Content-Type", "")
    raw = f"Content-Type: {content_type}\r\n\r\n".encode() + body
    msg = email.message_from_bytes(raw, policy=email.policy.compat32)
    fields: dict = {}
    files: dict = {}
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        cd = part.get("Content-Disposition", "")
        if not cd:
            continue
        params: dict = {}
        for chunk in cd.split(";")[1:]:
            chunk = chunk.strip()
            if "=" in chunk:
                k, v = chunk.split("=", 1)
                params[k.strip()] = v.strip().strip('"')
        name = params.get("name", "")
        filename = params.get("filename", "")
        payload = part.get_payload(decode=True) or b""
        if filename:
            files.setdefault(name, []).append((filename, len(payload)))
        elif name:
            fields[name] = payload.decode("utf-8", errors="replace").strip()
    return fields, files


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[http] {fmt % args}")

    def _send(self, code, ct, body):
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0].lstrip("/") or "dashboard.html"
        file_path = SITE_DIR / path
        if file_path.exists() and file_path.is_file():
            ct = "text/html" if path.endswith(".html") else "application/octet-stream"
            self._send(200, ct, file_path.read_bytes())
        else:
            self._send(404, "text/plain", b"Not found")

    def do_POST(self):
        endpoint = self.path.split("?")[0]

        if endpoint == "/api/upload-walk":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""
            fields, files = _parse_multipart(self.headers, body)

            walk_code = (
                f"{fields.get('backpack','')}_{fields.get('collector','')}_{fields.get('borough','')}_{fields.get('route','')}_{fields.get('date','')}_{fields.get('tod','')}"
            ).upper()

            print(f"\n[upload] Walk code: {walk_code}")
            print(f"[upload] Metadata fields: {fields}")
            for name, file_list in files.items():
                for fname, size in file_list:
                    print(f"[upload]   {name}: {fname} ({size} bytes)")

            resp = json.dumps({"ok": True, "walk": walk_code}).encode()
            self._send(200, "application/json", resp)
            return

        self._send(404, "text/plain", b"Unknown endpoint")


if __name__ == "__main__":
    if not SITE_DIR.exists():
        print(f"ERROR: {SITE_DIR} not found — run build_dashboard.py first")
        raise SystemExit(1)
    print(f"Upload UI test server running at http://localhost:{PORT}/dashboard.html")
    print("Press Ctrl+C to stop.")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
