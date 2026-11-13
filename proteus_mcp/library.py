"""Read the installed Proteus 9 device-library index without desktop input."""

import os
import struct
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .model import CircuitError


DEFAULT_LIBRARY = Path("D:/ProgramData/Labcenter Electronics/Proteus 9 Professional/Library")
_LIBRARY_ROOT = ContextVar('proteus_library_root', default=None)
INDEX_START, RECORD_SIZE = 1352, 276


def default_library():
    return Path(_LIBRARY_ROOT.get() or os.getenv('PROTEUS_MCP_LIBRARY') or DEFAULT_LIBRARY).resolve()


@contextmanager
def library_scope(root):
    token = _LIBRARY_ROOT.set(str(Path(root).resolve()))
    try:
        yield
    finally:
        _LIBRARY_ROOT.reset(token)


def read_index(data):
    if len(data) < INDEX_START or not data.startswith(b"DEVICE LIBRARY\x1a\x00"):
        raise CircuitError("Unknown native device-library signature")
    version, capacity = struct.unpack_from("<II", data, 16)
    count = struct.unpack_from("<H", data, 26)[0]
    if version != 610 or count > capacity or capacity > 100000:
        raise CircuitError("Uncalibrated native library index version or count")
    content_start = INDEX_START + capacity * RECORD_SIZE
    if content_start > len(data):
        raise CircuitError("Truncated native library index")
    results = []
    for index in range(count):
        offset = INDEX_START + index * RECORD_SIZE
        raw = data[offset:offset+128].split(b"\x00", 1)[0]
        try:
            name = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise CircuitError("Invalid UTF-8 device name") from error
        pointer = struct.unpack_from("<I", data, offset+128)[0]
        if not name or any(ord(c) < 32 for c in name) or not content_start <= pointer < len(data):
            raise CircuitError(f"Invalid device index record {index}")
        results.append({"device": name, "object_offset": pointer})
    return results


class LibraryIndex:
    def __init__(self, root=None):
        self.root = Path(root).resolve() if root is not None else default_library()
        self.cache = {}

    def search(self, query, limit=50):
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 128:
            raise CircuitError("query must contain 1..128 characters")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise CircuitError("limit must be within 1..200")
        if not self.root.is_dir():
            raise CircuitError(f"Proteus library directory not found: {self.root}")
        matches, skipped, total = [], [], 0
        files = sorted(self.root.glob("*.LIB"))
        for path in files:
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            if stat.st_size > 128 * 1024 * 1024:
                skipped.append({"library": path.name, "reason": "File exceeds 128 MiB"})
                continue
            if self.cache.get(path, (None,))[0] != signature:
                try:
                    records = read_index(path.read_bytes())
                except CircuitError as error:
                    records = str(error)
                self.cache[path] = (signature, records)
            records = self.cache[path][1]
            if isinstance(records, str):
                skipped.append({"library": path.name, "reason": records})
                continue
            total += len(records)
            matches.extend({**record, "library": path.name, "library_path": str(path)}
                           for record in records if query.strip().casefold() in record["device"].casefold())
        matches.sort(key=lambda r: (r["device"].casefold() != query.strip().casefold(),
                                    r["device"].casefold(), r["library"]))
        return {"source": "installed_native_library_index", "library_root": str(self.root),
                "libraries_scanned": len(files), "devices_indexed": total,
                "total_matches": len(matches), "results": matches[:limit], "skipped": skipped,
                "placement_support": "Index search does not imply native placement or a simulation model"}
