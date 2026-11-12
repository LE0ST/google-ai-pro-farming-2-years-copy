"""SQLite store. Schema migrations are idempotent and versioned.

Secrets never land here: password, TOTP seed and card number/CVC are Keychain
handles. Everything that is merely *about* an account is a column.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import aiosqlite

from .config import settings

SCHEMA_VERSION = 2

_DDL = """
CREATE TABLE IF NOT EXISTS accounts (
    id              TEXT PRIMARY KEY,
    email           TEXT NOT NULL UNIQUE,
    secret_ref      TEXT NOT NULL,
    recovery_email  TEXT NOT NULL DEFAULT '',
    totp_ref        TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'pending',
    step            INTEGER NOT NULL DEFAULT 0,
    step_message    TEXT NOT NULL DEFAULT '',
    expiry_date     TEXT NOT NULL DEFAULT '',
    egress_override TEXT NOT NULL DEFAULT '',
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);

"""

# v2: cards as a rotating pool. The account line format is email|password|totp,
# the card line format is number|MM/YY|cvc, so number and CVC live in Keychain
# and only last4/expiry are columns.
_DDL_V2 = """
CREATE TABLE IF NOT EXISTS cards (
    id           TEXT PRIMARY KEY,
    number_ref   TEXT NOT NULL,
    cvc_ref      TEXT NOT NULL,
    last4        TEXT NOT NULL,
    brand        TEXT NOT NULL DEFAULT 'Card',
    exp_month    TEXT NOT NULL,
    exp_year     TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active',
    last_used_at INTEGER NOT NULL DEFAULT 0,
    created_at   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cards_rotation ON cards(status, last_used_at);
"""


def _ts() -> int:
    return int(time.time())


async def connect() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(settings.db_path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.execute("PRAGMA busy_timeout=5000")
    return conn


@asynccontextmanager
async def _conn() -> AsyncIterator[aiosqlite.Connection]:
    """aiosqlite starts its worker thread in connect(); `async with` on the
    connection would start it a second time. Own the close explicitly."""
    conn = await connect()
    try:
        yield conn
    finally:
        await conn.close()


async def migrate() -> None:
    settings.ensure_dirs()
    async with _conn() as conn:
        await conn.execute("CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER NOT NULL)")
        cur = await conn.execute("SELECT version FROM schema_meta")
        row = await cur.fetchone()
        version = row["version"] if row else 0

        if version < 1:
            await conn.executescript(_DDL)
        if version < 2:
            await conn.executescript(_DDL_V2)
            cols = {r["name"] for r in await (await conn.execute("PRAGMA table_info(accounts)")).fetchall()}
            if "card_id" not in cols:
                await conn.execute("ALTER TABLE accounts ADD COLUMN card_id TEXT NOT NULL DEFAULT ''")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_accounts_card ON accounts(card_id)")

        if version < SCHEMA_VERSION:
            if row:
                await conn.execute("UPDATE schema_meta SET version = ?", (SCHEMA_VERSION,))
            else:
                await conn.execute("INSERT INTO schema_meta (version) VALUES (?)", (SCHEMA_VERSION,))
            await conn.commit()

    # Sekali saat boot saja (dulu: setiap connect()). File DB berisi metadata
    # akun; wal/shm ikut turunan mode-nya.
    try:
        settings.db_path.chmod(0o600)
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
