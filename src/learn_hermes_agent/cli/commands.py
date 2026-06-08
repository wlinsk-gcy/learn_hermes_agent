"""建立一个slash command的元数据中心， 不执行command，而是定义command能力"""
from __future__ import annotations
from dataclasses import dataclass # 装饰器


@dataclass(frozen=True)
class CommandDef:
    name: str # 指令名，不带斜杠
    description: str
    category: str # 命令分类
    # ... 是类型注解里面的特殊写法，意思是，可以重复任意多个str，如果是tuple[str,] 等价于 tuple[str]，意味着是一个固定长度为1的字符串元组
    aliases: tuple[str, ...] = () # 别名，tuple[str, ...] 表示字符串元组，长度不限。默认空元组。
    args_hint: str = "" # 参数提示。比如 /new [title] 里的 [title]。


# 命令注册表，用tuple主要是为了表达注册表的内容固定，不应该在运行时做修改
COMMAND_REGISTRY: tuple[CommandDef, ...] = (
    CommandDef("help", "Show available commands.", "Info", aliases=("commands",)),
    CommandDef("new", "Start a new chat session.", "Session", aliases=("reset",), args_hint="[title]"),
    CommandDef("sessions", "List persisted chat sessions.", "Session"),
    CommandDef("model", "Show the active provider and model.", "Configuration"),
    CommandDef("tools", "List available tools.", "Tools"),
    CommandDef("exit", "Exit interactive chat.", "Exit", aliases=("quit", "q")),
)


def _build_command_lookup() -> dict[str, CommandDef]:
    """把注册表转换成快速查找表, 别名也可以找到Command"""
    lookup: dict[str, CommandDef] = {}
    for command in COMMAND_REGISTRY:
        lookup[command.name] = command
        for alias in command.aliases:
            lookup[alias] = command
    return lookup

# 模块加载时立刻构建查找表。这样后面解析命令时不需要每次遍历
_COMMAND_LOOKUP = _build_command_lookup()


def resolve_command(name: str) -> CommandDef | None:
    normalized = name.strip().lower().lstrip("/")
    if not normalized:
        return None
    return _COMMAND_LOOKUP.get(normalized)


def parse_slash_command(text: str) -> tuple[CommandDef | None, str, str]:
    """
    解析完整 slash command 文本。返回三项：(command, raw_name, args)
    例如输入：/new phase6 check
    返回： (CommandDef("new", ...), "new", "phase6 check")
    """
    stripped = text.strip()
    if not stripped.startswith("/"):
        raise ValueError("Slash command must start with '/'.")

    body = stripped[1:].strip()
    if not body:
        # 如果只输入斜杠，就返回空结果
        return None, "", ""

    # 按第一个空格切分，例如："new phase6 check"
    # 得到：name = "new" _ = " " args = "phase6 check"
    name, _, args = body.partition(" ")
    command = resolve_command(name)
    return command, name, args.strip()


def format_help_lines() -> list[str]:
    """生成 /help 要显示的多行文本。返回 list，而不是直接 print，是为了让调用方决定怎么输出。"""
    lines = ["Available commands:"]

    # 记录当前输出到哪个分类了。用于分类变化时插入分类标题。
    current_category: str | None = None
    for command in COMMAND_REGISTRY:
        if command.category != current_category:
            # 如果当前命令的分类和上一条不同，说明进入了新分类。
            current_category = command.category
            # 插入空行，让帮助输出更清晰。
            lines.append("")
            lines.append(f"{current_category}:")

        usage = f"/{command.name}"
        if command.args_hint:
            usage = f"{usage} {command.args_hint}"

        aliases = ""
        if command.aliases:
            # 把别名拼成文本。例如：aliases: /quit, /q
            aliases = f" aliases: {', '.join('/' + alias for alias in command.aliases)}"
        # {usage:<22}：左对齐，占 22 个字符宽度。这样描述文本能大致对齐。
        lines.append(f" {usage:<22} {command.description}{aliases}")

    return lines
