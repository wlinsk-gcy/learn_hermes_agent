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
        "base_url": None,
        "api_key_env": None,
        "timeout_seconds": 60.0,
        "fallbacks": [],
    },
    "agent": {
        # 最大可迭代次数
        "max_iterations": 10,
    },
    "compression": {
        "enabled": True,
        "context_length": 2000,
        "threshold": 0.5,
        "protect_first_n": 2,
        "protect_last_n": 6,
    },
    "terminal": {
        "timeout_seconds": 180,  # terminal 默认最多运行 180 秒。
        "max_output_chars": 50_000,  # 最多向上层返回 50000 个字符，避免输出无限增长。
    },
    # checkpoints是文件修改前的快照生成策略，例如write_file和patch。
    "checkpoints": {
        "enabled": False,  # 启用后会先保存 workspace 当前状态，再修改文件。修改出错时，可以用 checkpoint 恢复修改前内容。
        "max_snapshots": 20,  # 每个 workspace 最多保留 20 个快照。
    },
    "security": {
        "approval_mode": "ask",
        "yolo": False,
        "workspace_root": ".",
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


def get_memory_dir_path() -> Path:
    return get_app_home() / "memories"


def get_skills_dir_path() -> Path:
    return get_app_home() / "skills"


def get_checkpoints_dir_path() -> Path:
    return get_app_home() / "checkpoints"


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
    base_url = model_config.get("base_url")
    api_key_env = model_config.get("api_key_env")
    timeout_seconds = model_config.get("timeout_seconds")
    fallbacks = model_config.get("fallbacks")

    if provider:
        provider = str(provider).strip().lower()
    else:
        provider = default_model_config["provider"]

    if model:
        model = str(model).strip()
    else:
        model = default_model_config["default"]

    if base_url:
        base_url = str(base_url).strip()
    else:
        base_url = default_model_config["base_url"]

    if api_key_env:
        api_key_env = str(api_key_env).strip()
    else:
        api_key_env = default_model_config["api_key_env"]

    if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
    ):
        timeout_seconds = default_model_config["timeout_seconds"]

    normalized_fallbacks: list[dict[str, Any]] = []

    if isinstance(fallbacks, list):
        for fallback in fallbacks:
            if not isinstance(fallback, dict):
                continue

            fallback_provider = fallback.get("provider")
            fallback_model = fallback.get("default")
            fallback_base_url = fallback.get("base_url")
            fallback_api_key_env = fallback.get("api_key_env")
            fallback_timeout_seconds = fallback.get("timeout_seconds")

            if fallback_provider:
                fallback_provider = str(fallback_provider).strip().lower()
            else:
                fallback_provider = default_model_config["provider"]

            if fallback_model:
                fallback_model = str(fallback_model).strip()
            else:
                fallback_model = default_model_config["default"]

            if fallback_base_url:
                fallback_base_url = str(fallback_base_url).strip()
            else:
                fallback_base_url = default_model_config["base_url"]

            if fallback_api_key_env:
                fallback_api_key_env = str(fallback_api_key_env).strip()
            else:
                fallback_api_key_env = default_model_config["api_key_env"]

            if (
                    isinstance(fallback_timeout_seconds, bool)
                    or not isinstance(fallback_timeout_seconds, (int, float))
                    or fallback_timeout_seconds <= 0
            ):
                fallback_timeout_seconds = default_model_config["timeout_seconds"]

            normalized_fallbacks.append(
                {
                    "provider": fallback_provider,
                    "default": fallback_model,
                    "base_url": fallback_base_url,
                    "api_key_env": fallback_api_key_env,
                    "timeout_seconds": float(fallback_timeout_seconds),
                }
            )

    config["model"] = {
        "provider": provider,
        "default": model,
        "base_url": base_url,
        "api_key_env": api_key_env,
        "timeout_seconds": float(timeout_seconds),
        "fallbacks": normalized_fallbacks,
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

    compression_config = config.get("compression")
    if not isinstance(compression_config, dict):
        compression_config = {}

    default_compression_config = DEFAULT_CONFIG["compression"]

    enabled = compression_config.get("enabled")
    context_length = compression_config.get("context_length")
    threshold = compression_config.get("threshold")
    protect_first_n = compression_config.get("protect_first_n")
    protect_last_n = compression_config.get("protect_last_n")

    if not isinstance(enabled, bool):
        enabled = default_compression_config["enabled"]

    if not isinstance(context_length, int) or context_length <= 0:
        context_length = default_compression_config["context_length"]

    if not isinstance(threshold, (int, float)) or threshold <= 0 or threshold > 1:
        threshold = default_compression_config["threshold"]

    if not isinstance(protect_first_n, int) or protect_first_n < 0:
        protect_first_n = default_compression_config["protect_first_n"]

    if not isinstance(protect_last_n, int) or protect_last_n < 0:
        protect_last_n = default_compression_config["protect_last_n"]

    config["compression"] = {
        "enabled": enabled,
        "context_length": context_length,
        "threshold": float(threshold),
        "protect_first_n": protect_first_n,
        "protect_last_n": protect_last_n,
    }

    terminal_config = config.get("terminal")
    if not isinstance(terminal_config, dict):
        # 非字典配置回退到默认值
        terminal_config = {}

    default_terminal_config = DEFAULT_CONFIG["terminal"]
    # timeout 只接受 1～600 秒。
    terminal_timeout = terminal_config.get("timeout_seconds")
    if (
            isinstance(terminal_timeout, bool)
            or not isinstance(terminal_timeout, int)
            or terminal_timeout < 1
            or terminal_timeout > 600
    ):
        terminal_timeout = default_terminal_config["timeout_seconds"]

    max_output_chars = terminal_config.get("max_output_chars")
    if (
            # 单独排除 bool，因为 Python 中 bool 是 int 的子类，True 否则会被当成 1
            isinstance(max_output_chars, bool)
            or not isinstance(max_output_chars, int)
            # # 输出上限必须是正整数
            or max_output_chars <= 0
    ):
        max_output_chars = default_terminal_config["max_output_chars"]

    config["terminal"] = {
        "timeout_seconds": terminal_timeout,
        "max_output_chars": max_output_chars,
    }

    checkpoints_config = config.get("checkpoints")
    if isinstance(checkpoints_config, bool):
        checkpoints_config = {"enabled": checkpoints_config}
    elif not isinstance(checkpoints_config, dict):
        checkpoints_config = {}

    default_checkpoints_config = DEFAULT_CONFIG["checkpoints"]

    checkpoints_enabled = checkpoints_config.get("enabled")
    if not isinstance(checkpoints_enabled, bool):
        checkpoints_enabled = default_checkpoints_config["enabled"]

    max_snapshots = checkpoints_config.get("max_snapshots")
    if (
            isinstance(max_snapshots, bool)
            or not isinstance(max_snapshots, int)
            or max_snapshots <= 0
    ):
        max_snapshots = default_checkpoints_config["max_snapshots"]

    config["checkpoints"] = {
        "enabled": checkpoints_enabled,
        "max_snapshots": max_snapshots,
    }

    security_config = config.get("security")
    if not isinstance(security_config, dict):
        security_config = {}

    default_security_config = DEFAULT_CONFIG["security"]

    approval_mode = security_config.get("approval_mode")
    if isinstance(approval_mode, str):
        approval_mode = approval_mode.strip().lower()
    if approval_mode not in {"ask", "auto", "deny"}:
        approval_mode = default_security_config["approval_mode"]

    yolo = security_config.get("yolo")
    if not isinstance(yolo, bool):
        yolo = default_security_config["yolo"]

    workspace_root = security_config.get("workspace_root")
    if workspace_root:
        workspace_root = str(workspace_root).strip()
    else:
        workspace_root = default_security_config["workspace_root"]

    config["security"] = {
        "approval_mode": approval_mode,
        "yolo": yolo,
        "workspace_root": workspace_root,
    }

    return config
