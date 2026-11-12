"""User-supplied visual evidence; never used to authorize subscription actions."""
import json
import re
from pathlib import Path


def path_for(directory: Path, account_id: str) -> Path:
    if not re.fullmatch(r"acc_[a-zA-Z0-9_]+", account_id):
        raise ValueError("invalid account identifier")
    return directory / f"visual_evidence_{account_id}.json"


def save(directory: Path, account_id: str, months: int) -> None:
    if type(months) is not int or not 1 <= months <= 60:
        raise ValueError("invalid promotional duration")
    path = path_for(directory, account_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"confirmed_promo_months": months, "source": "user_confirmed_visual_evidence"}), encoding="utf-8")
    temporary.replace(path)


def read(directory: Path, account_id: str) -> dict:
    try:
        value = json.loads(path_for(directory, account_id).read_text(encoding="utf-8"))
        months = value.get("confirmed_promo_months")
        if type(months) is int and 1 <= months <= 60 and value.get("source") == "user_confirmed_visual_evidence":
            return {"confirmed_promo_months": months, "source": value["source"]}
    except (OSError, ValueError, TypeError):
        pass
    return {}
