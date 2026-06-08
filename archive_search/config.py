from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ATTACHMENT_DIR = "data/attachments"


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
    typesense_url: str = "http://127.0.0.1:8108"
    typesense_api_key: str = ""
    typesense_collection: str = "freshdesk_tickets"
    typesense_chunk_collection: str = "freshdesk_ticket_chunks"
    typesense_embedding_model: str = "ts/all-MiniLM-L12-v2"
    typesense_vector_alpha: float = 0.35
    typesense_vector_k: int = 200
    typesense_chunk_chars: int = 2000
    typesense_chunk_overlap: int = 200
    attachment_dir: str = DEFAULT_ATTACHMENT_DIR
    search_backend: str = "postgres"


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
    typesense_url = os.environ.get("TYPESENSE_URL", "http://127.0.0.1:8108").strip()
    typesense_api_key = os.environ.get("TYPESENSE_API_KEY", "").strip()
    typesense_collection = os.environ.get("TYPESENSE_COLLECTION", "freshdesk_tickets").strip()
    typesense_chunk_collection = os.environ.get(
        "TYPESENSE_CHUNK_COLLECTION",
        "freshdesk_ticket_chunks",
    ).strip()
    typesense_embedding_model = os.environ.get(
        "TYPESENSE_EMBEDDING_MODEL",
        "ts/all-MiniLM-L12-v2",
    ).strip()
    typesense_vector_alpha = float(os.environ.get("TYPESENSE_VECTOR_ALPHA", "0.35"))
    typesense_vector_k = int(os.environ.get("TYPESENSE_VECTOR_K", "200"))
    typesense_chunk_chars = int(os.environ.get("TYPESENSE_CHUNK_CHARS", "2000"))
    typesense_chunk_overlap = int(os.environ.get("TYPESENSE_CHUNK_OVERLAP", "200"))
    attachment_dir = os.environ.get("FRESHDESK_ATTACHMENT_DIR", DEFAULT_ATTACHMENT_DIR).strip()
    search_backend = os.environ.get("SEARCH_BACKEND", "postgres").strip().lower()

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
        typesense_url=typesense_url,
        typesense_api_key=typesense_api_key,
        typesense_collection=typesense_collection,
        typesense_chunk_collection=typesense_chunk_collection,
        typesense_embedding_model=typesense_embedding_model,
        typesense_vector_alpha=typesense_vector_alpha,
        typesense_vector_k=typesense_vector_k,
        typesense_chunk_chars=typesense_chunk_chars,
        typesense_chunk_overlap=typesense_chunk_overlap,
        attachment_dir=attachment_dir,
        search_backend=search_backend
        if search_backend in {"postgres", "typesense", "hybrid"}
        else "postgres",
    )


def normalize_domain(domain: str) -> str:
    normalized = domain.removeprefix("https://").removeprefix("http://").rstrip("/")
    return normalized
