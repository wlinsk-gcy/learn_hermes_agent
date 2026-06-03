from __future__ import annotations

import os
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "model": {
        "provider": "fake",
        # 默认模型
        "default": "fake-basic",
    },
    "agent": {
        # 最大可迭代次数
        "max_iterations": 10,
    },
}


def get_app_home() -> Path:
    raw = os.environ.get("LEARN_HERMES_HOME")
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.cwd() / ".learn_hermes").resolve()


def ensure_app_home() -> Path:
    home = get_app_home()
    home.mkdir(parents=True, exist_ok=True)
    return home


def get_config_path() -> Path:
    return get_app_home() / "config.yaml"


def load_config() -> dict[str, Any]:
    return {
        "model": dict(DEFAULT_CONFIG["model"]),
        "agent": dict(DEFAULT_CONFIG["agent"]),
    }
