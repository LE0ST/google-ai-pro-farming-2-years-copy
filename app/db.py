"""Minimal SQLite store for read-only subscription diagnostics."""

from __future__ import annotations

import stat
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import aiosqlite

from .config import settings

# A distinct version prevents legacy upstream schemas (including v1) from being
# mistaken for the minimal public read-only database.
SCHEMA_VERSION = 100
_DDL = """
CREATE TABLE accounts (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'ready',
    step INTEGER NOT NULL DEFAULT 0,
    step_message TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
"""


@asynccontextmanager
async def _conn() -> AsyncIterator[aiosqlite.Connection]:
    conn = await aiosqlite.connect(settings.db_path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
    finally:
        await conn.close()


async def migrate() -> None:
    """Create a fresh schema and refuse legacy transaction-capable databases."""
    settings.ensure_dirs()
    async with _conn() as conn:
        tables = {
            row["name"]
            for row in await (await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )).fetchall()
        }
        if "schema_meta" not in tables and tables:
            raise RuntimeError(
                "This data directory contains an unversioned database; "
                "choose a new empty GAI_DATA_DIR."
            )
        if "schema_meta" not in tables:
            await conn.execute("CREATE TABLE schema_meta (version INTEGER NOT NULL)")
        row = await (await conn.execute("SELECT version FROM schema_meta LIMIT 1")).fetchone()
        version = int(row["version"]) if row else 0
        if version not in {0, SCHEMA_VERSION}:
            raise RuntimeError(
                "This data directory belongs to another GaiConsole schema; "
                "choose a new empty GAI_DATA_DIR."
            )
        if version == 0:
            await conn.executescript(_DDL)
            await conn.execute("INSERT INTO schema_meta (version) VALUES (?)", (SCHEMA_VERSION,))
        await conn.commit()
    try:
        settings.db_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


async def fetch_all(sql: str, params: tuple[Any, ...] = ()) -> list[aiosqlite.Row]:
    async with _conn() as conn:
        cur = await conn.execute(sql, params)
        return list(await cur.fetchall())


async def fetch_one(sql: str, params: tuple[Any, ...] = ()) -> aiosqlite.Row | None:
    async with _conn() as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchone()


async def execute(sql: str, params: tuple[Any, ...] = ()) -> None:
    async with _conn() as conn:
        await conn.execute(sql, params)
        await conn.commit()
