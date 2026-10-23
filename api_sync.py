"""Durable local delivery queue for the job feed API (no extra dependencies)."""

import json
import logging
import sqlite3
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

LOG = logging.getLogger("linkedin_jobs")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the scraper credential to a redirect destination.
        return None


def init_outbox(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS api_outbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL UNIQUE,
        payload TEXT NOT NULL
    )""")


def enqueue(conn: sqlite3.Connection, report: dict) -> str:
    """Call inside the same SQLite transaction that stores the observed jobs."""
    run_id = report.get("run_id") or str(uuid4())
    payload = {key: report[key] for key in (
        "checked_at", "status", "search_url", "message", "page_limit_reached", "jobs",
    ) if key in report}
    payload["run_id"] = run_id
    if "message" in payload:
        payload["message"] = payload["message"][:2000]
    conn.execute("INSERT OR IGNORE INTO api_outbox (run_id, payload) VALUES (?, ?)",
                 (run_id, json.dumps(payload, ensure_ascii=False)))
    return run_id


def flush_outbox(conn: sqlite3.Connection, api_url: str, token: str,
                 max_batches: int = 20) -> bool:
    """Remove a run only after the API acknowledges the same run_id."""
    opener = build_opener(NoRedirect())
    rows = conn.execute(
        "SELECT id, run_id, payload FROM api_outbox ORDER BY id LIMIT ?", (max_batches,),
    ).fetchall()
    for queue_id, run_id, payload in rows:
        request = Request(
            api_url.rstrip("/") + "/api/jobs/ingest", data=payload.encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "X-Scraper-Token": token},
        )
        try:
            with opener.open(request, timeout=15) as response:
                result = json.loads(response.read(4096))
                if response.status != 200 or result.get("run_id") != run_id:
                    raise ValueError("La API no confirmó el identificador del lote")
        except HTTPError as exc:
            LOG.warning("API HTTP %s: lote pendiente. Revisa URL, token y contrato de ingesta.", exc.code)
            return False
        except (URLError, OSError, ValueError, AttributeError) as exc:
            LOG.warning("No se confirmó la entrega a la API (%s). Se conserva el lote local.", type(exc).__name__)
            return False
        with conn:
            conn.execute("DELETE FROM api_outbox WHERE id = ?", (queue_id,))
        LOG.info("API: lote %s confirmado.", run_id)
    return conn.execute("SELECT COUNT(*) FROM api_outbox").fetchone()[0] == 0


def enqueue_existing_jobs(conn: sqlite3.Connection, checked_at: str, search_url: str) -> None:
    """Backfill locally saved jobs without launching Chromium."""
    rows = conn.execute("SELECT payload FROM jobs ORDER BY id").fetchall()
    with conn:
        for offset in range(0, len(rows), 500):
            enqueue(conn, {"checked_at": checked_at, "status": "ok", "search_url": search_url,
                           "message": "Sincronización de ofertas guardadas; no se consultó LinkedIn.",
                           "jobs": [json.loads(row[0]) for row in rows[offset:offset + 500]]})
