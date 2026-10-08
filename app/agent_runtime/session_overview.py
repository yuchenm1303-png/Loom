"""Small, stat-validated snapshots for sidebar reads, never runtime resumption."""
from collections import OrderedDict
from copy import deepcopy
import json
import threading


_FIELDS = (
    "session_id", "profile_id", "workspace_dir", "permission_mode", "created_at",
    "updated_at", "status", "current_turn_id", "forked_from_id", "model_selection",
    "model", "model_provider", "model_base_url", "model_vision", "reasoning_kind",
    "reasoning_value", "usage",
)


class SessionOverviewCache:
    def __init__(self, limit=128):
        self.limit = limit
        self._entries = OrderedDict()
        self._lock = threading.RLock()

    def read(self, path):
        stat = path.stat()
        signature = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino)
        with self._lock:
            cached = self._entries.get(path)
            if cached and cached[0] == signature:
                self._entries.move_to_end(path)
                return deepcopy(cached[1])
        payload = json.loads(path.read_text(encoding="utf-8"))
        overview = {key: payload[key] for key in _FIELDS if key in payload}
        overview["messages"] = []
        for message in payload.get("messages", []):
            if message.get("role") != "user":
                continue
            content = message.get("content", "")
            if isinstance(content, list):
                texts = [part.get("text", "") for part in content if part.get("type") == "text"]
                images = sum(part.get("type") == "image" for part in content)
                if images:
                    texts.append(f"[{images} image{'s' if images != 1 else ''} attached]")
                content = "\n".join(texts)
            title = str(content).strip().replace("\n", " ")[:80]
            if title:
                overview["messages"] = [{"role": "user", "content": title}]
                break
        with self._lock:
            self._entries[path] = (signature, overview)
            self._entries.move_to_end(path)
            while len(self._entries) > self.limit:
                self._entries.popitem(last=False)
        return deepcopy(overview)
