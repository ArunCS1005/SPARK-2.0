"""Small persistent store for values SPARK generates and must remember (admin API token, OpenID
client secret). Lives in the `spark-state` volume, never in the repository or .env."""

import contextlib
import json
import os
from pathlib import Path
from typing import Any


class State:
    FILENAME = "state.json"

    def __init__(self, directory: Path):
        self.path = Path(directory) / self.FILENAME
        self._data: dict[str, Any] = {}
        if self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8") or "{}")

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.save()

    def delete(self, key: str) -> None:
        if self._data.pop(key, None) is not None:
            self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)
        with contextlib.suppress(OSError):
            os.chmod(self.path, 0o600)
