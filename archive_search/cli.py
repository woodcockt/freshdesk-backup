from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .config import get_settings
from .db import Database
from .freshdesk import FreshdeskClient
from .redaction import Redactor
from .sync import SyncService
from .typesense_search import TypesenseClient, index_typesense


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="archive_search")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Create or update the Postgres schema.")
    subparsers.add_parser("refresh-search", help="Refresh the indexed search document cache.")

    sync_parser = subparsers.add_parser("sync", help="Sync Freshdesk tickets into Postgres.")
    sync_parser.add_argument("--since", help="Override the stored sync cursor.")
    sync_parser.add_argument("--max-tickets", type=int, help="Stop after N tickets.")

    search_parser = subparsers.add_parser("search", help="Search archived tickets.")
    search_parser.add_argument("query", nargs="?", help="Text query. Omit to list recent filtered tickets.")
    search_parser.add_argument("--product")
    search_parser.add_argument("--tag", action="append", default=[])
    search_parser.add_argument("--status", type=int)
    search_parser.add_argument("--priority", type=int)
    search_parser.add_argument("--created-from")
    search_parser.add_argument("--created-to")
    search_parser.add_argument("--updated-from")
    search_parser.add_argument("--updated-to")
    search_parser.add_argument("--limit", type=int, default=10)
    search_parser.add_argument("--backend", choices=["postgres", "typesense"], default=None)
    search_parser.add_argument("--json", action="store_true", help="Emit JSON rows.")

    typesense_parser = subparsers.add_parser(
        "index-typesense",
        help="Build or refresh the Typesense ticket search index.",
    )
    typesense_parser.add_argument("--batch-size", type=int, default=500)
    typesense_parser.add_argument("--recreate", action="store_true")

    show_parser = subparsers.add_parser("show", help="Show one archived ticket.")
    show_parser.add_argument("ticket_id", type=int)
    show_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    serve_parser = subparsers.add_parser("serve", help="Run the local archive web UI.")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument("--backend", choices=["postgres", "typesense"], default=None)

    args = parser.parse_args(argv)
    settings = get_settings()
    database = Database(settings.database_url)

    if args.command == "init-db":
        database.apply_migrations()
        print("Database schema is ready.")
        return 0

    if args.command == "refresh-search":
        database.refresh_search_documents()
        print("Search documents refreshed.")
        return 0

    if args.command == "sync":
        service = SyncService(
            client=FreshdeskClient(settings.freshdesk_domain, settings.freshdesk_api_key),
            database=database,
            redactor=Redactor(settings.pii_hash_secret),
            default_since=settings.sync_start,
            per_page=settings.freshdesk_per_page,
        )
        result = service.run(since=args.since, max_tickets=args.max_tickets)
        print(
            f"Synced {result.tickets} tickets and {result.conversations} conversations. "
            f"Last cursor: {result.last_updated_at or 'unchanged'}"
        )
        return 0

    if args.command == "search":
        backend = args.backend or settings.search_backend
        if backend == "typesense":
            result = _typesense(settings).search(
                args.query,
                limit=args.limit,
                product=args.product,
                tags=args.tag,
                status=args.status,
                priority=args.priority,
                created_from=args.created_from,
                created_to=args.created_to,
                updated_from=args.updated_from,
                updated_to=args.updated_to,
            )
            rows = result.rows
        else:
            rows = database.search(
                args.query,
                limit=args.limit,
                product=args.product,
                tags=args.tag,
                status=args.status,
                priority=args.priority,
                created_from=args.created_from,
                created_to=args.created_to,
                updated_from=args.updated_from,
                updated_to=args.updated_to,
            )
        if args.json:
            print(json.dumps([_jsonable(row) for row in rows], indent=2, default=str))
        else:
            _print_search_results(rows)
        return 0

    if args.command == "index-typesense":
        imported, failed = index_typesense(
            database,
            _typesense(settings),
            batch_size=args.batch_size,
            recreate=args.recreate,
        )
        print(f"Indexed {imported} tickets into Typesense. Failed: {failed}")
        return 0

    if args.command == "show":
        payload = database.show_ticket(args.ticket_id)
        if not payload:
            print(f"Ticket {args.ticket_id} was not found.", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(_jsonable(payload), indent=2, default=str))
        else:
            _print_ticket(payload)
        return 0

    if args.command == "serve":
        from .web import run_server

        typesense = _typesense(settings) if settings.typesense_api_key else None
        run_server(
            database,
            typesense=typesense,
            default_backend=args.backend or settings.search_backend,
            host=args.host,
            port=args.port,
        )
        return 0

    return 1


def _print_search_results(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("No matching tickets.")
        return
    for row in rows:
        print(
            f"#{row['freshdesk_id']} | status={row['status']} priority={row['priority']} "
            f"| product={row.get('product_label') or '-'} | updated={row.get('updated_at')}"
        )
        print(f"Subject: {row.get('subject') or '(no subject)'}")
        if row.get("tags"):
            print(f"Tags: {', '.join(row['tags'])}")
        if row.get("excerpt"):
            print(f"Excerpt: {row['excerpt']}")
        print()


def _print_ticket(payload: dict[str, Any]) -> None:
    ticket = payload["ticket"]
    print(f"Ticket #{ticket['freshdesk_id']}")
    print(f"Subject: {ticket.get('subject') or '(no subject)'}")
    print(
        f"Status={ticket.get('status')} Priority={ticket.get('priority')} "
        f"Product={ticket.get('product_label') or '-'}"
    )
    print(f"Created={ticket.get('created_at')} Updated={ticket.get('updated_at')}")
    if ticket.get("tags"):
        print(f"Tags: {', '.join(ticket['tags'])}")
    print()
    print(ticket.get("description_text") or "(no description)")

    conversations = payload["conversations"]
    if not conversations:
        return
    print("\nConversations")
    for conversation in conversations:
        visibility = "private" if conversation.get("private") else "public"
        direction = "incoming" if conversation.get("incoming") else "outgoing"
        print(
            f"\n[{conversation.get('created_at')}] "
            f"{visibility}, {direction}, source={conversation.get('source')}"
        )
        print(conversation.get("body_text") or "(empty)")


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _typesense(settings) -> TypesenseClient:
    if not settings.typesense_api_key:
        raise RuntimeError("Missing TYPESENSE_API_KEY in .env")
    return TypesenseClient(
        settings.typesense_url,
        settings.typesense_api_key,
        settings.typesense_collection,
    )
