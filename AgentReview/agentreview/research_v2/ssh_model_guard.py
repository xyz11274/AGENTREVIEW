"""Persistent Linux model watch for the SSH CoCo host; no model or API calls."""
import hashlib
import json
from pathlib import Path
import socket
import os


class ModelGuard:
    def __init__(self, models, watch_factory):
        self.models, self.watches, self.changed = models, {}, set()
        for role, model in models.items():
            root = Path(model["root"])
            self.watches[role] = watch_factory(root)
            actual = {str(p.relative_to(root)) for p in root.rglob("*")
                      if p.is_file() and ".cache" not in p.relative_to(root).parts}
            if actual != {row["relative_path"] for row in model["files"]}:
                raise ValueError("Model inventory differs before watcher startup")
            for row in model["files"]:
                digest = hashlib.sha256()
                with (root / row["relative_path"]).open("rb") as stream:
                    while block := stream.read(4 * 1024 * 1024):
                        digest.update(block)
                if digest.hexdigest() != row["sha256"]:
                    raise ValueError("Model bytes differ before watcher startup")
            self.verify(role)

    def verify(self, role):
        if self.watches[role].changed():
            self.changed.add(role)
        if role in self.changed:
            raise ValueError("Remote model modified after byte verification")
        payload = json.dumps(self.models[role]["files"], ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode()
        return {"status": "verified", "inventory_sha256": hashlib.sha256(payload).hexdigest()}


def serve(config_path, socket_path):
    from model_watch import ModelWatch
    config = json.loads(Path(config_path).read_text())
    guard = ModelGuard(config["models"], ModelWatch)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(socket_path)
        os.chmod(socket_path, 0o600)
        server.listen(8)
        while True:
            client, _ = server.accept()
            with client, client.makefile("rwb") as stream:
                try:
                    role = json.loads(stream.readline())["role"]
                    result = guard.verify(role)
                except Exception as exc:
                    result = {"status": "failed", "error": str(exc)}
                stream.write(json.dumps(result).encode() + b"\n")
                stream.flush()


if __name__ == "__main__":
    import sys
    serve(sys.argv[1], sys.argv[2])
