"""Versioned JSON presets: stable parameter names, every field optional."""
import json
import math
from pathlib import Path
from uuid import uuid4
from config import MOTOR_PRESET_FOLDER, BUNDLED_MOTOR_PRESET_FOLDER
from pid_protocol import GAINS


def validate_preset(data):
    if not isinstance(data, dict) or type(data.get("version")) is not int or data.get("version") != 2:
        raise ValueError("Unsupported motor preset format; expected version 2")
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        raise ValueError("A preset name is required")
    values = data.get("parameters", {})
    if not isinstance(values, dict):
        raise ValueError("Preset parameters must be an object")
    for key, value in values.items():
        if not isinstance(key, str) or not key or len(key) > 200:
            raise ValueError("Invalid parameter name")
        if type(value) not in (int, float, bool) or abs(value) > 1e100 or not math.isfinite(value):
            raise ValueError(f"{key}: a finite numeric value is required")
    pids = data.get("pids", {})
    if not isinstance(pids, dict):
        raise ValueError("Preset pids must be an object")
    for name, gains in pids.items():
        if not isinstance(name, str) or not name or len(name) > 127 or not isinstance(gains, dict):
            raise ValueError("Invalid PID controller name or gains")
        if any(key not in GAINS for key in gains):
            raise ValueError("PID gains must use kp, ki or kd")
        for value in gains.values():
            if type(value) not in (int, float) or abs(value) > 1e100 or not math.isfinite(value):
                raise ValueError(f"{name}: a finite numeric gain is required")
    return dict(data, parameters=values, pids=pids)


class PresetStore:
    def __init__(self, directory=None, bundled_directory=None):
        self.directory = Path(directory if directory is not None else MOTOR_PRESET_FOLDER).expanduser().resolve()
        self.bundled_directory = Path(bundled_directory if bundled_directory is not None else
                                      BUNDLED_MOTOR_PRESET_FOLDER).expanduser().resolve()

    def is_builtin(self, path):
        path = Path(path)
        return (path.absolute().is_relative_to(self.bundled_directory) or
                path.resolve().is_relative_to(self.bundled_directory))

    def can_update(self, path):
        builtin = self.is_builtin(path)
        path = Path(path).resolve()
        return (not builtin and path.parent == self.directory and
                path.suffix.lower() == ".json" and path.is_file())

    def list(self):
        result, errors, seen = [], [], set()
        for directory in (self.bundled_directory, self.directory):
            for path in sorted(directory.glob("*.json")):
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                try:
                    result.append((path, validate_preset(json.loads(path.read_text(encoding="utf-8")))))
                except (OSError, ValueError, TypeError) as exc:
                    errors.append(f"{path}: {exc}")
        return sorted(result, key=lambda item: (not self.is_builtin(item[0]), item[1]["name"].casefold(), item[0].name)), errors

    def save(self, name, parameters=None, pids=None, path=None):
        data = validate_preset({"version": 2, "name": name.strip(), "parameters": dict(parameters or {}), "pids": pids or {}})
        if self.is_builtin(self.directory):
            raise ValueError("The user preset folder must be outside the read-only built-in preset folder")
        if path is None:
            # Generated filenames keep model names out of filesystem paths.
            path = self.directory / (uuid4().hex + ".json")
        else:
            if self.is_builtin(path):
                raise ValueError("Built-in presets are read-only; create a new user preset")
            path = Path(path).resolve()
            if not self.can_update(path):
                raise ValueError("Choose an existing preset in the user preset folder")
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.stem}-{uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return path
