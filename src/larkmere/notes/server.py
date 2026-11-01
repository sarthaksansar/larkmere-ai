"""The local review page. Standard library only, bound to this machine."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources

from larkmere.notes.review import NotApproved


def serve(queue, port=0):
    """Start the review server in the background. Returns (server, url)."""
    lock = threading.Lock()
    page = resources.files("larkmere.web").joinpath("review.html").read_text(encoding="utf-8")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _send(self, status, body, kind="application/json"):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/":
                self._send(200, page, "text/html; charset=utf-8")
            elif self.path == "/api/drafts":
                with lock:
                    self._send(200, json.dumps({"drafts": queue.view(), "finished": queue.finished()}))
            else:
                self._send(404, "{}")

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            try:
                with lock:
                    if self.path == "/api/approve":
                        queue.approve(body["draft_id"], body.get("reviewer", ""), body.get("text", ""))
                    elif self.path == "/api/reject":
                        queue.reject(body["draft_id"], body.get("reviewer", ""), body.get("reason", ""))
                    else:
                        self._send(404, "{}")
                        return
            except NotApproved as refused:
                self._send(409, json.dumps({"error": str(refused)}))
                return
            self._send(200, json.dumps({"ok": True}))

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/"
