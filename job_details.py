"""Read descriptions and cache decisions without storing rejected job content."""

import json
import logging
import re
import sqlite3
import time
from collections.abc import Callable

from bs4 import BeautifulSoup

from stack_filter import POLICY_VERSION, matching_technologies

LOG = logging.getLogger("linkedin_jobs")
DESCRIPTION_SELECTOR = (
    "#job-details, .jobs-description-content__text, "
    ".jobs-description__content, section.description .description__text, "
    "section.description .show-more-less-html__markup"
)


class DescriptionUnavailable(RuntimeError):
    pass


class JobGone(RuntimeError):
    pass


def extract_description(html: str, job_id: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for selector in DESCRIPTION_SELECTOR.split(","):
        for node in soup.select(selector.strip()):
            text = node.get_text("\n", strip=True)
            if text:
                return text
    # Public detail pages may expose the complete posting as structured data.
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            pending = [json.loads(script.string or script.get_text())]
        except (ValueError, RecursionError):
            continue
        while pending:
            item = pending.pop()
            if isinstance(item, list):
                pending.extend(item)
            elif isinstance(item, dict):
                types = item.get("@type", [])
                types = [types] if isinstance(types, str) else types
                if isinstance(types, list) and "JobPosting" in types:
                    posting_url = str(item.get("url", ""))
                    identity = re.search(r"/jobs/view/(?:[^/?]*-)?(\d+)(?:[/?#]|$)", posting_url)
                    if identity and identity.group(1) != job_id:
                        continue
                    description = item.get("description")
                    if isinstance(description, str) and description.strip():
                        return BeautifulSoup(description, "html.parser").get_text("\n", strip=True)
                graph = item.get("@graph")
                if isinstance(graph, list):
                    pending.extend(graph)
    return ""


def init_description_cache(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS job_description_cache (
        id TEXT PRIMARY KEY, description TEXT NOT NULL,
        checked_at REAL NOT NULL, policy_version TEXT NOT NULL
    )""")


def select_jobs(conn: sqlite3.Connection, candidates: list[dict],
                read_description: Callable[[dict], str], *, max_details: int = 20,
                cache_seconds: int = 21600) -> tuple[list[dict], bool, str]:
    """Only an evaluated description can qualify a job; unread jobs remain pending."""
    accepted = []
    requested = cached = rejected = pending = failed = completed = 0
    for job in candidates:
        row = conn.execute(
            "SELECT description, checked_at FROM job_description_cache WHERE id=? AND policy_version=?",
            (job["id"], POLICY_VERSION),
        ).fetchone()
        if row and time.time() - row[1] < cache_seconds:
            description = row[0]
            cached += 1
        else:
            if requested >= max_details:
                pending += 1
                continue
            requested += 1
            try:
                description = read_description(job)
                if not description.strip() or len(description) > 100_000:
                    raise DescriptionUnavailable("Descripción ausente o demasiado larga.")
            except JobGone:
                description = ""
            except DescriptionUnavailable:
                failed += 1
                LOG.warning("No se pudo evaluar la descripción de la oferta %s; queda pendiente.", job["id"])
                continue
            completed += 1
            # Rejected jobs retain only their ID and decision timestamp, not their text.
            stored = description if matching_technologies(description) else ""
            with conn:
                conn.execute("""INSERT INTO job_description_cache VALUES (?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET description=excluded.description,
                    checked_at=excluded.checked_at, policy_version=excluded.policy_version""",
                    (job["id"], stored, time.time(), POLICY_VERSION))
        technologies = matching_technologies(description)
        if technologies:
            accepted.append(dict(job, description=description, technologies=technologies))
        else:
            rejected += 1
    if failed and not completed and not cached:
        raise DescriptionUnavailable("No se pudo leer ninguna descripción. No se publicarán ofertas sin evaluar.")
    message = (f"Candidatas: {len(candidates)}; aceptadas: {len(accepted)}; descartadas: {rejected}; "
               f"pendientes: {pending + failed}; consultas de detalle: {requested}; caché: {cached}.")
    return accepted, bool(pending or failed), message
