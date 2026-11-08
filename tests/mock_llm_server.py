"""A local HTTP server that imitates the Anthropic and OpenAI APIs for offline tests.

The provider SDKs use their own HTTP stack (httpx2), so respx cannot intercept them;
pointing base_url at this server exercises the real request/response path instead.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class MockLLMServer:
    def __init__(self, tool_input: dict[str, Any], tool_name: str = "Classification"):
        self.tool_input = tool_input
        self.tool_name = tool_name
        self.requests: list[tuple[str, dict[str, Any], dict[str, str]]] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                server.requests.append((self.path, body, dict(self.headers)))
                if self.path.endswith("/messages"):
                    name = body["tools"][0]["name"] if body.get("tools") else server.tool_name
                    resp = {
                        "id": "msg_1", "type": "message", "role": "assistant",
                        "model": body["model"], "stop_reason": "tool_use", "stop_sequence": None,
                        "content": [{"type": "tool_use", "id": "toolu_1", "name": name,
                                     "input": server.tool_input}],
                        "usage": {"input_tokens": 123, "output_tokens": 45},
                    }
                elif self.path.endswith("/chat/completions"):
                    resp = {
                        "id": "c1", "object": "chat.completion", "created": 1, "model": body["model"],
                        "choices": [{"index": 0, "finish_reason": "stop", "message": {
                            "role": "assistant", "content": json.dumps(server.tool_input)}}],
                        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
                    }
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = json.dumps(resp).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._httpd.shutdown()
        self._httpd.server_close()
