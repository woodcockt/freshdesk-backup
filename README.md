# Freshdesk Ticket Archive Search

Local archive for Freshdesk tickets and conversations. It redacts direct identifiers before storage, writes to Dockerized Postgres, and exposes CLI search using Postgres full-text search.

## Setup

1. Put credentials and local database settings in `.env`.
2. Install Python dependencies:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

3. Start Postgres:

```bash
docker compose up -d postgres
```

4. Create the schema:

```bash
python -m archive_search init-db
```

The compose stack maps Postgres to local port `55432` by default, so it will not collide with another local database on `5432`.

## Sync

Backfill from `FRESHDESK_SYNC_START`, then rerun the same command for incremental syncs:

```bash
python -m archive_search sync
```

Useful dry-development options:

```bash
python -m archive_search sync --max-tickets 25
python -m archive_search sync --since 2024-01-01T00:00:00Z
```

## Search

```bash
python -m archive_search search "release issue" --product "SciBiteSearch" --limit 10
python -m archive_search search "urgent" --tag Vocabs --status 2
python -m archive_search show 12345
```

The archive intentionally does not download attachment binaries in v1.

## Tests

```bash
python -m unittest
```

Optional Postgres integration checks can be added later behind a Docker-backed test database.
