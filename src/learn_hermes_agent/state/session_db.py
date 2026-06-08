from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from learn_hermes_agent.agent.messages import ChatMessage


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SessionStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def initialize(self) -> None:
        # parents=True：如果中间目录不存在，也一起创建。
        # exist_ok=True：如果目录已经存在，不报错。
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with self._connect() as conn:
            # PRAGMA 是sqlite的配置命令
            # journal_mode=WAL的意思是启用 Write-Ahead Logging。更适合持续追加和并发读取。
            # 因为：默认 SQLite 写数据时，可能会用一个回滚日志文件来保护事务。
            # WAL模式是：先把变更写到 .db-wal 文件，再由 SQLite 逐步合并回主 .db 文件
            # 好处就是读写并发更好。更适合CLI / gateway / 多入口为未来共享session DB
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                create table if not exists sessions (
                    id text primary key,
                    title text,
                    system_prompt text,
                    parent_session_id text,
                    end_reason text,
                    ended_at text,
                    created_at text not null,
                    updated_at text not null,
                    foreign key (parent_session_id) references sessions(id)
                )
                """
            )
            self._ensure_column(conn, "sessions", "system_prompt", "text")
            self._ensure_column(conn, "sessions", "parent_session_id", "text")
            self._ensure_column(conn, "sessions", "end_reason", "text")
            self._ensure_column(conn, "sessions", "ended_at", "text")
            conn.execute(
                """
                create table if not exists messages (
                    id integer primary key autoincrement,
                    session_id text not null,
                    role text not null, 
                    content text,
                    name text,
                    tool_call_id text,
                    tool_calls_json text,
                    raw_json text not null,
                    created_at text not null,
                    foreign key (session_id) references sessions(id) on delete cascade
                )
                """
            )
            conn.execute(
                """
                create index if not exists idx_messages_session_id_id
                    on messages(session_id, id)
                """
            )
            conn.execute(
                """
                create index if not exists idx_sessions_updated_at
                    on sessions(updated_at)
                """
            )
            conn.execute(
                """
                create index if not exists idx_sessions_parent_session_id
                    on sessions(parent_session_id)
                """
            )

    def create_session(self, *, title: str | None = None,system_prompt: str | None = None, parent_session_id: str | None = None) -> str:
        session_id = str(uuid.uuid4())
        now = utc_now_iso()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (id, title, system_prompt, parent_session_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, title, system_prompt, parent_session_id, now, now),
            )

        return session_id

    def end_session(self, session_id: str, reason: str):
        now = utc_now_iso()

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE sessions
                SET ended_at = ?,
                    end_reason = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, reason, now, session_id),
            )

    def append_message(self, session_id: str, message: ChatMessage) -> int:
        now = utc_now_iso()
        role = str(message.get("role") or "")
        content = message.get("content")
        name = message.get("name")
        tool_call_id = message.get("tool_call_id")

        tool_calls = message.get("tool_calls")
        tool_calls_json = (
            json.dumps(tool_calls, ensure_ascii=False)
            if tool_calls is not None
            else None
        )
        raw_json = json.dumps(message, ensure_ascii=False)

        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO messages (session_id,
                                      role,
                                      content,
                                      name,
                                      tool_call_id,
                                      tool_calls_json,
                                      raw_json,
                                      created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    role,
                    content,
                    name,
                    tool_call_id,
                    tool_calls_json,
                    raw_json,
                    now,
                ),
            )
            conn.execute(
                """
                UPDATE sessions
                SET updated_at = ?
                WHERE id = ?
                """,
                (now, session_id),
            )
            return int(cursor.lastrowid)

    def append_messages(self, session_id: str, messages: list[ChatMessage]) -> None:
        for message in messages:
            self.append_message(session_id, message)


    def replace_messages(self, session_id: str, messages: list[ChatMessage]):
        now = utc_now_iso()

        with self._connect() as conn:
            conn.execute(
                """
                delete from messages where session_id = ?
                """,
                (session_id,),
            )
            for message in messages:
                role = str(message.get("role") or "")
                content = message.get("content")
                name = message.get("name")
                tool_call_id = message.get("tool_call_id")

                tool_calls = message.get("tool_calls")
                tool_calls_json = json.dumps(tool_calls, ensure_ascii=False) if tool_calls is not None else None
                raw_json = json.dumps(message, ensure_ascii=False)

                conn.execute(
                    """
                     INSERT INTO messages (
                        session_id,
                        role,
                        content,
                        name,
                        tool_call_id,
                        tool_calls_json,
                        raw_json,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        role,
                        content,
                        name,
                        tool_call_id,
                        tool_calls_json,
                        raw_json,
                        now,
                    ),
                )

            conn.execute(
                """
                UPDATE sessions
                SET updated_at = ?
                WHERE id = ?
                """,
                (now, session_id),
            )

    def get_session_messages(self, session_id: str) -> list[ChatMessage]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select raw_json
                from messages
                where session_id = ?
                order by id asc
                """,
                (session_id,),
            ).fetchall()

        result: list[ChatMessage] = []
        for row in rows:
            decoded = json.loads(row["raw_json"])
            if isinstance(decoded, dict):
                result.append(decoded)
        return result

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                select id, title, system_prompt, parent_session_id, end_reason, ended_at, created_at, updated_at
                from sessions
                where id = ?
                """,
                (session_id,),
            ).fetchone()

        if row is None:
            return None

        return dict(row)

    def list_sessions(self) -> list[dict[str, Any]]:
        with (self._connect() as conn):
            rows = conn.execute(
                """
                select sessions.id,
                       sessions.title,
                       sessions.parent_session_id,
                       sessions.end_reason,
                       sessions.ended_at,
                       sessions.created_at,
                       sessions.updated_at,
                       count(messages.id) as message_count
                from sessions
                         left join messages on messages.session_id = sessions.id
                group by sessions.id
                order by sessions.updated_at desc
                """
            ).fetchall()

        return [dict(row) for row in rows]

    def _ensure_column(self, conn: sqlite3.Connection, table_name: str, column_name: str,
                       column_definition: str) -> None:
        """SQL迁移Helper"""
        rows = conn.execute(f"pragma table_info({table_name})").fetchall()
        existing_columns = {row["name"] for row in rows}
        if column_name not in existing_columns:
            conn.execute(
                f"alter table {table_name} add column {column_name} {column_definition}"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # 让查询结果可以像字典一样按字段名读取。否则，就需要用row[0], row[1]这样取值
        conn.execute("PRAGMA foreign_keys=ON")  # 开启外键，默认不开
        return conn
