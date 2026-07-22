from __future__ import annotations

import re

_DESTRUCTIVE_PATTERNS = re.compile(
    r"""(?:^|\s|&&|\|\||;|\x60)(?:
          rm\s|rmdir\s|
          cp\s|install\s|
          mv\s|
          sed\s+-i|
          truncate\s|
          dd\s|
          shred\s|
          git\s+(?:reset|clean|checkout)\s
      )""",
    re.IGNORECASE | re.VERBOSE,
)

_REDIRECT_OVERWRITE = re.compile(
    r"(?<![>&])>(?![>&])|(?:^|\s)&>(?!>)"
)


def is_destructive_command(command: str) -> bool:
    """只判断命令是否可能修改文件，用于决定是否创建 checkpoint。它不负责审批或阻止命令"""
    if not command:
        return False

    if _DESTRUCTIVE_PATTERNS.search(command):
        return True

    return _REDIRECT_OVERWRITE.search(command) is not None
