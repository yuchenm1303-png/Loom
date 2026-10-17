"""Loopback-only upstream and real policy server for the Desktop catalog contract."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from services.loom_model_policy.catalog_sync import CatalogSynchronizer
from services.loom_model_policy.policy import PolicyStore
from services.loom_model_policy.server import ModelPolicyApplication, PolicyRequestHandler

phase = [False]


class Upstream(BaseHTTPRequestHandler):
    def do_GET(self):
        data = [{"id": "omen-alpha"}] + ([{"id": "beam-free"}] if phase[0] else [])
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({"data": data}).encode())

    def log_message(self, *args):
        pass


upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
threading.Thread(target=upstream.serve_forever, daemon=True).start()
app = object.__new__(ModelPolicyApplication)
app.store = PolicyStore(Path(sys.argv[1]) / "policy.db")
app.accounts = SimpleNamespace(user_id_for_authorization=lambda auth: 7,
    model_access=lambda auth: {"enabled": True})
sync = CatalogSynchronizer(app.store, environ={"OPENCODE_GO_API_KEY": "fixture-only",
    "LOOM_OPENCODE_GO_BASE_URL": f"http://127.0.0.1:{upstream.server_port}"})
sync.refresh()
original_access = app.access


def access(*args, **kwargs):
    result = original_access(*args, **kwargs)
    if phase[0]:
        # A different provider gets corrupted in transit while Go adds Beam.
        row = next(row for row in result["access"]["catalog"]["models"] if row["group_id"] == "opencode-console")
        row["available"] = "malformed"
    return result


app.access = access


class Handler(PolicyRequestHandler):
    def do_POST(self):
        if self.path == "/fixture/advance":
            phase[0] = True
            sync.refresh()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")
            return
        super().do_POST()

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
server.application = app
print(json.dumps({"port": server.server_port}), flush=True)
server.serve_forever()
