from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path | None = None) -> None:
    env_path = path or ROOT / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    freshdesk_domain: str
    freshdesk_api_key: str
    database_url: str
    pii_hash_secret: str
    sync_start: str = "2015-01-01T00:00:00Z"
    freshdesk_per_page: int = 100


def get_settings() -> Settings:
    load_env()
    domain = os.environ.get("FRESHDESK_DOMAIN", "").strip()
    api_key = os.environ.get("FRESHDESK_API_KEY", "").strip()
    database_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://freshdesk_archive:freshdesk_archive@localhost:5432/freshdesk_archive",
    ).strip()
    pii_hash_secret = os.environ.get("PII_HASH_SECRET", "").strip()
    sync_start = os.environ.get("FRESHDESK_SYNC_START", "2015-01-01T00:00:00Z").strip()
    per_page = int(os.environ.get("FRESHDESK_PER_PAGE", "100"))

    missing = []
    if not domain:
        missing.append("FRESHDESK_DOMAIN")
    if not api_key:
        missing.append("FRESHDESK_API_KEY")
    if not pii_hash_secret or pii_hash_secret.startswith("change_me"):
        missing.append("PII_HASH_SECRET")
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(f"Missing required .env setting(s): {joined}")

    return Settings(
        freshdesk_domain=normalize_domain(domain),
        freshdesk_api_key=api_key,
        database_url=database_url,
        pii_hash_secret=pii_hash_secret,
        sync_start=sync_start,
        freshdesk_per_page=per_page,
    )


def normalize_domain(domain: str) -> str:
    normalized = domain.removeprefix("https://").removeprefix("http://").rstrip("/")
    return normalized

