"""Authentication through the installed SDK over real local HTTP."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from distr.core.mcp.streamable_sdk import StreamableSdkSession


def test_authentication_survives_initialize_discovery_and_tool_call():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((body["method"], self.headers.get("x-consumer-api-key")))
            if "id" not in body:
                self.send_response(202)
                self.end_headers()
                return
            result = {
                "initialize": {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}, "serverInfo": {"name": "auth-test", "version": "1"}},
                "tools/list": {"tools": [{"name": "probe", "inputSchema": {"type": "object"}}]},
                "tools/call": {"content": [{"type": "text", "text": "connected"}]},
            }[body["method"]]
            payload = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    session = StreamableSdkSession(f"http://127.0.0.1:{server.server_port}/mcp", {"x-consumer-api-key": "test-consumer-key"})
    try:
        session.start(timeout=10)
        assert session.list_tools()["tools"][0]["name"] == "probe"
        assert session.call_tool("probe", {})["content"][0]["text"] == "connected"
    finally:
        session.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert {method for method, _ in seen} >= {"initialize", "tools/list", "tools/call"}
    assert all(key == "test-consumer-key" for _, key in seen)
