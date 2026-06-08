# Freshdesk Ticket Archive Search

Local archive for Freshdesk tickets, conversations, and attachment files. It redacts direct identifiers before storage, writes ticket data to Dockerized Postgres, indexes search documents in Typesense, and serves a local CLI/web UI with Postgres FTS, Typesense keyword search, and Typesense hybrid vector search.

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
.venv/bin/python -m archive_search init-db
```

The compose stack maps Postgres to local port `55432` by default, so it will not collide with another local database on `5432`.

Typesense is optional, but recommended for the web UI search experience:

```bash
docker compose up -d typesense
.venv/bin/python -m archive_search index-typesense --recreate
```

Hybrid search uses a second Typesense collection of redacted ticket chunks with Typesense auto-generated embeddings:

```bash
.venv/bin/python -m archive_search index-typesense-vectors --recreate
```

For a small trial run before indexing the whole archive:

```bash
.venv/bin/python -m archive_search index-typesense-vectors --recreate --max-tickets 100
```

## Keep Current

Run this sequence after each Freshdesk sync while Freshdesk is still available:

```bash
docker compose up -d postgres typesense
.venv/bin/python -m archive_search init-db
.venv/bin/python -m archive_search sync
.venv/bin/python -m archive_search index-typesense
.venv/bin/python -m archive_search index-typesense-vectors
.venv/bin/python -m archive_search download-attachments
```

What each command updates:

- `sync` pulls new/changed tickets and conversations into Postgres and refreshes the Postgres FTS materialized view.
- `index-typesense` upserts the ticket-level Typesense keyword index.
- `index-typesense-vectors` upserts the redacted chunk-level semantic index used by Hybrid RRF search. Re-running it is safe because chunk IDs are stable.
- `download-attachments` refreshes attachment metadata, discovers inline Freshdesk-rendered images from live conversation HTML, and downloads only files that are not already local.

Use `--recreate` on Typesense index commands only when you want to rebuild a collection from scratch:

```bash
.venv/bin/python -m archive_search index-typesense --recreate
.venv/bin/python -m archive_search index-typesense-vectors --recreate
```

If you only need Postgres FTS refreshed after manual database work:

```bash
.venv/bin/python -m archive_search refresh-search
```

## Attachments

Attachment metadata is captured from Freshdesk conversations during sync. To download attachment binaries into the local archive, first apply migrations, then run the attachment downloader:

```bash
.venv/bin/python -m archive_search init-db
.venv/bin/python -m archive_search download-attachments
```

Files are stored under `FRESHDESK_ATTACHMENT_DIR`, which defaults to `data/attachments` and is ignored by git. Ticket detail pages link downloaded attachments through the local web app. Downloading is resumable: by default, the command skips rows where `local_path` is already set. Use `--force` only when you intentionally want to redownload existing files.

Useful small-batch options:

```bash
.venv/bin/python -m archive_search download-attachments --max-attachments 25
.venv/bin/python -m archive_search download-attachments --max-tickets 1000
.venv/bin/python -m archive_search download-attachments --ticket-id 6555
```

`--max-tickets 1000` means the first 1000 archived Freshdesk ticket IDs. To extend a previous batch, increase the number, for example from `--max-tickets 1000` to `--max-tickets 2000`; already-downloaded files are skipped.

## Sync

Backfill from `FRESHDESK_SYNC_START`, then rerun the same command for incremental syncs:

```bash
.venv/bin/python -m archive_search sync
```

Useful dry-development options:

```bash
.venv/bin/python -m archive_search sync --max-tickets 25
.venv/bin/python -m archive_search sync --since 2024-01-01T00:00:00Z
```

## Search

```bash
.venv/bin/python -m archive_search search "release issue" --product "SciBiteSearch" --limit 10
.venv/bin/python -m archive_search search "customer could not log in" --backend hybrid
.venv/bin/python -m archive_search search "urgent" --tag Vocabs --status 2
.venv/bin/python -m archive_search show 12345
```

## Web UI

Run the local browser UI:

```bash
.venv/bin/python -m archive_search serve
```

Then open:

```text
http://127.0.0.1:8000
```

The UI uses the same Postgres full-text search and redacted ticket detail data as the CLI.
If `SEARCH_BACKEND=hybrid` is set in `.env`, the UI defaults to hybrid rank fusion and still lets you switch back to Typesense keyword search or Postgres FTS from the search form.

The archive only downloads attachment binaries when you run `download-attachments`.

## Tests

```bash
.venv/bin/python -m unittest
```

Optional Postgres integration checks can be added later behind a Docker-backed test database.
