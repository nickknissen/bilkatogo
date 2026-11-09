import json
import os
import sys
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class TokenStorage(ABC):
    @abstractmethod
    async def load(self) -> dict[str, Any] | None: ...

    @abstractmethod
    async def save(self, data: dict[str, Any]) -> None: ...


class FileTokenStorage(TokenStorage):
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    async def load(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    async def save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            if sys.platform != "win32":
                os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, self._path)
        except OSError:
            os.unlink(tmp_path)
            raise
