import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from loom_model_admin import _discover


@pytest.fixture
def endpoint():
    state = {"status": 200, "body": {"data": [{"id": "workbuddy/example"}, {"id": "workbuddy/example"}]}, "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["requests"].append((self.path, self.headers.get("Authorization")))
            self.send_response(state["status"])
            if state["status"] == 302:
                self.send_header("Location", "/redirected")
            self.end_headers()
            self.wfile.write(json.dumps(state["body"]).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1", state
    server.shutdown()
    server.server_close()
    thread.join()


def test_discover_deduplicates_and_does_not_return_credentials(endpoint):
    url, state = endpoint
    result = _discover({"baseUrl": url + "/", "apiKey": "test-secret"})
    assert result["models"] == ["workbuddy/example"]
    assert state["requests"] == [("/v1/models", "Bearer test-secret")]
    assert "test-secret" not in json.dumps(result)


@pytest.mark.parametrize("status", [401, 302, 500])
def test_errors_do_not_echo_upstream_body_or_follow_redirects(endpoint, status):
    url, state = endpoint
    state.update(status=status, body={"error": "test-secret"})
    with pytest.raises(RuntimeError, match=f"HTTP {status}") as error:
        _discover({"baseUrl": url, "apiKey": "test-secret"})
    assert "test-secret" not in str(error.value)
    assert len(state["requests"]) == 1


def test_empty_catalog_is_valid_but_wrong_shape_is_not(endpoint):
    url, state = endpoint
    state["body"] = {"data": []}
    assert _discover({"baseUrl": url, "apiKey": "test"})["models"] == []
    state["body"] = {"message": "welcome"}
    with pytest.raises(ValueError, match="catalog"):
        _discover({"baseUrl": url, "apiKey": "test"})


@pytest.mark.parametrize("url", ["file:///tmp", "http://u:password@localhost/v1", "http://localhost/v1?key=secret"])
def test_rejects_invalid_endpoint(url):
    with pytest.raises(ValueError):
        _discover({"baseUrl": url, "apiKey": "test"})
