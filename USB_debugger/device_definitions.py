"""Runtime device metadata, canonical fingerprints, and persistent discovery cache."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import struct
import tempfile

from protocol_definitions import PROTOCOL_VERSION

KINDS = ("signals", "variables", "pids")
INFO_LAYOUT = struct.Struct("<BBBBHHHBBIHHQB")
PAGE_HEADER = struct.Struct("<BHHH")
CACHE_FORMAT = 1


@dataclass(frozen=True)
class DeviceInfo:
    protocol: int
    firmware: tuple[int, int, int]
    counts: tuple[int, int, int]
    mask_bytes: int
    max_signals: int
    sample_rate: int
    max_command: int
    max_reply: int
    fingerprint: int
    status: int = 0

    @classmethod
    def decode(cls, payload):
        if len(payload) != INFO_LAYOUT.size:
            raise ValueError("Invalid device info length")
        v = INFO_LAYOUT.unpack(payload)
        info = cls(v[0], tuple(v[1:4]), tuple(v[4:7]), *v[7:])
        info.validate()
        return info

    def validate(self):
        if self.protocol != PROTOCOL_VERSION:
            raise ValueError("Incompatible protocol; install matching firmware and plotter")
        if self.status:
            raise ValueError("Driver definitions are invalid (duplicate names)")
        if not 1 <= self.counts[0] <= 256 or any(not 0 <= n <= 256 for n in self.counts):
            raise ValueError("Invalid definition counts")
        if self.mask_bytes != (self.counts[0] + 7) // 8:
            raise ValueError("Invalid signal-mask width")
        if not 1 <= self.max_signals <= min(self.counts[0], 255):
            raise ValueError("Invalid selected-signal limit")
        if not 0 < self.sample_rate <= 1000000:
            raise ValueError("Invalid telemetry rate")
        if not max(3, self.mask_bytes) <= self.max_command <= 65535 or not 256 <= self.max_reply <= 65535:
            raise ValueError("Invalid packet limits")

    def encode(self):
        return INFO_LAYOUT.pack(self.protocol, *self.firmware, *self.counts,
                                self.mask_bytes, self.max_signals, self.sample_rate,
                                self.max_command, self.max_reply, self.fingerprint, self.status)


def parse_rows(kind, text):
    if kind not in range(3):
        raise ValueError("Unknown definition list")
    if text and not text.endswith("\n"):
        raise ValueError("Incomplete definition row")
    result = []
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) != (2 if kind == 2 else 3) or not fields[0].isascii() or not fields[0].isdecimal():
            raise ValueError("Malformed definition row")
        identifier = int(fields[0])
        entry = {"bit" if kind == 0 else "id": identifier, "name": fields[-1]}
        if kind != 2:
            entry["type"] = fields[1]
        result.append(entry)
    return result


@dataclass(frozen=True)
class Definitions:
    signals: list[dict]
    variables: list[dict]
    pids: list[dict]

    def lists(self):
        return (self.signals, self.variables, self.pids)

    def validate(self, info=None):
        for kind, entries in enumerate(self.lists()):
            if len(entries) > 256:
                raise ValueError("Too many definition entries")
            seen = set()
            for index, entry in enumerate(entries):
                key = "bit" if kind == 0 else "id"
                if type(entry.get(key)) is not int or entry[key] != index:
                    raise ValueError("Duplicate or nonsequential definition IDs")
                name = entry.get("name")
                if not isinstance(name, str) or not name or len(name.encode("utf-8")) > 127:
                    raise ValueError("Invalid definition name")
                if any(ord(char) < 33 or ord(char) > 126 for char in name):
                    raise ValueError("Invalid characters in definition name")
                if name in seen:
                    raise ValueError("Duplicate definition name: " + name)
                seen.add(name)
                if kind != 2 and entry.get("type") not in ("f", "i32", "u32"):
                    raise ValueError("Unknown primitive type")
        if not self.signals:
            raise ValueError("Driver must expose at least one signal")
        if info is not None:
            if tuple(map(len, self.lists())) != info.counts:
                raise ValueError("Definition counts do not match device info")
            if self.fingerprint() != info.fingerprint:
                raise ValueError("Definitions fingerprint mismatch")
        return self

    def text(self, kind):
        lines = []
        for entry in self.lists()[kind]:
            identifier = entry["bit" if kind == 0 else "id"]
            fields = [str(identifier)]
            if kind != 2:
                fields.append(entry["type"])
            fields.append(entry["name"])
            lines.append("\t".join(fields) + "\n")
        return "".join(lines)

    def fingerprint(self):
        value = 14695981039346656037
        for kind, label in enumerate(KINDS):
            for byte in (label + "\n" + self.text(kind)).encode("utf-8"):
                value = ((value ^ byte) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
        return value

    def mask_for_names(self, names, max_signals):
        wanted = set(names)
        mask = bytearray((len(self.signals) + 7) // 8)
        selected = 0
        for entry in self.signals:
            if entry["name"] in wanted and selected < max_signals:
                bit = entry["bit"]
                mask[bit // 8] |= 1 << (bit % 8)
                selected += 1
        return bytes(mask)

    def as_dict(self):
        return dict(zip(KINDS, self.lists()))


def cache_directory():
    override = os.environ.get("RAPID_DRIVE_CACHE_DIR")
    if override:
        return Path(override)
    root = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return root / "RapidDrive" / "cache"


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".definitions-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class DefinitionCache:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else cache_directory()

    def path(self, info):
        return self.directory / f"definitions-v{info.protocol}-{info.fingerprint:016x}.json"

    def load(self, info):
        try:
            path = self.path(info)
            if path.stat().st_size > 262144:
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            if data["format"] != CACHE_FORMAT or data["protocol"] != info.protocol:
                return None
            return Definitions(**data["definitions"]).validate(info)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None

    def save(self, info, definitions):
        definitions.validate(info)
        try:
            atomic_json(self.path(info), {"format": CACHE_FORMAT, "protocol": info.protocol,
                                         "definitions": definitions.as_dict()})
        except OSError:
            pass  # A read-only cache must not prevent using the driver.


class SelectionStore:
    """Persist names only; numeric IDs are resolved afresh for each connection."""
    def __init__(self, directory=None):
        self.path = (Path(directory) if directory is not None else cache_directory()) / "selected-signals.json"

    def load(self):
        try:
            if self.path.stat().st_size > 65536:
                return []
            names = json.loads(self.path.read_text(encoding="utf-8"))
            return names if isinstance(names, list) and all(isinstance(n, str) for n in names) else []
        except (OSError, ValueError):
            return []

    def save(self, names):
        try:
            atomic_json(self.path, list(dict.fromkeys(names)))
        except OSError:
            pass
