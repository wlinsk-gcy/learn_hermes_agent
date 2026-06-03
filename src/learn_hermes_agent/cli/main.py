from __future__ import annotations

import argparse
import platform
import sys

from learn_hermes_agent import __version__
from learn_hermes_agent.config import get_app_home, get_config_path, load_config


def build_parser() -> argparse.ArgumentParser:
    # --help是ArgumentParser默认加的，因为默认是add_help=True，会自动注册-h，--help
    parser = argparse.ArgumentParser(
        prog="learn-hermes-agent",  # 只是控制 help 里的显示名称，比如：usage: learn-hermes-agent [-h] [--version] {doctor} ...
        description="From-scratch learning reimplementation of Hermes Agent.",
    )
    # 让learn-hermes-agent --version合法
    parser.add_argument(
        "--version",
        action="store_true",  # 这个的意思是：如果用户传了 --version，那么解析后的args.version = True
        help="Show version and exit.",
    )
    # add_subparsers是创建子命令机制。使用时通过args.command进行逻辑判断，例如：args.command == "doctor"
    subparsers = parser.add_subparsers(dest="command")
    # 注册一个doctor的子命令
    subparsers.add_parser(
        "doctor",
        help="Show basic runtime and project configuration information.",
    )

    return parser


def run_doctor() -> int:
    config = load_config()

    print("learn-hermes-agent doctor")
    print(f"version: {__version__}")
    print(f"python: {platform.python_version()}")
    print(f"platform: {platform.platform()}")
    print(f"home: {get_app_home()}")
    print(f"config: {get_config_path()}")
    print(f"provider: {config['model']['provider']}")
    print(f"model: {config['model']['default']}")
    print(f"max_iterations: {config['agent']['max_iterations']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    # argparse 遇到 --help 会直接打印帮助并退出，流程不会往下走
    args = parser.parse_args(argv)

    if args.version:
        print(__version__)
        return 0

    if args.command == "doctor":
        return run_doctor()

    parser.print_help()
    return 0


if __name__ == "__main__":
    """
    测试命令：
    uv run learn-hermes-agent --help
    uv run learn-hermes-agent --version
    uv run learn-hermes-agent doctor
    """
    raise SystemExit(main(sys.argv[1:]))
