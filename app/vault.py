"""Account and card vault.

Secrets live in Keychain; the database holds only references and metadata. Reads
go through a process-lifetime cache because every `security` call is a
subprocess (~50 ms) and a page refresh would otherwise call it once per row.

Card semantics: a card is reused across accounts, round-robin, until it is
marked dead. `last_used_at` orders the rotation, so the least recently used
active card is always picked next.
"""

from __future__ import annotations

import re
import secrets as _rand
import time
from typing import Any

from . import db, secrets, totp

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CARD_NUMBER = re.compile(r"^\d{12,19}$")
_EXP = re.compile(r"^(\d{1,2})\s*/\s*(\d{2}|\d{4})$")

_cache: dict[str, str] = {}


# ── secret cache ────────────────────────────────────────────────────────────
def secret_put(ref: str, value: str) -> None:
    secrets.put(ref, value)
    _cache[ref] = value


def secret_get(ref: str) -> str:
    if ref in _cache:
        return _cache[ref]
    value = secrets.get(ref)
    _cache[ref] = value
    return value


def secret_drop(ref: str) -> None:
    _cache.pop(ref, None)
    secrets.delete(ref)


# ── parsing ─────────────────────────────────────────────────────────────────
def split_line(line: str) -> list[str]:
    """Accept pipe, comma, semicolon, tab or whitespace as the separator."""
    for sep in ("|", "\t", ";", ","):
        if sep in line:
            return [p.strip() for p in line.split(sep)]
    return [p.strip() for p in re.split(r"\s{2,}|\s", line) if p.strip()]


def parse_account(line: str) -> tuple[str, str, str] | None:
    parts = split_line(line)
    if len(parts) < 2:
        return None
    email, password = parts[0].lower(), parts[1]
    seed = parts[2] if len(parts) > 2 else ""
    if not _EMAIL.match(email) or not password:
        return None
    if seed and len(totp.normalize(seed).rstrip("=")) < 16:
        seed = ""
    return email, password, seed


def parse_card(line: str) -> tuple[str, str, str] | None:
    parts = split_line(line)
    if len(parts) < 2:
        return None
    number = re.sub(r"[^\d]", "", parts[0])
    exp_raw = parts[1]
    cvc = re.sub(r"[^\d]", "", parts[2]) if len(parts) > 2 else ""
    hit = _EXP.match(exp_raw)
    if not _CARD_NUMBER.match(number) or not hit:
        return None
    cvc = cvc or "000"
    month = f"{int(hit.group(1)):02d}"
    year = hit.group(2)
    year = year[2:] if len(year) == 4 else year
    return number, month, year, cvc


def brand_of(number: str) -> str:
    if number.startswith("4"):
        return "Visa"
    if number[:2] in {"51", "52", "53", "54", "55"}:
        return "Mastercard"
    if number[:2] in {"34", "37"}:
        return "Amex"
    return "Card"


# ── accounts ────────────────────────────────────────────────────────────────
async def add_account(email: str, password: str, seed: str) -> tuple[bool, str]:
    email = email.strip().lower()
    if not _EMAIL.match(email) or not password:
        return False, f"baris tidak valid: {email or '(kosong)'}"
    row = await db.fetch_one("SELECT id FROM accounts WHERE email = ?", (email,))
    now = int(time.time())
    if row:
        acc_id = row["id"]
        secret_put(f"{acc_id}:password", password)
        if seed:
            secret_put(f"{acc_id}:totp", totp.normalize(seed))
        await db.execute("UPDATE accounts SET updated_at = ? WHERE id = ?", (now, acc_id))
        return False, f"{email} sudah ada — diperbarui"

    acc_id = "acc_" + _rand.token_hex(6)
    secret_put(f"{acc_id}:password", password)
    if seed:
        secret_put(f"{acc_id}:totp", totp.normalize(seed))
    await db.execute(
        "INSERT INTO accounts (id, email, secret_ref, totp_ref, card_id, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (acc_id, email, f"{acc_id}:password", f"{acc_id}:totp" if seed else "", "", now, now),
    )
    await attach_card(acc_id)
    return True, f"{email} ditambahkan"


async def backfill_cards() -> int:
    """Assign a card to every account that has none. Existing assignments are kept."""
    rows = await db.fetch_all("SELECT id FROM accounts WHERE card_id = '' ORDER BY created_at")
    assigned = 0
    for row in rows:
        if await attach_card(row["id"]):
            assigned += 1
    return assigned


async def import_accounts(blob: str) -> list[str]:
    notes: list[str] = []
    for raw in blob.splitlines():
        line = raw.strip()
        if not line:
            continue
        parsed = parse_account(line)
        if not parsed:
            notes.append(f"dilewati: {line[:48]}")
            continue
        ok, note = await add_account(*parsed)
        notes.append(note)
    return notes


async def delete_account(acc_id: str) -> None:
    for ref in (f"{acc_id}:password", f"{acc_id}:totp"):
        secret_drop(ref)
    await db.execute("DELETE FROM accounts WHERE id = ?", (acc_id,))


