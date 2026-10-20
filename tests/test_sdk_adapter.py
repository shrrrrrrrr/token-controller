"""Real localhost sockets + actual LiteLLM SDK, fake upstream (never Qwen)."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

from gateway.providers import LiteLLMText, verified_usage


def test_litellm_sdk_http_contract():
    received = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, value):
            content = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            assert self.path == "/v1/models"
            self.reply({"data": [{"id": "qwen3-32b"}]})

        def do_POST(self):
            assert self.path == "/v1/chat/completions"
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.reply(dict(id="chatcmpl-fixture", object="chat.completion", created=1, model="qwen3-32b",
                            choices=[dict(index=0, message=dict(role="assistant", content="fake upstream"), finish_reason="stop")],
                            usage=dict(prompt_tokens=7, completion_tokens=3, total_tokens=10)))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        async def run():
            provider = LiteLLMText(f"http://127.0.0.1:{server.server_port}/v1", "fixture-not-a-real-upstream-key")
            assert (await provider.probe())["model"] == "qwen3-32b"
            response = await provider.complete(dict(messages=[{"role": "user", "content": "fixture"}], max_tokens=32, temperature=0.2))
            assert verified_usage(response) == [7, 3, 10]
            assert response["choices"][0]["message"]["content"] == "fake upstream"
        asyncio.run(run())
        assert len(received) == 1 and received[0]["model"] == "qwen3-32b"
        # LiteLLM omits stream=false; the OpenAI-compatible default is non-streaming.
        assert received[0].get("stream", False) is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
