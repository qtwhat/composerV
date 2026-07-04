"""Local http.server that shows the confirm form and writes the submission back to the store."""

from __future__ import annotations

import http.server
import os
import threading
import urllib.parse
import webbrowser

from composerv.confirm.apply import apply_submission
from composerv.confirm.form import parse_confirm_submission, render_confirm_form
from composerv.faces.review import person_rows


def make_confirm_server(store, scope: str, *, port: int = 0):
    """Start a threaded local server; return (server, url, done_event). done_event is set once
    the user submits /save. Caller is responsible for server.shutdown()."""
    # Prefetch everything GET needs in this (the store's) thread: sqlite connections are
    # single-thread, so the handler thread must not touch `store` — POST reopens by path.
    rows = person_rows(store)
    brief0 = store.get_brief(scope)
    db_path = store.path
    store_cls = type(store)
    crops = {r.person_id: r.rep_crop for r in rows}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path == "/crop":
                q = urllib.parse.parse_qs(u.query)
                pid = int(q.get("id", ["-1"])[0])
                path = crops.get(pid, "")
                if path and os.path.exists(path):
                    with open(path, "rb") as f:
                        self._send(200, f.read(), "image/jpeg")
                else:
                    self._send(404, b"", "text/plain")
                return
            html = render_confirm_form(rows, brief0,
                                       crop_url=lambda pid: f"/crop?id={pid}")
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n).decode("utf-8")
            flat = {k: v[0] for k, v in
                    urllib.parse.parse_qs(raw, keep_blank_values=True).items()}
            updates, brief = parse_confirm_submission(flat)
            apply_submission(store_cls(db_path), scope, updates, brief)
            self._send(200, "已保存，可关闭本页。".encode("utf-8"), "text/html; charset=utf-8")
            done.set()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    return srv, url, done


def serve_confirm(store, scope: str, *, port: int = 0, open_browser: bool = True,
                  log=print, timeout_s: float | None = None) -> str:
    """Open the confirm form in a browser and block until the user submits (or timeout)."""
    srv, url, done = make_confirm_server(store, scope, port=port)
    log(f"确认页已打开：{url}  填完点保存即可（保存后可关页）")
    if open_browser:
        webbrowser.open(url)
    try:
        done.wait(timeout=timeout_s)
    finally:
        srv.shutdown()
    return url
