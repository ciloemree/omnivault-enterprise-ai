"""Local Privacy Shield — reversible redaction of PII before any LLM call leaves the process.

Matches are replaced with stable tokens like ``[[EMAIL_1]]`` so answers can be
de-tokenized before they reach the UI. Patterns intentionally stay high-precision
to avoid shredding technical content (IPs as hostnames, short numeric IDs, etc.).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Pattern, Tuple


@dataclass
class PatternRule:
    name: str
    pattern: Pattern[str]
    token_prefix: str


RULES: Tuple[PatternRule, ...] = (
    PatternRule(
        "email",
        re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
        "EMAIL",
    ),
    PatternRule(
        "iban",
        re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
        "IBAN",
    ),
    PatternRule(
        "credit_card",
        re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
        "CARD",
    ),
    PatternRule(
        "phone_tr",
        re.compile(r"(?<!\d)(?:\+90|0)?\s*5\d{2}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)"),
        "PHONE",
    ),
    PatternRule(
        "phone_intl",
        re.compile(r"(?<!\d)\+\d{1,3}[\s\-]?\(?\d{1,4}\)?[\s\-]?\d{3,4}[\s\-]?\d{3,4}(?!\d)"),
        "PHONE",
    ),
    PatternRule(
        "tc_kimlik",
        re.compile(r"(?<!\d)\d{11}(?!\d)"),
        "TCKN",
    ),
    PatternRule(
        "ipv4",
        re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"),
        "IP",
    ),
)


def _luhn_ok(digits: str) -> bool:
    total = 0
    reverse = digits[::-1]
    for index, char in enumerate(reverse):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _valid_tckn(value: str) -> bool:
    if len(value) != 11 or value[0] == "0" or not value.isdigit():
        return False
    digits = [int(c) for c in value]
    odd = digits[0] + digits[2] + digits[4] + digits[6] + digits[8]
    even = digits[1] + digits[3] + digits[5] + digits[7]
    check10 = (odd * 7 - even) % 10
    check11 = sum(digits[:10]) % 10
    return digits[9] == check10 and digits[10] == check11


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


@dataclass
class PrivacyShield:
    enabled: bool = False
    mapping: Dict[str, str] = field(default_factory=dict)
    counts: Dict[str, int] = field(default_factory=dict)

    def redact(self, text: str) -> str:
        if not self.enabled or not text:
            return text
        result = text
        for rule in RULES:
            result = self._apply_rule(result, rule)
        return result

    def _apply_rule(self, text: str, rule: PatternRule) -> str:
        def replacer(match: re.Match[str]) -> str:
            raw = match.group(0)
            if rule.name == "credit_card":
                digits = re.sub(r"\D", "", raw)
                if len(digits) < 13 or len(digits) > 19 or not _luhn_ok(digits):
                    return raw
            if rule.name == "tc_kimlik" and not _valid_tckn(raw):
                return raw
            if rule.name == "iban":
                compact = raw.replace(" ", "").upper()
                if len(compact) < 15:
                    return raw
            token_key = f"{rule.token_prefix}:{_fingerprint(raw)}"
            if token_key not in self.mapping:
                self.counts[rule.token_prefix] = self.counts.get(rule.token_prefix, 0) + 1
                token = f"[[{rule.token_prefix}_{self.counts[rule.token_prefix]}]]"
                self.mapping[token_key] = raw
                self.mapping[token] = raw
            else:
                # Reuse existing human-readable token for the same fingerprint.
                for token, value in self.mapping.items():
                    if value == raw and token.startswith("[["):
                        return token
                token = f"[[{rule.token_prefix}_{self.counts[rule.token_prefix]}]]"
            return next(
                (token for token, value in self.mapping.items() if value == raw and token.startswith("[[")),
                f"[[{rule.token_prefix}_{self.counts[rule.token_prefix]}]]",
            )

        return rule.pattern.sub(replacer, text)

    def restore(self, text: str) -> str:
        if not text or not self.mapping:
            return text
        result = text
        # Longest tokens first so nested prefixes never partially replace.
        for token in sorted((k for k in self.mapping if k.startswith("[[")), key=len, reverse=True):
            result = result.replace(token, self.mapping[token])
        return result

    def summary(self) -> Dict[str, int]:
        return dict(self.counts)

    def stats_for_texts(self, texts: Iterable[str]) -> Dict[str, int]:
        probe = PrivacyShield(enabled=True)
        for text in texts:
            probe.redact(text or "")
        return probe.summary()
