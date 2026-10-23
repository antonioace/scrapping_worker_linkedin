"""Monitor local de ofertas de LinkedIn. Python 3.10+."""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
import re
import signal
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from playwright.sync_api import Error as BrowserError
from playwright.sync_api import TimeoutError as BrowserTimeout
from playwright.sync_api import sync_playwright

from api_sync import enqueue, enqueue_existing_jobs, flush_outbox, init_outbox

ROOT = Path(__file__).resolve().parent
BOGOTA = ZoneInfo("America/Bogota")
LOG = logging.getLogger("linkedin_jobs")
CARD_SELECTOR = (
    "li.jobs-search-results__list-item, .job-card-container, "
    ".base-search-card, .base-card, [data-occludable-job-id]"
)
EMPTY_SELECTOR = (
    ".jobs-search-no-results-banner, .jobs-search-no-results, "
    ".jobs-search-results-list__no-results, .jobs-search-two-pane__no-results, "
    ".jobs-search-empty-state"
)
AUTH_SELECTOR = "#global-nav, nav.global-nav, a[href*='/mynetwork/']"


class SessionRequired(RuntimeError):
    pass


class AccessBlocked(RuntimeError):
    pass


def now() -> str:
    return datetime.now(BOGOTA).isoformat(timespec="seconds")


def search_url(keywords="developer react", location="Colombia", start=0) -> str:
    return "https://www.linkedin.com/jobs/search/?" + urlencode({
        "keywords": keywords, "location": location, "f_AL": "true",
        "f_TPR": "r86400", "f_WT": "2", "sortBy": "DD",
        "origin": "JOB_SEARCH_PAGE_JOB_FILTER", "start": start,
    })


def page_url(url: str, start: int) -> str:
    parts = urlsplit(url)
    params = dict(parse_qsl(parts.query))
    params["start"] = str(start)
    return urlunsplit(parts._replace(query=urlencode(params)))


def clean_text(node) -> str:
    return " ".join(node.stripped_strings) if node else ""


def parse_jobs(html: str) -> list[dict]:
    """Extrae tarjetas, no las recomendaciones del panel de detalle."""
    soup = BeautifulSoup(html, "html.parser")
    jobs = {}
    for card in soup.select(CARD_SELECTOR):
        link = card.select_one("a[href*='/jobs/view/']")
        href = link.get("href", "") if link else ""
        match = re.search(r"/jobs/view/(?:[^/?]*-)?(\d+)(?:[/?#]|$)", href)
        job_id = match.group(1) if match else None
        if not job_id:
            raw = card.get("data-job-id") or card.get("data-occludable-job-id", "")
            job_id = str(raw) if str(raw).isdigit() else None
        if not job_id:
            continue
        title_node = card.select_one(".job-card-list__title strong, .job-card-list__title--link strong")
        title_node = title_node or card.select_one(
            ".job-card-list__title, .job-card-list__title--link, "
            ".base-search-card__title, h3"
        )
        title = clean_text(title_node) or (link.get("aria-label", "") if link else "")
        title = title or clean_text(link)
        if not title:
            continue
        posted = card.select_one("time")
        job = {
            "id": job_id, "title": title,
            "company": clean_text(card.select_one(
                ".job-card-container__primary-description, "
                ".artdeco-entity-lockup__subtitle, .base-search-card__subtitle"
            )),
            "location": clean_text(card.select_one(
                ".job-card-container__metadata-item, "
                ".artdeco-entity-lockup__caption, .job-search-card__location"
            )),
            "published": (posted.get("datetime") or clean_text(posted)) if posted else "",
            "url": f"https://www.linkedin.com/jobs/view/{job_id}/",
        }
        # Una tarjeta puede coincidir con varios selectores: conservar la más completa.
        previous = jobs.get(job_id)
        if previous is None or sum(bool(v) for v in job.values()) > sum(bool(v) for v in previous.values()):
            jobs[job_id] = job
    return list(jobs.values())


def needs_login(page) -> bool:
    path = urlsplit(page.url).path
    return any(part in path for part in ("/login", "/checkpoint", "/authwall", "/challenge", "/uas/")) or page.locator(
        "input#username, input[name='session_key']"
    ).first.is_visible()


def open_page(page, url):
    response = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    if response and response.status in (403, 429):
        raise AccessBlocked(f"LinkedIn respondió HTTP {response.status}. Se detiene el monitor.")
    if response and response.status == 401:
        raise SessionRequired("LinkedIn solicita iniciar sesión nuevamente.")
    if response and response.status >= 400:
        raise RuntimeError(f"LinkedIn respondió HTTP {response.status}.")


