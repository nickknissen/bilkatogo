import json
import os
from pathlib import Path
from typing import Any

import typer

APP_NAME = "bilkatogo"
CONFIG_DIR_ENV = "BILKATOGO_CONFIG_DIR"


def config_dir() -> Path:
    if override := os.environ.get(CONFIG_DIR_ENV):
        return Path(override)
    return Path(typer.get_app_dir(APP_NAME))


def config_file() -> Path:
    return config_dir() / "config.json"


def session_file() -> Path:
    return config_dir() / "session.json"


def load_config() -> dict[str, Any]:
    try:
        data = json.loads(config_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}
