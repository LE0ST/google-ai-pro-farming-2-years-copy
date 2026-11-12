"""Durable submission intents. Unknown outcomes must never be retried automatically."""
import json
import os
import re
from pathlib import Path

from ..config import settings


def path_for(account_id: str, layer: int) -> Path:
    if not re.fullmatch(r"acc_[a-zA-Z0-9_]+", account_id) or layer not in (1, 2, 3, 4):
        raise ValueError("invalid operation key")
    return settings.artifacts_dir / f"operation_{account_id}_L{layer}.json"


def read(account_id: str, layer: int):
    path = path_for(account_id, layer)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if value.get("phase") not in {"submitted", "pending_verification", "completed"}:
        raise ValueError("invalid operation checkpoint; manual review required")
    return value["phase"]


def claim(account_id: str, layer: int) -> bool:
    """Persist intent before submission. Even a crash before the click requires review."""
    path = path_for(account_id, layer)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump({"phase": "submitted"}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    return True


def mark(account_id: str, layer: int, phase: str):
    current = read(account_id, layer)
    if (current, phase) not in {("submitted", "pending_verification"), ("pending_verification", "completed")}:
        raise ValueError("invalid operation transition")
    path = path_for(account_id, layer)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump({"phase": phase}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def summary(account_id: str) -> dict:
    phases = {layer: read(account_id, layer) for layer in (1, 2, 3, 4)}
    pending = [layer for layer, phase in phases.items() if phase in {"submitted", "pending_verification"}]
    return {"verified_layers": [layer for layer, phase in phases.items() if phase == "completed"],
            "pending_operation": bool(pending),
            "pending_layers": pending, "phase": phases[pending[0]] if pending else "none",
            "blocked_operations": ["subscription", "cancellation", "resubmission"] if pending else [],
            "reason": "Previous submission requires independent confirmation; automatic retry is blocked" if pending else ""}