def login(playwright, profile: Path, timeout: int) -> None:
    context = playwright.chromium.launch_persistent_context(
        str(profile), headless=False, viewport={"width": 1280, "height": 900},
    )
    try:
        page = context.pages[0] if context.pages else context.new_page()
        open_page(page, "https://www.linkedin.com/login")
        LOG.info("Inicia sesión en el navegador y aprueba la verificación si aparece. Tienes %s segundos.", timeout)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            has_cookie = any(c["name"] == "li_at" for c in context.cookies("https://www.linkedin.com"))
            if has_cookie and not needs_login(page) and page.locator(AUTH_SELECTOR).first.is_visible():
                LOG.info("Sesión confirmada. El perfil queda guardado en %s", profile)
                return
            page.wait_for_timeout(1000)
        raise SessionRequired("No se confirmó el inicio de sesión a tiempo. Ejecuta otra vez con --login.")
    finally:
        context.close()


def wait_results(page) -> None:
    try:
        page.wait_for_function(
            """({cards, empty}) => !!document.querySelector(cards + ', ' + empty)
                || /\\/(login|checkpoint|authwall|challenge)(\\/|$)/.test(location.pathname)
                || !!document.querySelector('input#username, input[name="session_key"]')""",
            arg={"cards": CARD_SELECTOR, "empty": EMPTY_SELECTOR}, timeout=30_000,
        )
    except BrowserTimeout as exc:
        if needs_login(page):
            raise SessionRequired("La sesión caducó o LinkedIn pide verificación.") from exc
        raise RuntimeError(
            "No se reconoció la lista de ofertas. Puede haber cambiado la página; "
            "no se registrará como una búsqueda sin resultados. Revisa con --visible."
        ) from exc
    if needs_login(page):
        raise SessionRequired("La sesión caducó o LinkedIn pide verificación. Ejecuta --login.")


def collect_jobs(page, url: str, max_pages: int) -> tuple[list[dict], bool]:
    jobs = {}
    for index in range(max_pages):
        open_page(page, page_url(url, index * 25))
        wait_results(page)
        # Espera el contenido de las tarjetas, que puede llegar después del contenedor.
        page.wait_for_timeout(1500)
        # LinkedIn carga tarjetas al desplazar su panel, no siempre toda la ventana.
        stale = 0
        page_jobs = {}
        for _ in range(30):
            batch = parse_jobs(page.content())
            page_jobs.update({job["id"]: job for job in batch})
            old_count = len(jobs)
            jobs.update({job["id"]: job for job in batch})
            stale = stale + 1 if len(jobs) == old_count else 0
            if stale >= 3:
                break
            page.evaluate("""() => {
                const card = document.querySelector('[data-occludable-job-id], .job-card-container, .base-search-card');
                let panel = card?.parentElement;
                while (panel && !(panel.scrollHeight > panel.clientHeight + 50 &&
                       /auto|scroll/.test(getComputedStyle(panel).overflowY))) panel = panel.parentElement;
                (panel || document.scrollingElement).scrollBy(0, 650);
            }""")
            page.wait_for_timeout(650)
        if needs_login(page):
            raise SessionRequired("LinkedIn solicita verificación durante la consulta.")
        if not page_jobs:
            if page.locator(EMPTY_SELECTOR).count():
                return list(jobs.values()), False
            raise RuntimeError("Hay tarjetas, pero no se pudieron leer. Revisa los selectores con --visible.")
        # Solo terminar cuando LinkedIn deshabilita explícitamente la página siguiente.
        next_button = page.locator(
            "button[aria-label='View next page'], button[aria-label='Ver página siguiente'], "
            "button[aria-label='Siguiente'], button.artdeco-pagination__button--next"
        ).first
        if next_button.count() and next_button.is_disabled():
            return list(jobs.values()), False
    # No se promete cubrir toda la búsqueda: solo las páginas configuradas.
    return list(jobs.values()), True


