"""Runtime device metadata, canonical fingerprints, and persistent discovery cache."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import struct
import tempfile

from protocol_definitions import SUPPORTED_PROTOCOL_VERSIONS

KINDS = ("signals", "variables", "pids", "can_cyclic", "configuration")
LEGACY_INFO_LAYOUT = struct.Struct("<BBBBHHHBBIHHQB")
INFO_LAYOUT = struct.Struct("<BBBBHHHBBIHHQBH")
PAGE_HEADER = struct.Struct("<BHHH")
CACHE_FORMAT = 1


@dataclass(frozen=True)
class DeviceInfo:
    protocol: int
    firmware: tuple[int, int, int]
    counts: tuple[int, ...]
    mask_bytes: int
    max_signals: int
    sample_rate: int
    max_command: int
    max_reply: int
    fingerprint: int
    status: int = 0
    debug_writes: bool = True

    @classmethod
    def decode(cls, payload):
        if not payload or payload[0] not in SUPPORTED_PROTOCOL_VERSIONS:
            raise ValueError("Incompatible protocol; this plotter supports USB protocols 6, 7 and 8")
        layout = LEGACY_INFO_LAYOUT if payload[0] == 6 else INFO_LAYOUT
        if len(payload) != (34 if payload[0] >= 8 else layout.size):
            raise ValueError("Invalid device info length")
        v = layout.unpack(payload[:layout.size])
        counts = tuple(v[4:7]) + ((v[-1],) if v[0] >= 7 else ())
        info = cls(v[0], tuple(v[1:4]), counts, *(v[7:-1] if v[0] >= 7 else v[7:]))
        if payload[0] >= 8:
            from dataclasses import replace
            if payload[33] > 1:
                raise ValueError("Unknown device capabilities")
            info = replace(info, counts=counts + (struct.unpack_from("<H", payload, 31)[0],), debug_writes=bool(payload[33]))
        info.validate()
        return info

    def validate(self):
        if self.protocol not in SUPPORTED_PROTOCOL_VERSIONS:
            raise ValueError("Incompatible protocol; install matching firmware and plotter")
        if self.status:
            raise ValueError("Driver definitions are invalid (invalid or duplicate names)")
        if len(self.counts) != (3 if self.protocol == 6 else 4 if self.protocol == 7 else 5) or not 1 <= self.counts[0] <= 256 or any(not 0 <= n <= 256 for n in self.counts):
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
        base = LEGACY_INFO_LAYOUT.pack(self.protocol, *self.firmware, *self.counts[:3],
                                self.mask_bytes, self.max_signals, self.sample_rate,
                                self.max_command, self.max_reply, self.fingerprint, self.status)
        return base + (struct.pack("<H", self.counts[3]) if self.protocol >= 7 else b"") + (struct.pack("<HB", self.counts[4], self.debug_writes) if self.protocol >= 8 else b"")


def parse_rows(kind, text):
    if kind not in range(len(KINDS)):
        raise ValueError("Unknown definition list")
    if text and not text.endswith("\n"):
        raise ValueError("Incomplete definition row")
    result = []
    for line in text.splitlines():
        fields = line.split("\t")
        if kind == 4:
            if len(fields) != 10:
                raise ValueError("Malformed configuration descriptor")
            result.append(dict(zip(("id", "group_id", "group", "type", "minimum", "maximum", "unit", "states", "name", "label"),
                                   (int(fields[0]), int(fields[1]), *fields[2:7], int(fields[7]), *fields[8:]))))
            continue
        if len(fields) != (2 if kind >= 2 else 3) or not fields[0].isascii() or not fields[0].isdecimal():
            raise ValueError("Malformed definition row")
        identifier = int(fields[0])
        entry = {"bit" if kind == 0 else "id": identifier, "name": fields[-1]}
        if kind < 2:
            entry["type"] = fields[1]
        result.append(entry)
    return result


@dataclass(frozen=True)
class Definitions:
    signals: list[dict]
    variables: list[dict]
    pids: list[dict]
    can_cyclic: list[dict] | None = None
    configuration: list[dict] | None = None

    def lists(self):
        base = (self.signals, self.variables, self.pids)
        result = base if self.can_cyclic is None else base + (self.can_cyclic,)
        return result if self.configuration is None else result + (self.configuration,)

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
                if name != name.strip() or any(ord(char) < (32 if kind == 3 else 33) or ord(char) > 126 for char in name):
                    raise ValueError("Invalid characters in definition name")
                if name in seen:
                    raise ValueError("Duplicate definition name: " + name)
                seen.add(name)
                if kind < 2 and entry.get("type") not in ("f", "i32", "u32"):
                    raise ValueError("Unknown primitive type")
                if kind == 4:
                    from configuration_protocol import validate_descriptor
                    validate_descriptor(entry)
            if kind == 4:
                groups = {}
                for entry in entries:
                    previous = groups.setdefault(entry["group_id"], entry["group"])
                    if previous != entry["group"]:
                        raise ValueError("Inconsistent configuration group names")
                if len(set(groups.values())) != len(groups):
                    raise ValueError("Duplicate configuration group names")
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
            if kind == 4:
                lines.append("\t".join(str(entry[key]) for key in
                    ("id", "group_id", "group", "type", "minimum", "maximum", "unit", "states", "name", "label")) + "\n")
                continue
            identifier = entry["bit" if kind == 0 else "id"]
            fields = [str(identifier)]
            if kind < 2:
                fields.append(entry["type"])
            fields.append(entry["name"])
            lines.append("\t".join(fields) + "\n")
        return "".join(lines)

    def fingerprint(self):
        value = 14695981039346656037
        for kind, label in enumerate(KINDS[:len(self.lists())]):
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
