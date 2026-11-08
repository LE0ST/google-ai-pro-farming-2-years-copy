"""DTOs. Account secrets are represented by reference, never by value."""

from __future__ import annotations

import re

from pydantic import BaseModel, field_validator

_EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class AccountCreate(BaseModel):
    email: str
    password: str
    recovery_email: str = ""
    totp_secret: str = ""
    egress_override: str = ""

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        if not re.match(_EMAIL, v):
            raise ValueError(f"not an email: {v!r}")
        return v.lower()
