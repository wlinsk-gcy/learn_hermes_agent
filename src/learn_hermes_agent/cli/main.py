from __future__ import annotations

import os
import argparse
import json
import platform
import sys

from learn_hermes_agent import __version__
from learn_hermes_agent.agent.core import AIAgent
from learn_hermes_agent.agent.messages import ChatMessage
from learn_hermes_agent.agent.prompt_builder import PromptBuilder
from learn_hermes_agent.agent.context_compressor import CompressionConfig, ContextCompressor
from learn_hermes_agent.agent.tool_context import create_tool_execution_context
from learn_hermes_agent.cli.commands import format_help_lines, parse_slash_command
from learn_hermes_agent.config import (
    get_app_home,
    get_config_path,
    get_memory_dir_path,
    get_skills_dir_path,
    get_state_db_path,
    load_config,
    get_checkpoints_dir_path,
)
from learn_hermes_agent.model_tools import get_tool_definitions, handle_function_call
from learn_hermes_agent.providers.runtime import build_provider_transport
from learn_hermes_agent.state.session_db import SessionStore
from learn_hermes_agent.agent.memory_store import MemoryStore
from learn_hermes_agent.agent.skills import SkillLibrary
from learn_hermes_agent.agent.runtime_cwd import (
    clear_session_cwd,
    copy_session_cwd,
)
from learn_hermes_agent.tools.terminal_tool import (
    clear_terminal_environment,
    move_terminal_environment,
)


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
    # metavar是控制 --help 里的参数占位符显示，不影响实际变量名
    # 例如：uv run learn-hermes-agent chat --help 会看到类似：--resume SESSION_ID   Resume an existing session.
    # 如果不写metavar的话，argparse 默认会用参数名的大写形式，通常显示成：--resume RESUME
    # SESSION_ID 只是 help 文案里的占位符名称
    chat_parser.add_argument(
        "--resume",
        metavar="SESSION_ID",
        help="Resume an existing session. Compression parents resume from their latest continuation child.",
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

    skills_parser = subparsers.add_parser(
        "skills",
        help="List available skills.",
    )
    skills_parser.add_argument(
        "--category",
        help="Optional skill category filter.",
    )

    view_skill_parser = subparsers.add_parser(
        "view-skill",
        help="View one skill or a linked file inside it.",
    )
    view_skill_parser.add_argument(
        "name",
        help="Skill name.",
    )
    view_skill_parser.add_argument(
        "file_path",
        nargs="?",
        help="Optional relative file path inside the skill directory.",
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
    memory_store = MemoryStore(get_memory_dir_path())
    skill_library = SkillLibrary(get_skills_dir_path())
    return PromptBuilder(
        memory_store=memory_store,
        skill_library=skill_library,
    ).build()


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


def build_agent(
        config: dict,
        *,
        tool_demo: bool = False
) -> AIAgent:
    provider = build_provider_transport(config, tool_demo=tool_demo)

    return AIAgent(
        provider=provider,
        max_iterations=config["agent"]["max_iterations"],
        context_compressor=build_context_compressor(config),
        checkpoints_enabled=config["checkpoints"]["enabled"],
        checkpoint_max_snapshots=(
            config["checkpoints"]["max_snapshots"]
        ),
    )


def run_doctor() -> int:
    config = load_config()

    print("learn-hermes-agent doctor")
    print(f"version: {__version__}")
    print(f"python: {platform.python_version()}")
    print(f"platform: {platform.platform()}")
    print(f"home: {get_app_home()}")
    print(f"config: {get_config_path()}")
    print(f"checkpoints: {get_checkpoints_dir_path()}")
    model_config = config["model"]
    api_key_env = model_config["api_key_env"]
    api_key_env_status = "set" if os.environ.get(api_key_env) else "missing"

    print(f"provider: {model_config['provider']}")
    print(f"model: {model_config['default']}")
    print(f"base_url: {model_config['base_url']}")
    print(f"api_key_env: {api_key_env}")
    print(f"api_key_env_status: {api_key_env_status}")
    print(f"timeout_seconds: {model_config['timeout_seconds']}")

    fallbacks = model_config.get("fallbacks", [])
    if not isinstance(fallbacks, list):
        fallbacks = []

    print(f"fallback_count: {len(fallbacks)}")

    for index, fallback in enumerate(fallbacks, start=1):
        if not isinstance(fallback, dict):
            continue

        print(f"fallback_{index}_provider: {fallback.get('provider', 'fake')}")
        print(f"fallback_{index}_model: {fallback.get('default', 'fake-basic')}")
        print(f"fallback_{index}_api_key_env: {fallback.get('api_key_env', 'OPENAI_API_KEY')}")

    print(f"max_iterations: {config['agent']['max_iterations']}")
    compression = config["compression"]
    print(f"compression_enabled: {compression['enabled']}")
    print(f"compression_context_length: {compression['context_length']}")
    print(f"compression_threshold: {compression['threshold']}")
    print(f"compression_protect_first_n: {compression['protect_first_n']}")
    print(f"compression_protect_last_n: {compression['protect_last_n']}")

    terminal = config["terminal"]
    print(f"terminal_timeout_seconds: {terminal['timeout_seconds']}")
    print(f"terminal_max_output_chars: {terminal['max_output_chars']}")

    checkpoints = config["checkpoints"]
    print(f"checkpoints_enabled: {checkpoints['enabled']}")
    print(f"checkpoint_max_snapshots: {checkpoints['max_snapshots']}")

    security = config["security"]
    print(f"security_approval_mode: {security['approval_mode']}")
    print(f"security_yolo: {security['yolo']}")
    print(f"security_workspace_root: {security['workspace_root']}")
    return 0


def run_chat(message: str | None, *, tool_demo: bool = False, show_messages: bool = False,
             resume_session_id: str | None = None, ) -> int:
    if resume_session_id is not None and message is not None:
        print("error: --resume currently supports interactive chat only; omit the message argument.", file=sys.stderr)
        return 1
    if message is None:
        return run_interactive_chat(tool_demo=tool_demo, show_messages=show_messages,
                                    resume_session_id=resume_session_id)

    config = load_config()
    try:
        agent = build_agent(config, tool_demo=tool_demo)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    system_prompt = build_system_prompt()
    store = get_session_store()
    session_id = store.create_session(title=message[:80], system_prompt=system_prompt)
    tool_context = create_tool_execution_context(config, session_id=session_id)

    try:
        messages = agent.run_conversation(message, system_prompt=system_prompt, tool_context=tool_context)
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    store.append_messages(session_id, messages)

    if agent.last_context_compressed:
        print("context compressed: yes")

    if show_messages:
        print(json.dumps(messages, indent=2, ensure_ascii=False))
        print(json.dumps(agent.usage_snapshot(), indent=2, ensure_ascii=False))
        print(f"session_id: {session_id}")
        return 0

    final_message = messages[-1]
    print(f"assistant: {final_message['content']}")
    print(f"session_id: {session_id}")
    return 0


def run_interactive_chat(*, tool_demo: bool = False, show_messages: bool = False,
                         resume_session_id: str | None = None) -> int:
    """启动连续对话"""
    config = load_config()
    store = get_session_store()

    if resume_session_id is None:
        system_prompt = build_system_prompt()
        session_id = store.create_session(title="interactive chat", system_prompt=system_prompt)
        history: list[ChatMessage] = []
        resumed_from: str | None = None
    else:
        requested_session = store.get_session(resume_session_id)
        if requested_session is None:
            print(f"error: session not found: {resume_session_id}", file=sys.stderr)
            return 1

        session_id = store.get_compression_tip(resume_session_id)
        session = store.get_session(session_id)
        if session is None:
            print(f"error: resume target not found: {session_id}", file=sys.stderr)
            return 1

        raw_system_prompt = session.get("system_prompt")
        system_prompt = str(raw_system_prompt) if raw_system_prompt else build_system_prompt()
        history = store.get_session_messages(session_id)
        resumed_from = resume_session_id

    try:
        agent = build_agent(config, tool_demo=tool_demo)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("learn-hermes-agent interactive chat")
    print("Type /help for commands, /exit to quit.")
    print(f"session_id: {session_id}")
    if resumed_from is not None:
        print(f"resumed_from: {resumed_from}")

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
                # 这里只清理旧 session key，不清理新 key，也不修改 SessionStore schema。
                title = command_args or "interactive chat"
                system_prompt = build_system_prompt()

                new_session_id = store.create_session(
                    title=title,
                    system_prompt=system_prompt,
                )

                # 必须在覆盖 session_id 前清理，否则会误清理新 session
                clear_terminal_environment(session_id)
                clear_session_cwd(session_id)
                session_id = new_session_id

                agent = build_agent(
                    config,
                    tool_demo=tool_demo,
                )
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
        try:
            # Hermes 的关键设计不是让每个工具各自判断安全，而是在 handler 执行前有统一 preflight。
            # 这里先把 context 贯穿到分发层；因为 Batch 1 不注册 terminal/read_file/write_file/patch，现有工具行为应该保持不变。
            tool_context = create_tool_execution_context(config, session_id=session_id)
            messages = agent.run_conversation(
                user_input,
                history=history,
                system_prompt=system_prompt,
                tool_context=tool_context,
            )
        except (RuntimeError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            continue

        if agent.last_context_compressed:
            # 触发压缩时，结束当前session并创建子session进行延续
            old_session_id = session_id
            store.end_session(old_session_id, "compression")
            session_id = store.create_session(
                title="compressed continuation",
                system_prompt=system_prompt,
                parent_session_id=old_session_id,
            )
            # 先复制再清理，顺序不能乱，这样 continuation 继承原工作目录，同时旧 session key 不继续占用内存。
            copy_session_cwd(
                old_session_id,
                session_id,
            )
            clear_session_cwd(old_session_id)

            store.append_messages(session_id, messages)
            new_messages = messages
            print(f"session_id: {session_id}")

        else:
            # 没压缩的时候，直接追加messages即可
            new_messages = messages[before_count:]
            store.append_messages(session_id, new_messages)

        history = messages

        if agent.last_context_compressed:
            print("context compressed: yes")

        if show_messages:
            print(json.dumps(new_messages, indent=2, ensure_ascii=False))
            print(json.dumps(agent.usage_snapshot(), indent=2, ensure_ascii=False))

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
    config = load_config()
    # Batch 1 已经把安全检查放进 model_tools.handle_function_call(..., context=...) 的 preflight 里。
    # 之前的 call-tool 没传 context，所以手工调用会绕过统一 preflight；现在 CLI 手工验证和 agent tool calling 的安全路径一致。
    tool_context = create_tool_execution_context(config)
    try:
        result_json = handle_function_call(name, arguments, context=tool_context)
    except (KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(result_json)
    return 0


def run_skills(category: str | None = None) -> int:
    library = SkillLibrary(get_skills_dir_path())
    payload = library.list_skills(category)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def run_view_skill(name: str, file_path: str | None = None) -> int:
    library = SkillLibrary(get_skills_dir_path())

    try:
        payload = library.view_skill(name, file_path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def run_sessions() -> int:
    store = get_session_store()
    sessions = store.list_sessions()
    print(json.dumps(sessions, indent=2, ensure_ascii=False))
    return 0


def run_show_session(session_id: str) -> int:
    store = get_session_store()
    session = store.get_session(session_id)
    if session is None:
        print(f"error: session not found: {session_id}", file=sys.stderr)
        return 1

    messages = store.get_session_messages(session_id)
    payload = {
        "session": {
            **session,
            "message_count": len(messages),
        },
        "compression_tip": store.get_compression_tip(session_id),
        "lineage": store.get_session_chain(session_id),
        "messages": messages,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
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
            resume_session_id=args.resume,
        )

    if args.command == "tools":
        return run_tools()

    if args.command == "call-tool":
        return run_call_tool(args.name, args.arguments)

    if args.command == "skills":
        return run_skills(args.category)

    if args.command == "view-skill":
        return run_view_skill(args.name, args.file_path)

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
