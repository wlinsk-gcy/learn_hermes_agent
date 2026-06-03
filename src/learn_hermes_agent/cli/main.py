from __future__ import annotations

import argparse
import json
import platform
import sys

from learn_hermes_agent import __version__
from learn_hermes_agent.agent.core import AIAgent
from learn_hermes_agent.config import get_app_home, get_config_path, load_config
from learn_hermes_agent.model_tools import get_tool_definitions, handle_function_call
from learn_hermes_agent.providers.fake import FakeProviderTransport


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
    # uv run learn-hermes-agent chat "hello"
    chat_parser = subparsers.add_parser(
        "chat",
        help="Run one chat turn through the agent.",
    )
    chat_parser.add_argument(
        "message",
        help="User message to send to the agent.",
    )
    # uv run learn-hermes-agent tools
    subparsers.add_parser(
        "tools",
        help="List available tool definitions."
    )
    # uv run learn-hermes-agent call-tool echo '{\"text\":\"hello\"}'
    call_tool_parser = subparsers.add_parser(
        "call-tool",
        help="Call one tool manually with JSON arguments."
    )
    call_tool_parser.add_argument(
        "name",
        help="Tool name.",
    )
    call_tool_parser.add_argument(
        "arguments",
        nargs="?", # 指这个位置参数可传可不传，最多传一个。如果没有nargs="?"的话，那么arguments位置就是必填的，否则会报错
        default="{}",
        help="Tool arguments as a JSON object.",
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


def run_chat(message: str) -> int:
    config = load_config()
    provider = FakeProviderTransport(model=config["model"]["default"])
    agent = AIAgent(
        provider=provider,
        max_iterations=config["agent"]["max_iterations"],
    )

    messages = agent.run_conversation(message)
    final_message = messages[-1]

    print(f"assistant: {final_message['content']}")
    return 0


def run_tools() -> int:
    definitions = get_tool_definitions()
    # indent=2 表示：把 JSON 格式化成多行，并且每一层缩进 2 个空格。
    # 没有 indent 时，输出会挤在一行：[{"type":"function","function":{"name":"echo"}}]
    # 有indent时，就是展开的json，美化了
    # ---
    # ensure_ascii=False 表示：不要把非 ASCII 字符转义成 \uXXXX。
    # 例如：{"description": "返回文本"}，ensure_ascii=True的话，有可能输出：{"description": "\u8fd4\u56de\u6587\u672c"}
    print(json.dumps(definitions, indent=2, ensure_ascii=False))
    return 0


def run_call_tool(name: str, arguments: str) -> int:
    try:
        result_json = handle_function_call(name, arguments)
    except (KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(result_json)
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

    if args.command == "chat":
        return run_chat(args.message)

    if args.command == "tools":
        return run_tools()

    if args.command == "call-tool":
        return run_call_tool(args.name, args.arguments)

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