def database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY, first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL, payload TEXT NOT NULL
    )""")
    init_outbox(conn)
    conn.commit()
    return conn


def atomic_write(path: Path, content: str, encoding="utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding=encoding, newline="", dir=path.parent, delete=False) as file:
            temp = Path(file.name)
            file.write(content)
        os.replace(temp, path)
    finally:
        if temp and temp.exists():
            temp.unlink()


def markdown_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("[", "\\[").replace("]", "\\]").replace("\n", " ")


def save_report(conn, output: Path, url: str, jobs: list[dict], limited: bool,
                queue_sync: bool = False) -> dict:
    checked = now()
    seen = dict(conn.execute("SELECT id, first_seen FROM jobs"))
    enriched = [dict(job, new=job["id"] not in seen,
                     first_seen=seen.get(job["id"], checked), last_seen=checked) for job in jobs]
    new_jobs = [job for job in enriched if job["new"]]
    report = {"run_id": str(uuid4()), "checked_at": checked, "status": "ok", "search_url": url,
              "total_found": len(enriched), "new_count": len(new_jobs),
              "page_limit_reached": limited, "jobs": enriched}
    lines = ["# Ofertas de LinkedIn", "", f"Consulta: {checked} (Colombia)", "",
             f"[Abrir búsqueda]({url})", "",
             f"**{len(enriched)} ofertas encontradas · {len(new_jobs)} nuevas**", ""]
    if limited:
        lines += ["Se alcanzó el máximo de páginas configurado; puede haber más resultados.", ""]
    if not jobs:
        lines += ["LinkedIn mostró la búsqueda sin resultados.", ""]
    for title, subset in (("Nuevas", new_jobs), ("Ya vistas", [job for job in enriched if not job["new"]])):
        if subset:
            lines += [f"## {title}", "", "| Cargo | Empresa | Ubicación | Publicación |", "|---|---|---|---|"]
            for job in subset:
                cells = [f"[{markdown_text(job['title'])}]({job['url']})"]
                cells += [markdown_text(job[key]) or "—" for key in ("company", "location", "published")]
                lines.append("| " + " | ".join(cells) + " |")
            lines.append("")
    csv_buffer = io.StringIO(newline="")
    fields = ["id", "title", "company", "location", "published", "url", "new", "first_seen", "last_seen"]
    writer = csv.DictWriter(csv_buffer, fieldnames=fields)
    writer.writeheader()
    for job in enriched:
        writer.writerow({key: ("'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value)
                         for key, value in job.items()})
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    stamp = datetime.fromisoformat(checked).strftime("%Y%m%d_%H%M%S")
    atomic_write(output / "history" / f"{stamp}.json", payload)
    atomic_write(output / "latest.json", payload)
    atomic_write(output / "latest.csv", csv_buffer.getvalue(), encoding="utf-8-sig")
    atomic_write(output / "latest.md", "\n".join(lines))
    # Marcar como vistas solo después de haber guardado el reporte correctamente.
    with conn:
        conn.executemany("""INSERT INTO jobs VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen, payload=excluded.payload""",
            [(job["id"], job["first_seen"], checked, json.dumps(job, ensure_ascii=False)) for job in enriched])
        if queue_sync:
            enqueue(conn, report)
    atomic_write(output / "status.json", json.dumps({"checked_at": checked, "status": "ok"}, indent=2))
    return report


def monitor(playwright, args, url: str) -> None:
    context = playwright.chromium.launch_persistent_context(
        str(args.profile), headless=not args.visible,
        viewport={"width": 1440, "height": 1000}, locale="es-CO",
    )
    try:
        if not any(c["name"] == "li_at" for c in context.cookies("https://www.linkedin.com")):
            raise SessionRequired("No hay una sesión guardada. Ejecuta: python linkedin_jobs.py --login")
        page = context.pages[0] if context.pages else context.new_page()
        with closing(database(args.output / "jobs.sqlite3")) as conn:
            failures = 0
            while True:
                started = time.monotonic()
                try:
                    jobs, limited = collect_jobs(page, url, args.max_pages)
                    report = save_report(conn, args.output, url, jobs, limited, queue_sync=bool(args.api_url))
                    if args.api_url:
                        flush_outbox(conn, args.api_url, args.api_token)
                    failures = 0
                    LOG.info("REPORTE: %s ofertas, %s nuevas. %s", report["total_found"], report["new_count"], args.output / "latest.md")
                    for job in report["jobs"]:
                        if job["new"]:
                            LOG.info("NUEVA: %s | %s | %s", job["title"], job["company"], job["url"])
                except (SessionRequired, AccessBlocked):
                    raise
                except (BrowserError, RuntimeError) as exc:
                    failures += 1
                    failure = {
                        "checked_at": now(), "status": "error", "message": str(exc),
                        "search_url": url,
                    }
                    atomic_write(args.output / "status.json", json.dumps(failure, ensure_ascii=False, indent=2))
                    if args.api_url:
                        with conn:
                            enqueue(conn, failure)
                        flush_outbox(conn, args.api_url, args.api_token)
                    LOG.error("Consulta fallida (%s/3): %s", failures, exc)
                    if args.once or failures >= 3:
                        raise
                if args.once:
                    return
                delay = max(1, args.interval * (2 ** min(failures, 2)) - (time.monotonic() - started))
                LOG.info("Siguiente consulta en %.0f segundos. Ctrl+C para detener.", delay)
                time.sleep(delay)
    finally:
        context.close()


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login", action="store_true", help="Abre el navegador para guardar o renovar la sesión y sale.")
    parser.add_argument("--once", action="store_true", help="Genera un reporte y sale.")
    parser.add_argument("--visible", action="store_true", help="Muestra el navegador durante las consultas.")
    parser.add_argument("--require-session", action="store_true",
                        help="No abre login automáticamente; requiere un perfil ya autenticado (servidores).")
    parser.add_argument("--interval", type=int, default=300, help="Segundos entre consultas; predeterminado: 300.")
    parser.add_argument("--max-pages", type=int, default=3, help="Máximo de páginas por consulta (25 posiciones por página).")
    parser.add_argument("--keywords", default="developer react")
    parser.add_argument("--location", default="Colombia")
    parser.add_argument("--profile", type=Path, default=ROOT / ".linkedin-profile")
    parser.add_argument("--output", type=Path, default=ROOT / "reports")
    parser.add_argument("--login-timeout", type=int, default=300)
    parser.add_argument("--api-url", default=os.environ.get("JOBS_API_URL", ""),
                        help="URL base del backend, por ejemplo http://localhost:8000. También JOBS_API_URL.")
    parser.add_argument("--sync-only", action="store_true",
                        help="Envía las ofertas ya guardadas a la API sin abrir Chromium.")
    args = parser.parse_args()
    if args.interval < 60 or args.max_pages < 1 or args.login_timeout < 1:
        parser.error("--interval debe ser >= 60, --max-pages y --login-timeout deben ser >= 1.")
    args.api_url = args.api_url.strip().rstrip("/")
    args.api_token = os.environ.get("JOBS_INGEST_TOKEN", "")
    if args.api_url:
        parsed = urlsplit(args.api_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
            parser.error("--api-url debe ser una URL base http(s), sin credenciales, ruta ni parámetros.")
        if not args.api_token:
            parser.error("Configura JOBS_INGEST_TOKEN para enviar resultados a la API.")
    if args.sync_only and (not args.api_url or args.login):
        parser.error("--sync-only requiere --api-url y no se combina con --login.")
    args.profile = args.profile.resolve()
    args.output = args.output.resolve()
    return args


def save_stopped_status(args: argparse.Namespace, url: str, message: str) -> None:
    failure = {
        "checked_at": now(), "status": "stopped", "message": message,
        "search_url": url,
    }
    atomic_write(args.output / "status.json", json.dumps(failure, ensure_ascii=False, indent=2))
    if args.api_url:
        with closing(database(args.output / "jobs.sqlite3")) as conn:
            with conn:
                enqueue(conn, failure)
            flush_outbox(conn, args.api_url, args.api_token)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S", stream=sys.stdout)
    args = arguments()
    url = search_url(args.keywords, args.location)
    LOG.info("Búsqueda: %s", url)
    try:
        if args.sync_only:
            with closing(database(args.output / "jobs.sqlite3")) as conn:
                enqueue_existing_jobs(conn, now(), url)
                queued = conn.execute("SELECT COUNT(*) FROM api_outbox").fetchone()[0]
                return 0 if flush_outbox(conn, args.api_url, args.api_token, max_batches=max(1, queued)) else 1
        with sync_playwright() as playwright:
            if args.login:
                login(playwright, args.profile, args.login_timeout)
                return 0
            if not args.profile.exists():
                if args.require_session:
                    raise SessionRequired("No hay perfil autenticado. Ejecuta primero el servicio Docker login o --login.")
                login(playwright, args.profile, args.login_timeout)
            monitor(playwright, args, url)
    except KeyboardInterrupt:
        if not args.login and not args.sync_only:
            save_stopped_status(args, url, "Monitor detenido por el operador")
        LOG.info("Monitor detenido.")
        return 0
    except (SessionRequired, AccessBlocked, BrowserError, RuntimeError, OSError, sqlite3.Error) as exc:
        save_stopped_status(args, url, str(exc))
        LOG.error("%s", exc)
        LOG.error("Si pide acceso, ejecuta --login. Si falta Chromium: python -m playwright install chromium.")
        return 1
    return 0


if __name__ == "__main__":
    def handle_stop(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, handle_stop)
    raise SystemExit(main())
