"""Role and department rules for the local control plane."""

from __future__ import annotations

import re
import unicodedata
from typing import Optional, Sequence

ROLES = ("admin", "reader", "member", "hr", "finance", "engineering", "legal", "sales")
DEPARTMENTS = ("public", "hr", "finance", "engineering", "legal", "sales")

ROLE_DEPARTMENT = {
    "hr": "hr",
    "finance": "finance",
    "engineering": "engineering",
    "legal": "legal",
    "sales": "sales",
}

ALIASES = {
    "ik": "hr",
    "genel": "public",
    "teknik": "engineering",
    "finans": "finance",
    "hukuk": "legal",
    "satis": "sales",
    "satış": "sales",
    "pazarlama": "sales",
}


def normalize_role(value: Optional[str]) -> str:
    role = (value or "admin").strip().lower()
    return role if role in ROLES else "reader"


_TR_MAP = str.maketrans({
    "İ": "i", "I": "i", "ı": "i", "Ş": "s", "ş": "s", "Ğ": "g", "ğ": "g",
    "Ü": "u", "ü": "u", "Ö": "o", "ö": "o", "Ç": "c", "ç": "c",
})


def normalize_department(value: Optional[str]) -> str:
    original = (value or "public").strip()
    folded = unicodedata.normalize("NFKD", original.translate(_TR_MAP))
    folded = "".join(char for char in folded if not unicodedata.combining(char)).lower()
    raw = ALIASES.get(folded, folded)
    slug = re.sub(r"[^a-z0-9\-]+", "-", raw).strip("-")[:48]
    return slug or "public"


def effective_department(role: str, requested: Optional[str]) -> str:
    role = normalize_role(role)
    forced = ROLE_DEPARTMENT.get(role)
    if forced:
        return forced
    return normalize_department(requested)


def can_write(role: str) -> bool:
    return normalize_role(role) != "reader"


def can_admin(role: str) -> bool:
    return normalize_role(role) == "admin"


def upload_department(role: str, requested: Optional[str]) -> str:
    role = normalize_role(role)
    if role == "reader":
        raise PermissionError("Readers cannot add knowledge.")
    return effective_department(role, requested)


def visible_departments(role: str, active: Optional[str] = None) -> Optional[Sequence[str]]:
    """None means every department. Otherwise the active department plus Genel."""
    role = normalize_role(role)
    requested = (active or "").strip().lower()
    if role == "admin" and requested in {"", "all", "tumu", "tümü", "*"}:
        return None
    if role == "reader":
        return ("yonetim",)
    department = effective_department(role, active)
    if department in {"all", "tumu"}:
        return None
    return (department,)


def can_see_department(role: str, department: Optional[str], active: Optional[str] = None) -> bool:
    allowed = visible_departments(role, active)
    if allowed is None:
        return True
    return normalize_department(department) in allowed


def can_mutate_document(role: str, department: Optional[str], active: Optional[str] = None) -> bool:
    role = normalize_role(role)
    if role == "reader":
        return False
    if role == "admin":
        return True
    return normalize_department(department) == effective_department(role, active)
