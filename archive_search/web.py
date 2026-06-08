from __future__ import annotations

from datetime import date, datetime
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .db import Database
from .typesense_search import TypesenseClient, TypesenseError


STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_LIMIT = 25
MAX_LIMIT = 100

STATUS_LABELS = {
    2: "Open",
    3: "Pending",
    4: "Resolved",
    5: "Closed",
}
PRIORITY_LABELS = {
    1: "Low",
    2: "Medium",
    3: "High",
    4: "Urgent",
}


def run_server(
    database: Database,
    typesense: TypesenseClient | None = None,
    default_backend: str = "postgres",
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    handler = make_handler(database, typesense, default_backend)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Freshdesk archive web UI running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping web UI.")
    finally:
        server.server_close()


def make_handler(
    database: Database,
    typesense: TypesenseClient | None = None,
    default_backend: str = "postgres",
):
    class ArchiveHandler(BaseHTTPRequestHandler):
        server_version = "FreshdeskArchiveWeb/0.1"

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_html(
                    render_search_page(database, typesense, default_backend, parsed.query)
                )
                return
            if parsed.path.startswith("/ticket/"):
                ticket_id = parsed.path.removeprefix("/ticket/").strip("/")
                self._send_html(render_ticket_page(database, ticket_id, parsed.query))
                return
            if parsed.path == "/static/app.css":
                self._send_static("app.css", "text/css; charset=utf-8")
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_html(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_static(self, filename: str, content_type: str) -> None:
            path = STATIC_DIR / filename
            if not path.exists():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            encoded = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return ArchiveHandler


def render_search_page(
    database: Database,
    typesense: TypesenseClient | None,
    default_backend: str,
    raw_query: str,
) -> str:
    params = _search_params(raw_query, default_backend)
    error = ""
    found = None
    search_ms = None
    if params["backend"] == "typesense" and typesense is not None:
        try:
            result = typesense.search(
                params["query"] or None,
                limit=params["limit"],
                product=params["product"] or None,
                tags=[params["tag"]] if params["tag"] else None,
                status=params["status"],
                priority=params["priority"],
                created_from=params["created_from"] or None,
                created_to=params["created_to"] or None,
                updated_from=params["updated_from"] or None,
                updated_to=params["updated_to"] or None,
            )
            rows = result.rows
            found = result.found
            search_ms = result.search_ms
        except TypesenseError as exc:
            rows = _postgres_search(database, params)
            params["backend"] = "postgres"
            error = f"Typesense unavailable; showing Postgres results. {exc}"
    else:
        rows = _postgres_search(database, params)
        if params["backend"] == "typesense":
            error = "Typesense is not configured; showing Postgres results."
            params["backend"] = "postgres"

    options = database.get_filter_options()
    return _layout(
        "Freshdesk Archive",
        f"""
        <header class="topbar">
          <div>
            <h1>Freshdesk Archive</h1>
            <div class="meta-line">
              {_number(options["summary"]["ticket_count"])} tickets /
              {_number(options["summary"]["conversation_count"])} conversations /
              {_number(options["summary"]["attachment_count"])} attachments
            </div>
          </div>
        </header>
        <main class="shell">
          {render_search_form(params, options)}
          {render_error(error)}
          <section class="results">
            <div class="section-title">
              <h2>{_result_count(len(rows), found)} results</h2>
              <span>{escape(_active_filter_label(params, search_ms))}</span>
            </div>
            {render_results(rows, raw_query)}
          </section>
        </main>
        """,
    )


def render_ticket_page(database: Database, ticket_id: str, raw_query: str) -> str:
    if not ticket_id.isdigit():
        return _layout("Ticket not found", render_not_found(ticket_id))

    payload = database.show_ticket(int(ticket_id))
    if not payload:
        return _layout("Ticket not found", render_not_found(ticket_id))

    ticket = payload["ticket"]
    conversations = payload["conversations"]
    back_url = "/" + (f"?{raw_query}" if raw_query else "")
    return _layout(
        f"Ticket #{ticket['freshdesk_id']}",
        f"""
        <header class="topbar detail-topbar">
          <div>
            <a class="back-link" href="{escape(back_url)}">Back to search</a>
            <h1>Ticket #{ticket['freshdesk_id']}</h1>
            <p class="ticket-subject">{_text(ticket.get("subject") or "(no subject)")}</p>
          </div>
        </header>
        <main class="shell detail-shell">
          <section class="detail-grid">
            <div class="detail-main">
              <section class="panel">
                <h2>Description</h2>
                <div class="body-text">{_multiline(ticket.get("description_text") or "(no description)")}</div>
              </section>
              <section class="panel">
                <div class="section-title">
                  <h2>Conversations</h2>
                  <span>{len(conversations)} entries</span>
                </div>
                {render_conversations(conversations)}
              </section>
            </div>
            <aside class="detail-side">
              {render_ticket_facts(ticket)}
            </aside>
          </section>
        </main>
        """,
    )


def render_search_form(params: dict[str, Any], options: dict[str, Any]) -> str:
    return f"""
    <form class="search-panel" method="get" action="/">
      <div class="query-row">
        <label>
          <span>Keyword</span>
          <input name="q" value="{_text(params['query'])}" placeholder="Subject, description, conversation" autofocus>
        </label>
        <button type="submit">Search</button>
      </div>
      <div class="filters">
        <label>
          <span>Engine</span>
          <select name="backend">
            <option value="typesense" {_selected("typesense", params["backend"])}>Typesense</option>
            <option value="postgres" {_selected("postgres", params["backend"])}>Postgres FTS</option>
          </select>
        </label>
        <label>
          <span>Product</span>
          <select name="product">
            <option value="">Any product</option>
            {render_options(options["products"], params["product"])}
          </select>
        </label>
        <label>
          <span>Tag</span>
          <select name="tag">
            <option value="">Any tag</option>
            {render_options(options["tags"], params["tag"])}
          </select>
        </label>
        <label>
          <span>Status</span>
          <select name="status">
            <option value="">Any status</option>
            {render_numeric_options(options["statuses"], params["status"], STATUS_LABELS)}
          </select>
        </label>
        <label>
          <span>Priority</span>
          <select name="priority">
            <option value="">Any priority</option>
            {render_numeric_options(options["priorities"], params["priority"], PRIORITY_LABELS)}
          </select>
        </label>
        <label>
          <span>Created from</span>
          <input type="date" name="created_from" value="{_text(params['created_from'])}">
        </label>
        <label>
          <span>Created to</span>
          <input type="date" name="created_to" value="{_text(params['created_to'])}">
        </label>
        <label>
          <span>Limit</span>
          <input type="number" min="1" max="{MAX_LIMIT}" name="limit" value="{params['limit']}">
        </label>
      </div>
    </form>
    """


def render_error(message: str) -> str:
    if not message:
        return ""
    return f'<div class="notice">{_text(message)}</div>'


def render_results(rows: list[dict[str, Any]], raw_query: str) -> str:
    if not rows:
        return '<div class="empty-state">No matching tickets.</div>'

    rendered = []
    for row in rows:
        href = _ticket_url(row["freshdesk_id"], raw_query)
        tags = "".join(f'<span class="tag">{_text(tag)}</span>' for tag in row.get("tags") or [])
        rendered.append(
            f"""
            <article class="result-card">
              <div class="result-top">
                <a class="ticket-link" href="{href}">#{row['freshdesk_id']}</a>
                <span>{_status(row.get("status"))}</span>
                <span>{_priority(row.get("priority"))}</span>
                <span>{_date(row.get("updated_at"))}</span>
              </div>
              <h3><a href="{href}">{_text(row.get("subject") or "(no subject)")}</a></h3>
              <div class="result-meta">
                <span>{_text(row.get("product_label") or "No product")}</span>
                {tags}
              </div>
              <p class="excerpt">{_excerpt(row.get("excerpt") or "")}</p>
            </article>
            """
        )
    return "\n".join(rendered)


def render_ticket_facts(ticket: dict[str, Any]) -> str:
    tags = "".join(f'<span class="tag">{_text(tag)}</span>' for tag in ticket.get("tags") or [])
    return f"""
    <section class="panel facts">
      <h2>Details</h2>
      <dl>
        <dt>Status</dt><dd>{_status(ticket.get("status"))}</dd>
        <dt>Priority</dt><dd>{_priority(ticket.get("priority"))}</dd>
        <dt>Product</dt><dd>{_text(ticket.get("product_label") or "-")}</dd>
        <dt>Created</dt><dd>{_date(ticket.get("created_at"))}</dd>
        <dt>Updated</dt><dd>{_date(ticket.get("updated_at"))}</dd>
        <dt>Requester ID</dt><dd>{_text(ticket.get("requester_id") or "-")}</dd>
        <dt>Company ID</dt><dd>{_text(ticket.get("company_id") or "-")}</dd>
      </dl>
      <div class="tag-wrap">{tags or '<span class="muted">No tags</span>'}</div>
    </section>
    """


def render_conversations(conversations: list[dict[str, Any]]) -> str:
    if not conversations:
        return '<div class="empty-state">No conversations archived.</div>'

    rendered = []
    for conversation in conversations:
        visibility = "Private note" if conversation.get("private") else "Public"
        direction = "Incoming" if conversation.get("incoming") else "Outgoing"
        attachments = render_attachments(conversation.get("attachments") or [])
        rendered.append(
            f"""
            <article class="conversation">
              <div class="conversation-meta">
                <span>{_date(conversation.get("created_at"))}</span>
                <span class="badge">{visibility}</span>
                <span class="badge">{direction}</span>
              </div>
              <div class="body-text">{_multiline(conversation.get("body_text") or "(empty)")}</div>
              {attachments}
            </article>
            """
        )
    return "\n".join(rendered)


def render_attachments(attachments: list[dict[str, Any]]) -> str:
    if not attachments:
        return ""
    rows = []
    for attachment in attachments:
        rows.append(
            f"""
            <li>
              <span>{_text(attachment.get("name") or "attachment")}</span>
              <span>{_text(attachment.get("content_type") or "unknown")}</span>
              <span>{_file_size(attachment.get("size"))}</span>
            </li>
            """
        )
    return f"""
    <div class="attachments">
      <h4>Attachments</h4>
      <ul>{''.join(rows)}</ul>
    </div>
    """


def render_options(rows: list[dict[str, Any]], selected: str) -> str:
    return "".join(
        f'<option value="{_text(row["value"])}" {_selected(row["value"], selected)}>'
        f'{_text(row["value"])} ({row["count"]})</option>'
        for row in rows
    )


def render_numeric_options(
    rows: list[dict[str, Any]],
    selected: int | None,
    labels: dict[int, str],
) -> str:
    return "".join(
        f'<option value="{row["value"]}" {_selected(row["value"], selected)}>'
        f'{_text(labels.get(row["value"], str(row["value"])))} ({row["count"]})</option>'
        for row in rows
    )


def render_not_found(ticket_id: str) -> str:
    return f"""
    <main class="shell">
      <section class="panel">
        <a class="back-link" href="/">Back to search</a>
        <h1>Ticket not found</h1>
        <p>Ticket #{_text(ticket_id)} is not in the archive.</p>
      </section>
    </main>
    """


def _layout(title: str, body: str) -> str:
    return f"""<!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>{_text(title)}</title>
        <link rel="stylesheet" href="/static/app.css">
      </head>
      <body>{body}</body>
    </html>
    """


def _search_params(raw_query: str, default_backend: str = "postgres") -> dict[str, Any]:
    params = parse_qs(raw_query)
    backend = _first(params, "backend") or default_backend
    if backend not in {"postgres", "typesense"}:
        backend = "postgres"
    return {
        "backend": backend,
        "query": _first(params, "q"),
        "product": _first(params, "product"),
        "tag": _first(params, "tag"),
        "status": _optional_int(_first(params, "status")),
        "priority": _optional_int(_first(params, "priority")),
        "created_from": _first(params, "created_from"),
        "created_to": _first(params, "created_to"),
        "updated_from": _first(params, "updated_from"),
        "updated_to": _first(params, "updated_to"),
        "limit": _limit(_first(params, "limit")),
    }


def _first(params: dict[str, list[str]], key: str) -> str:
    return (params.get(key) or [""])[0].strip()


def _optional_int(value: str) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _limit(value: str) -> int:
    parsed = _optional_int(value)
    if parsed is None:
        return DEFAULT_LIMIT
    return min(max(parsed, 1), MAX_LIMIT)


def _ticket_url(ticket_id: int, raw_query: str) -> str:
    return f"/ticket/{ticket_id}" + (f"?{raw_query}" if raw_query else "")


def _active_filter_label(params: dict[str, Any], search_ms: int | None = None) -> str:
    active = [params["backend"]]
    if search_ms is not None:
        active.append(f"{search_ms} ms")
    if params["query"]:
        active.append(f'query "{params["query"]}"')
    for key in ("product", "tag", "created_from", "created_to"):
        if params[key]:
            active.append(f"{key.replace('_', ' ')} {params[key]}")
    if params["status"] is not None:
        active.append(f"status {_status(params['status'])}")
    if params["priority"] is not None:
        active.append(f"priority {_priority(params['priority'])}")
    return ", ".join(active) if active else "recent tickets"


def _result_count(row_count: int, found: int | None) -> str:
    if found is None:
        return str(row_count)
    if found == row_count:
        return str(row_count)
    return f"{row_count} of {found}"


def _postgres_search(database: Database, params: dict[str, Any]) -> list[dict[str, Any]]:
    return database.search(
        params["query"] or None,
        limit=params["limit"],
        product=params["product"] or None,
        tags=[params["tag"]] if params["tag"] else None,
        status=params["status"],
        priority=params["priority"],
        created_from=params["created_from"] or None,
        created_to=params["created_to"] or None,
        updated_from=params["updated_from"] or None,
        updated_to=params["updated_to"] or None,
    )


def _selected(value: Any, selected: Any) -> str:
    return "selected" if str(value) == str(selected) else ""


def _text(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


def _multiline(value: Any) -> str:
    return _text(value).replace("\n", "<br>")


def _excerpt(value: str) -> str:
    escaped = _text(value)
    return escaped.replace("&lt;&lt;", "<mark>").replace("&gt;&gt;", "</mark>")


def _date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return _text(value or "-")


def _status(value: Any) -> str:
    if value is None:
        return "-"
    return f"{_text(STATUS_LABELS.get(value, str(value)))}"


def _priority(value: Any) -> str:
    if value is None:
        return "-"
    return f"{_text(PRIORITY_LABELS.get(value, str(value)))}"


def _number(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def _file_size(value: Any) -> str:
    try:
        size = int(value)
    except (TypeError, ValueError):
        return "-"
    units = ["B", "KB", "MB", "GB"]
    amount = float(size)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{size} B"
