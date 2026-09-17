"""Linux mutation notifications for process-local frozen model digest reuse."""
import ctypes
import os
from pathlib import Path


class ModelWatch:
    def __init__(self, directory):
        libc = ctypes.CDLL(None, use_errno=True)
        self.fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "Cannot watch frozen model assets")
        paths = {Path(directory), *Path(directory).rglob("*")}
        paths.update(path.resolve(strict=True) for path in list(paths))
        try:
            for path in sorted(paths):
                # Access/open events do not invalidate; writes, namespace and
                # attribute changes do. Queue overflow also invalidates (drain).
                if libc.inotify_add_watch(self.fd, os.fsencode(path), 0x00000FCE) < 0:
                    raise OSError(ctypes.get_errno(), f"Cannot watch {path}")
        except BaseException:
            self.close()
            raise

    def changed(self):
        changed = False
        while True:
            try:
                if os.read(self.fd, 65536):
                    changed = True
                else:
                    break
            except BlockingIOError:
                break
        return changed

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __del__(self):
        self.close()
