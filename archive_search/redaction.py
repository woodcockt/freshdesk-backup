from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d(). \-]{7,}\d)(?!\w)")
URL_RE = re.compile(r"https?://[^\s<>'\"]+")
SENSITIVE_QUERY_KEYS = {
    "access_token",
    "apikey",
    "api_key",
    "auth",
    "code",
    "key",
    "pass",
    "password",
    "refresh_token",
    "secret",
    "sig",
    "signature",
    "token",
}
SENSITIVE_QUERY_RE = re.compile(
    r"([?&](?:"
    + "|".join(re.escape(key) for key in sorted(SENSITIVE_QUERY_KEYS, key=len, reverse=True))
    + r")=)([^&#\s<>'\"]*)",
    re.IGNORECASE,
)


class Redactor:
    def __init__(self, secret: str) -> None:
        if not secret:
            raise ValueError("A non-empty PII hash secret is required")
        self._secret = secret.encode("utf-8")

    def hash_identifier(self, value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip().lower()
        if not normalized:
            return None
        digest = hmac.new(self._secret, normalized.encode("utf-8"), hashlib.sha256)
        return digest.hexdigest()

    def hash_list(self, values: list[Any] | None) -> list[str]:
        if not values:
            return []
        return [hashed for value in values if (hashed := self.hash_identifier(value))]

    def redact_text(self, value: Any) -> str:
        if value is None:
            return ""
        text = str(value)
        text = URL_RE.sub(self._redact_url, text)
        text = EMAIL_RE.sub("[REDACTED_EMAIL]", text)
        text = PHONE_RE.sub(_redact_phone, text)
        return text

    def redact_json(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self.redact_json(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.redact_json(item) for item in value]
        if isinstance(value, str):
            return self.redact_text(value)
        return value

    def _redact_url(self, match: re.Match[str]) -> str:
        url = match.group(0)
        try:
            parsed = urlsplit(url)
        except ValueError:
            return _redact_sensitive_query_values(url)
        if not parsed.query:
            return url

        changed = False
        redacted_pairs = []
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            if key.lower() in SENSITIVE_QUERY_KEYS:
                redacted_pairs.append((key, "[REDACTED_QUERY]"))
                changed = True
            else:
                redacted_pairs.append((key, value))

        if not changed:
            return url
        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urlencode(redacted_pairs, doseq=True),
                parsed.fragment,
            )
        )


def _redact_sensitive_query_values(url: str) -> str:
    return SENSITIVE_QUERY_RE.sub(
        lambda match: f"{match.group(1)}[REDACTED_QUERY]",
        url,
    )


def _redact_phone(match: re.Match[str]) -> str:
    candidate = match.group(0)
    digit_count = sum(1 for char in candidate if char.isdigit())
    if digit_count < 10:
        return candidate
    return "[REDACTED_PHONE]"
