from __future__ import annotations

import argparse
import json
import platform
import sys

from learn_hermes_agent import __version__
from learn_hermes_agent.agent.core import AIAgent
from learn_hermes_agent.agent.messages import ChatMessage
from learn_hermes_agent.agent.prompt_builder import PromptBuilder
from learn_hermes_agent.agent.context_compressor import CompressionConfig, ContextCompressor
from learn_hermes_agent.cli.commands import format_help_lines, parse_slash_command
from learn_hermes_agent.config import get_app_home, get_config_path, get_state_db_path, load_config
from learn_hermes_agent.model_tools import get_tool_definitions, handle_function_call
from learn_hermes_agent.providers.fake import FakeProviderTransport, tool_demo_provider
from learn_hermes_agent.state.session_db import SessionStore



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
        nargs="?",
        help="User message to send to the agent. If omitted, starts interactive chat.",
    )
    chat_parser.add_argument(
        "--tool-demo",
        action="store_true",
        help="Use a fake scripted provider that calls echo before returning a final answer.",
    )
    chat_parser.add_argument(
        "--show-messages",
        action="store_true",
        help="Print the full conversation messages as JSON.",
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
        nargs="?",  # 指这个位置参数可传可不传，最多传一个。如果没有nargs="?"的话，那么arguments位置就是必填的，否则会报错
        default="{}",
        help="Tool arguments as a JSON object.",
    )

    subparsers.add_parser(
        "sessions",
        help="List persisted chat sessions.",
    )
    show_session_parser = subparsers.add_parser(
        "show-session",
        help="Show persisted messages for one session."
    )
    show_session_parser.add_argument(
        "session_id",
        help="Session id to inspect.",
    )

    return parser


def get_session_store() -> SessionStore:
    store = SessionStore(get_state_db_path())
    store.initialize()
    return store


def build_system_prompt() -> str:
    return PromptBuilder().build()

def build_context_compressor(config: dict) -> ContextCompressor:
    compression = config["compression"]
    compression_config = CompressionConfig(
        enabled=compression["enabled"],
        context_length=compression["context_length"],
        threshold=compression["threshold"],
        protect_first_n=compression["protect_first_n"],
        protect_last_n=compression["protect_last_n"],
    )
    return ContextCompressor(compression_config)

def build_agent(config: dict, *, tool_demo: bool = False) -> AIAgent:
    if tool_demo:
        provider = tool_demo_provider(model=config["model"]["default"])
    else:
        provider = FakeProviderTransport(model=config["model"]["default"])

    return AIAgent(provider=provider, max_iterations=config["agent"]["max_iterations"], context_compressor=build_context_compressor(config))



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
    compression = config["compression"]
    print(f"compression_enabled: {compression['enabled']}")
    print(f"compression_context_length: {compression['context_length']}")
    print(f"compression_threshold: {compression['threshold']}")
    print(f"compression_protect_first_n: {compression['protect_first_n']}")
    print(f"compression_protect_last_n: {compression['protect_last_n']}")
    return 0


def run_chat(message: str | None, *, tool_demo: bool = False, show_messages: bool = False) -> int:
    if message is None:
        return run_interactive_chat(tool_demo=tool_demo, show_messages=show_messages)

    config = load_config()
    agent = build_agent(config, tool_demo=tool_demo)


    system_prompt = build_system_prompt()
    store = get_session_store()
    session_id = store.create_session(title=message[:80], system_prompt=system_prompt)

    messages = agent.run_conversation(message, system_prompt=system_prompt)
    store.append_messages(session_id, messages)

    if agent.last_context_compressed:
        print("context compressed: yes")


    if show_messages:
        print(json.dumps(messages, indent=2, ensure_ascii=False))
        print(f"session_id: {session_id}")
        return 0

    final_message = messages[-1]
    print(f"assistant: {final_message['content']}")
    print(f"session_id: {session_id}")
    return 0


def run_interactive_chat(*, tool_demo: bool = False, show_messages: bool = False) -> int:
    """启动连续对话"""
    config = load_config()
    store = get_session_store()

    system_prompt = build_system_prompt()
    session_id = store.create_session(title="interactive chat", system_prompt=system_prompt)
    agent = build_agent(config, tool_demo=tool_demo)
    history: list[ChatMessage] = []

    print("learn-hermes-agent interactive chat")
    print("Type /help for commands, /exit to quit.")
    print(f"session_id: {session_id}")

    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not user_input:
            continue

        if user_input.startswith("/"):
            command, raw_name, command_args = parse_slash_command(user_input)

            if command is None:
                if raw_name:
                    print(f"unknown command: /{raw_name}")
                    print("Type /help for available commands.")
                else:
                    print("error: empty command")
                continue

            if command.name == "help":
                print("\n".join(format_help_lines()))
                continue

            if command.name == "exit":
                return 0

            if command.name == "new":
                title = command_args or "interactive chat"
                system_prompt = build_system_prompt()
                session_id = store.create_session(title=title, system_prompt=system_prompt)
                agent = build_agent(config, tool_demo=tool_demo)
                history = []
                print(f"session_id: {session_id}")
                continue

            if command.name == "sessions":
                print(json.dumps(store.list_sessions(), indent=2, ensure_ascii=False))
                continue

            if command.name == "model":
                print(f"provider: {config['model']['provider']}")
                print(f"model: {config['model']['default']}")
                continue

            if command.name == "tools":
                print(json.dumps(get_tool_definitions(), indent=2, ensure_ascii=False))
                continue

            print(f"command not implemented: /{command.name}")
            continue


        before_count = len(history)
        messages = agent.run_conversation(
            user_input,
            history=history,
            system_prompt=system_prompt,
        )
        if agent.last_context_compressed:
            # 触发压缩时，当前 session 的消息整体替换成压缩后的 working messages，避免旧消息重复或切片为空。
            store.replace_messages(session_id, messages)
            new_messages = messages
        else:
            # 没压缩的时候，直接追加messages即可
            new_messages = messages[before_count:]
            store.append_messages(session_id, new_messages)

        history = messages

        if agent.last_context_compressed:
            print("context compressed: yes")

        if show_messages:
            print(json.dumps(new_messages, indent=2,
                             ensure_ascii=False))

        final_message = messages[-1]
        print(f"assistant: {final_message['content']}")




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


def run_sessions() -> int:
    store = get_session_store()
    sessions = store.list_sessions()
    print(json.dumps(sessions, indent=2, ensure_ascii=False))
    return 0


def run_show_session(session_id: str) -> int:
    store = get_session_store()
    messages = store.get_session_messages(session_id)
    print(json.dumps(messages, indent=2, ensure_ascii=False))
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
        return run_chat(
            args.message,
            tool_demo=args.tool_demo,
            show_messages=args.show_messages,
        )

    if args.command == "tools":
        return run_tools()

    if args.command == "call-tool":
        return run_call_tool(args.name, args.arguments)

    if args.command == "sessions":
        return run_sessions()

    if args.command == "show-session":
        return run_show_session(args.session_id)

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