# ── cards ───────────────────────────────────────────────────────────────────
async def add_card(number: str, month: str, year: str, cvc: str) -> tuple[bool, str]:
    number = re.sub(r"[^\d]", "", number)
    if not _CARD_NUMBER.match(number):
        return False, "nomor kartu tidak valid"
    row = await db.fetch_one("SELECT id FROM cards WHERE last4 = ? AND exp_month = ? AND exp_year = ?",
                             (number[-4:], month, year))
    if row:
        return False, f"{brand_of(number)}-{number[-4:]} sudah ada"

    card_id = "card_" + _rand.token_hex(6)
    secret_put(f"{card_id}:number", number)
    secret_put(f"{card_id}:cvc", cvc)
    await db.execute(
        "INSERT INTO cards (id, number_ref, cvc_ref, last4, brand, exp_month, exp_year, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (card_id, f"{card_id}:number", f"{card_id}:cvc", number[-4:],
         brand_of(number), month, year, int(time.time())),
    )
    await backfill_cards()
    return True, f"{brand_of(number)}-{number[-4:]} ditambahkan"


async def import_cards(blob: str) -> list[str]:
    notes: list[str] = []
    for raw in blob.splitlines():
        line = raw.strip()
        if not line:
            continue
        parsed = parse_card(line)
        if not parsed:
            notes.append(f"dilewati: {line[:48]}")
            continue
        ok, note = await add_card(*parsed)
        notes.append(note)
    return notes


async def kill_card(card_id: str, dead: bool = True) -> None:
    await db.execute("UPDATE cards SET status = ? WHERE id = ?",
                     ("dead" if dead else "active", card_id))


async def delete_card(card_id: str) -> None:
    for ref in (f"{card_id}:number", f"{card_id}:cvc"):
        secret_drop(ref)
    await db.execute("UPDATE accounts SET card_id = '' WHERE card_id = ?", (card_id,))
    await db.execute("DELETE FROM cards WHERE id = ?", (card_id,))


async def pick_card() -> dict[str, Any] | None:
    """Least recently used active card. Round-robin, so a card that works keeps
    taking accounts until the operator marks it dead."""
    row = await db.fetch_one(
        "SELECT * FROM cards WHERE status = 'active' ORDER BY last_used_at ASC, id ASC LIMIT 1"
    )
    return dict(row) if row else None


async def attach_card(acc_id: str) -> str:
    """Assign a card to an account, keeping an existing assignment if still valid."""
    acc = await db.fetch_one("SELECT card_id FROM accounts WHERE id = ?", (acc_id,))
    if acc and acc["card_id"]:
        still = await db.fetch_one("SELECT status FROM cards WHERE id = ?", (acc["card_id"],))
        if still and still["status"] == "active":
            return acc["card_id"]

    card = await pick_card()
    if not card:
        return ""
    await db.execute("UPDATE cards SET last_used_at = ? WHERE id = ?", (int(time.time()), card["id"]))
    await db.execute("UPDATE accounts SET card_id = ? WHERE id = ?", (card["id"], acc_id))
    return card["id"]


async def card_public(card_id: str) -> str:
    if not card_id:
        return ""
    row = await db.fetch_one("SELECT brand, last4 FROM cards WHERE id = ?", (card_id,))
    if not row:
        return ""
    return f"{row['brand']}-{row['last4']}"


async def account_creds(email: str) -> tuple[str, str, str] | None:
    """Resolve (acc_id, password, totp_seed) for an email.

    Tasks used to rebuild the id from the email local-part
    (`"acc_" + email.split("@")[0]`), but ids are random hex minted in
    `add_account` — so the lookup always missed, Keychain returned "", and the
    flow typed an empty password until it ran out of steps. The id has to come
    from the row, never from the address.
    """
    row = await db.fetch_one(
        "SELECT id, secret_ref, totp_ref FROM accounts WHERE email = ?", (email.strip().lower(),)
    )
    if not row:
        return None
    password = secret_get(row["secret_ref"]) if row["secret_ref"] else ""
    seed = secret_get(row["totp_ref"]) if row["totp_ref"] else ""
    return row["id"], password, seed


# ── views ───────────────────────────────────────────────────────────────────
async def accounts_view() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT a.*, c.brand, c.last4, c.status AS card_status"
        " FROM accounts a LEFT JOIN cards c ON c.id = a.card_id ORDER BY a.created_at"
    )
    out = []
    for row in rows:
        seed = secret_get(row["totp_ref"]) if row["totp_ref"] else ""
        out.append({
            "id": row["id"],
            "email": row["email"],
            "has_secret": bool(row["secret_ref"]),
            "has_totp": bool(seed),
            "code": totp.code(seed) if seed else "",
            "left": totp.seconds_left() if seed else 0,
            "card": f"{row['brand']}-{row['last4']}" if row["brand"] else "",
            "card_status": row["card_status"] or "",
            "status": row["status"],
            "step": row["step"],
            "message": row["step_message"],
        })
    return out


async def cards_view() -> list[dict[str, Any]]:
    rows = await db.fetch_all("SELECT * FROM cards ORDER BY created_at")
    out = []
    for row in rows:
        used = await db.fetch_one("SELECT COUNT(*) AS n FROM accounts WHERE card_id = ?", (row["id"],))
        out.append({
            "id": row["id"],
            "label": f"{row['brand']}-{row['last4']}",
            "exp": f"{row['exp_month']}/{row['exp_year']}",
            "status": row["status"],
            "used": used["n"] if used else 0,
        })
    return out
