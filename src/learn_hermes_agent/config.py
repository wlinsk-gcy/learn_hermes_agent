from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from typing import Any

import yaml

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


def get_state_db_path() -> Path:
    return get_app_home() / "state.db"

def read_raw_config() -> dict[str, Any]:
    config_path = get_config_path()
    if not config_path.exists():
        return {}

    try:
        with config_path.open("r", encoding="utf-8") as file:
            # config.yaml不合法时，直接返回空，降级为默认配置
            data = yaml.safe_load(file) or {}
    except Exception as exc:
        print(
            f"warning: failed to parse config file {config_path}: {exc}; using defaults.",
            file=sys.stderr,
        )
        return {}

    if not isinstance(data, dict):
        print(
            f"warning: config file {config_path} must contain a YAML object; using defaults.",
            file=sys.stderr,
        )
        return {}

    return data



def load_config() -> dict[str, Any]:
    config = copy.deepcopy(DEFAULT_CONFIG)
    user_config = read_raw_config()
    merged = _deep_merge(config, user_config)
    return _normalize_config(merged)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)

    return result

def _normalize_config(config: dict[str, Any]) -> dict[str, Any]:
    model_config = config.get("model")
    if not isinstance(model_config, dict):
        model_config = {}

    default_model_config = DEFAULT_CONFIG["model"]
    provider = model_config.get("provider")
    model = model_config.get("default")

    config["model"] = {
        "provider": str(provider).strip() if provider else default_model_config["provider"],
        "default": str(model).strip() if model else default_model_config["default"],
    }

    agent_config = config.get("agent")
    if not isinstance(agent_config, dict):
        agent_config = {}

    max_iterations = agent_config.get("max_iterations")
    if not isinstance(max_iterations, int) or max_iterations <= 0:
        max_iterations = DEFAULT_CONFIG["agent"]["max_iterations"]

    config["agent"] = {
        "max_iterations": max_iterations,
    }

    return config
