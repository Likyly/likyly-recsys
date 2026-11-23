"""The free recommendation audit of www.likyly.com/audit - no account needed.

    POST /audits                 {url, captcha_token?}  starts the audit; answers the verdicts (teaser,
                                                          201) when it ends within SYNC_WAIT_SECONDS,
                                                          else the job to follow (202, {job, status})
                                 {page_id, captcha_token?}  ... of a page the visitor's browser sent
                                 {url, html, captcha_token?}  ... of a page's pasted source code
    GET  /audits/jobs/{job}                               where the audit is: running | waiting (its
                                                          place in front of the browser: ahead,
                                                          wait_seconds) | done (report) | failed
    POST /audits/page            form {url, html, lang?} the bookmarklet: keeps the page the visitor's
                                                          browser shows (for sites that turn robots
                                                          away), redirects to www.likyly.com/audit?page=…
    POST /audits/{token}/email   {email}                 records the lead, answers the full report and
                                                          emails it (in the background)
    GET  /audits/{token}                                  the report again: full once an email was given
                                                          (the link in the email), teaser before

The audit itself is application/utils/site_audit.py. `token` is unguessable (144 random bits) and is
the only key to a report; a report link works for REPORT_TTL_DAYS after the audit, then answers 410.
Limits per visitor (salted hash of the IP, never the address): a few audits an hour, a few emails a
day, and a cap on report reads - tighter on unknown tokens, so guessing links goes nowhere. When TURNSTILE_SECRET_KEY is set, POST /audits also needs a Cloudflare Turnstile
token (the site sends it); without it, the check is skipped (local development).

AUDIT_RENDERER_URL (+ AUDIT_RENDERER_TOKEN): the audit's browser (renderer/, its own container), for
blocks drawn by JavaScript. Unset: the audit reads the HTML only. It shows one page at a time: audits
take turns in front of it (application/utils/audit_jobs.py), each for all its pages in a row.

Language: what the visitor reads (errors, the report, its email) is in French or English, from the
request's Accept-Language (i18n.py; the website sends its page's language). A report keeps the
language it was made in (result["language"]): its email and its link follow it.

Marketing surface of the website, not part of the public API: hidden from /openapi.json.
"""
import asyncio
import hashlib
import html
import os
import re
import secrets
import sys
import time
from datetime import timedelta
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, model_validator

current_dir = os.path.dirname(os.path.abspath(__file__))
_utils_dir = os.path.abspath(os.path.join(current_dir, "../utils"))
if _utils_dir not in sys.path:
    sys.path.insert(0, _utils_dir)

import audit_jobs  # noqa: E402
import db  # noqa: E402
import site_audit  # noqa: E402
from i18n import pick, request_language  # noqa: E402
from observability import log_event  # noqa: E402
from upgrade_requests import DEFAULT_RECIPIENT, deliver, smtp_settings  # noqa: E402

router = APIRouter(prefix="/audits", tags=["siteAudit"], include_in_schema=False)

AUDITS_PER_HOUR = 6
AUDITS_PER_DAY = 20
EMAILS_PER_DAY = 5
AUDIT_DEADLINE_SECONDS = 30
RENDERED_AUDIT_DEADLINE_SECONDS = 50  # from the moment the audit gets the browser: 5 to 15 s a page
SYNC_WAIT_SECONDS = 8  # POST /audits answers the report itself when the audit is that quick
JOBS_PER_VISITOR = 2  # audits running at once from one connection


def browser_busy() -> str:
    return site_audit.t("notre navigateur d'audit était saturé, relancez l'audit dans quelques minutes pour une lecture complète",
                        "our audit browser was overloaded, run the audit again in a few minutes for a full reading")


MAX_PAGE_CHARS = 6 * 1024 * 1024  # a page sent by the visitor's browser (scripts and styles trimmed)
PAGES_PER_HOUR = 20
REPORT_TTL_DAYS = 10
READS_PER_HOUR = 60
MISSES_PER_HOUR = 10
EMAIL_RE = re.compile(r"^[^@\s<>()\[\],;:\"]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
TEMPLATES = Path(__file__).resolve().parent / "email_templates"


def site_url() -> str:
    return (os.environ.get("PUBLIC_SITE_URL") or "https://www.likyly.com").rstrip("/")


def allow_private() -> bool:
    """Local development only: audit the CMS sandboxes on localhost."""
    return os.environ.get("SITE_AUDIT_ALLOW_PRIVATE") == "1"


class AuditCreate(BaseModel):
    url: Optional[str] = Field(None, min_length=1, max_length=2000)
    captcha_token: Optional[str] = Field(None, max_length=4096)
    page_id: Optional[str] = Field(None, min_length=8, max_length=64)  # sent by the bookmarklet
    html: Optional[str] = Field(None, min_length=1, max_length=MAX_PAGE_CHARS)  # pasted source code

    @model_validator(mode="after")
    def _one_source(self):
        if not self.page_id and not self.url:
            raise ValueError("url or page_id")
        return self


class PageStore:
    """Pages the visitor's browser sent through the bookmarklet, kept in memory until the audit page
    picks them up (once), for PAGE_TTL_SECONDS at most and MAX_TOTAL_CHARS in all. Never stored."""

    PAGE_TTL_SECONDS = 15 * 60
    MAX_TOTAL_CHARS = 40 * 1024 * 1024

    def __init__(self):
        self.pages: dict[str, tuple[float, str, str]] = {}

    def _expire(self) -> None:
        now = time.monotonic()
        self.pages = {k: v for k, v in self.pages.items() if now - v[0] < self.PAGE_TTL_SECONDS}

    def put(self, url: str, html_text: str) -> Optional[str]:
        self._expire()
        if sum(len(v[2]) for v in self.pages.values()) + len(html_text) > self.MAX_TOTAL_CHARS:
            return None
        page_id = secrets.token_urlsafe(16)
        self.pages[page_id] = (time.monotonic(), url, html_text)
        return page_id

    def take(self, page_id: str) -> Optional[tuple[str, str]]:
        self._expire()
        entry = self.pages.pop(page_id, None)
        return (entry[1], entry[2]) if entry else None

    def reset(self) -> None:
        self.pages.clear()


sent_pages = PageStore()


class AuditEmail(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)


def client_ip(request: Request) -> str:
    """Caddy (the only way in) sets X-Forwarded-For to the visitor's address."""
    forwarded = request.headers.get("x-forwarded-for", "")
    first = forwarded.split(",")[0].strip()
    return first or (request.client.host if request.client else "unknown")


def ip_hash(request: Request) -> str:
    salt = os.environ.get("SITE_AUDIT_SALT") or "likyly-site-audit"
    return hashlib.sha256(f"{salt}:{client_ip(request)}".encode()).hexdigest()[:32]


async def verify_captcha(token: Optional[str], request: Request) -> None:
    secret = os.environ.get("TURNSTILE_SECRET_KEY")
    if not secret:
        return
    if not token:
        raise HTTPException(status_code=400, detail=pick(request, fr="Vérification anti-robot manquante : rechargez la page et réessayez.",
                                                         en="The anti-robot check is missing: reload the page and try again."))
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            answer = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={"secret": secret, "response": token, "remoteip": client_ip(request)},
            )
        ok = answer.json().get("success") is True
    except (httpx.HTTPError, ValueError):
        log_event("site_audit_captcha_unreachable")
        return  # Cloudflare down: don't lock the page, the rate limits still apply
    if not ok:
        raise HTTPException(status_code=400, detail=pick(request, fr="La vérification anti-robot a échoué : rechargez la page et réessayez.",
                                                         en="The anti-robot check failed: reload the page and try again."))


class HourlyLimiter:
    """Per-visitor counts over the last hour, in memory: enough for one API container (a restart
    only forgives the current hour). Used for report reads, which leave no row to count."""

    def __init__(self, limit: int):
        self.limit = limit
        self.hits: dict[str, list[float]] = {}

    def allow(self, visitor: str) -> bool:
        now = time.monotonic()
        recent = [t for t in self.hits.get(visitor, []) if now - t < 3600]
        if len(recent) >= self.limit:
            self.hits[visitor] = recent
            return False
        recent.append(now)
        self.hits[visitor] = recent
        if len(self.hits) > 50_000:  # never grows without bound
            self.hits = {k: v for k, v in self.hits.items() if v and now - v[-1] < 3600}
        return True

    def reset(self) -> None:
        self.hits.clear()


reads = HourlyLimiter(READS_PER_HOUR)
misses = HourlyLimiter(MISSES_PER_HOUR)
page_posts = HourlyLimiter(PAGES_PER_HOUR)
jobs = audit_jobs.JobBoard()
browser_queue = audit_jobs.BrowserQueue()


def expires_at(row: dict[str, Any]):
    return row["created_at"] + timedelta(days=REPORT_TTL_DAYS)


def find_report(token: str, request: Request) -> dict[str, Any]:
    """The report behind a link, or the reason it can't be shown (429 / 404 / 410)."""
    visitor = ip_hash(request)
    if not reads.allow(visitor):
        raise HTTPException(status_code=429, detail=pick(request, fr="Trop de consultations depuis cette connexion : réessayez dans une heure.",
                                                         en="Too many reads from this connection: try again in an hour."))
    row = db.get_site_audit(token[:64])
    if row is None:
        if not misses.allow(visitor):
            raise HTTPException(status_code=429, detail=pick(request, fr="Trop de liens invalides depuis cette connexion : réessayez dans une heure.",
                                                             en="Too many invalid links from this connection: try again in an hour."))
        raise HTTPException(status_code=404, detail=pick(request, fr="Ce rapport n'existe pas.", en="This report doesn't exist."))
    if db.utcnow() >= expires_at(row):
        raise HTTPException(status_code=410, detail=pick(
            request,
            fr=f"Ce rapport a expiré : un lien d'audit reste valable {REPORT_TTL_DAYS} jours. Relancez un audit pour un rapport à jour.",
            en=f"This report has expired: an audit link stays valid for {REPORT_TTL_DAYS} days. Run a new audit for an up-to-date report.",
        ))
    return row


def public_report(row: dict[str, Any], full: bool) -> dict[str, Any]:
    result = row["result"]
    body = result if full else site_audit.teaser(result)
    if full:
        body = {**result, "block_count": len(result.get("blocks", []))}
    return {
        "token": row["token"], "created_at": row["created_at"], "expires_at": expires_at(row), "unlocked": full,
        "report_url": report_link(row),
        **body,
    }


def report_language(row: dict[str, Any]) -> str:
    """The language the report was made in (reports from before English existed: French)."""
    return "en" if (row.get("result") or {}).get("language") == "en" else "fr"


def report_link(row: dict[str, Any]) -> str:
    prefix = "/en" if report_language(row) == "en" else ""
    return f"{site_url()}{prefix}/audit/r/{row['token']}"


def renderer() -> Optional[site_audit.RendererClient]:
    base = os.environ.get("AUDIT_RENDERER_URL")
    return site_audit.RendererClient(base, os.environ.get("AUDIT_RENDERER_TOKEN")) if base else None


def _run(url: str, provided: Optional[site_audit.Page] = None, source: str = "robot", job: Optional[audit_jobs.AuditJob] = None) -> dict[str, Any]:
    browser = renderer()
    job = job or audit_jobs.AuditJob()
    deadline = [time.monotonic() + AUDIT_DEADLINE_SECONDS]
    fetcher = site_audit.SafeFetcher(allow_private=allow_private())

    def fetch(target: str) -> site_audit.Page:
        if time.monotonic() > deadline[0]:
            raise site_audit.AuditError(site_audit.t("Le site met trop de temps à répondre.", "The site takes too long to answer."))
        return fetcher.get(target)

    renders: list[str] = []
    lease = {"held": False, "refused": False}

    def render(target: str) -> site_audit.Page:
        # The browser is lent to one audit at a time, for all its pages: wait for it on the first one.
        if lease["refused"]:
            raise site_audit.AuditError(browser_busy())
        if not lease["held"]:
            if not browser_queue.acquire(job):
                lease["refused"] = True
                deadline[0] = max(deadline[0], time.monotonic() + 20)  # the rest of the audit, from the HTML
                log_event("site_audit_browser_busy")
                raise site_audit.AuditError(browser_busy())
            lease["held"] = True
            deadline[0] = time.monotonic() + RENDERED_AUDIT_DEADLINE_SECONDS
        left = deadline[0] - time.monotonic()
        if left < 9:  # keep time for the rest of the audit (stock, UCP)
            raise site_audit.AuditError(site_audit.t("plus assez de temps pour l'afficher dans un navigateur", "not enough time left to display it in a browser"))
        assert browser is not None
        # The audited page gets the most time (its blocks at the bottom load as it scrolls); the
        # pages it is compared with, less.
        cap = 22 if not renders else 15
        renders.append(target)
        return browser.render(target, int(min(cap, left - 6) * 1000))

    try:
        return site_audit.run_audit(url, fetch, allow_private=allow_private(), render=render if browser else None, provided=provided, source=source,
                                    render_done=lambda: browser_queue.release(job))
    finally:
        browser_queue.release(job)
        fetcher.close()
        if browser:
            browser.close()


def _work(job: audit_jobs.AuditJob, url: str, provided: Optional[site_audit.Page], source: str, lang: str = "fr") -> None:
    """The audit, in its own thread: the report (stored) or the reason it failed, on the job - in `lang`."""
    with site_audit.language(lang):
        try:
            result = _run(url, provided, source, job)
            row = db.create_site_audit(result["url"], result.get("host"), result.get("score"), result, job.visitor)
            log_event("site_audit_run", host=result.get("host"), cms=result["cms"]["id"], page_type=result["page_type"]["id"], score=result["score"])
            job.done(jsonable_encoder(public_report(row, full=False)))
        except site_audit.AuditError as error:
            log_event("site_audit_failed", reason=str(error)[:120], blocked=error.blocked)
            job.fail(str(error), error.blocked)
        except Exception as error:  # never leave the visitor's page waiting
            log_event("site_audit_crashed", error=type(error).__name__, message=str(error)[:200])
            job.fail(site_audit.t("L'audit a rencontré une erreur. Réessayez dans un instant.", "The audit ran into an error. Try again in a moment."))


def job_answer(job: audit_jobs.AuditJob) -> JSONResponse:
    if job.status == "done":
        return JSONResponse(status_code=201, content=job.report)
    if job.status == "failed":
        return JSONResponse(status_code=422, content={"detail": job.detail, "blocked": job.blocked})
    return JSONResponse(status_code=202, content=job.public())


def audit_failed(error: site_audit.AuditError) -> JSONResponse:
    """422 with the reason; `blocked` when the site turns robots away (the page offers the browser way)."""
    log_event("site_audit_failed", reason=str(error)[:120], blocked=error.blocked)
    return JSONResponse(status_code=422, content={"detail": str(error), "blocked": error.blocked})


@router.post("", status_code=201, summary="Run a recommendation audit")
async def create_audit(payload: AuditCreate, request: Request):
    """Runs the audit of one page and answers the verdicts; the explanations come with the email."""
    visitor = ip_hash(request)
    lang = request_language(request)
    now = db.utcnow()
    running = jobs.running_for(visitor)
    if running >= JOBS_PER_VISITOR:
        raise HTTPException(status_code=429, detail=pick(request, fr="Un audit est déjà en cours depuis votre connexion : attendez son résultat avant d'en lancer un autre.",
                                                         en="An audit is already running from your connection: wait for its result before starting another one."))
    if db.count_site_audits_since(visitor, now - timedelta(hours=1)) + running >= AUDITS_PER_HOUR or db.count_site_audits_since(visitor, now - timedelta(days=1)) + running >= AUDITS_PER_DAY:
        raise HTTPException(status_code=429, detail=pick(request, fr="Vous avez lancé beaucoup d'audits : réessayez dans une heure.",
                                                         en="You have run a lot of audits: try again in an hour."))
    await verify_captcha(payload.captcha_token, request)
    provided: Optional[site_audit.Page] = None
    source = "robot"
    url = payload.url or ""
    if payload.page_id:
        sent = sent_pages.take(payload.page_id)
        if sent is None:
            raise HTTPException(status_code=410, detail=pick(request, fr="Cette page n'est plus disponible : retournez sur votre site et cliquez à nouveau sur le favori LIKYLY.",
                                                             en="This page is no longer available: go back to your site and click the LIKYLY bookmark again."))
        url, page_html = sent
        provided, source = site_audit.Page(url=url, status=200, text=page_html, content_type="text/html"), "browser"
    elif payload.html is not None:
        provided, source = site_audit.Page(url=url, status=200, text=payload.html, content_type="text/html"), "paste"
        try:
            with site_audit.language(lang):
                provided.url = site_audit.normalize_input_url(url, allow_private())
        except site_audit.AuditError as error:
            return audit_failed(error)
    job = audit_jobs.AuditJob(visitor)
    jobs.start(job, lambda: _work(job, url, provided, source, lang))
    end = time.monotonic() + SYNC_WAIT_SECONDS
    while not job.finished and time.monotonic() < end:
        await asyncio.sleep(0.05)
    return job_answer(job)


@router.get("/jobs/{job_id}", summary="Follow a running audit")
async def follow_audit(job_id: str, request: Request):
    """Where the audit is; its page asks every second or two (a waiting audit whose page stopped
    asking gives its place in front of the browser to the next one)."""
    job = jobs.get(job_id[:64])
    if job is None:
        raise HTTPException(status_code=404, detail=pick(request, fr="Cet audit n'est plus suivi (le service a peut-être redémarré) : relancez-le.",
                                                         en="This audit is no longer followed (the service may have restarted): run it again."))
    job.seen()
    return job.public()


@router.post("/page", summary="Send a page from the visitor's browser")
async def receive_page(request: Request):
    """The bookmarklet's form: the page as the visitor's browser shows it (a site that turns robots
    away still shows itself to its visitors). Kept a few minutes, then the visitor lands on the audit
    page, which runs the audit of it. A plain form POST - a navigation, not a cross-origin call - so
    the site's own scripts and CORS rules don't get in the way."""
    # The page's language: the form's `lang` field ("fr" | "en"), or ?lang= on the form's action
    # (known even when the form can't be read). English pages live under /en.
    prefix = "/en" if request.query_params.get("lang") == "en" else ""
    target = f"{site_url()}{prefix}/audit/navigateur"
    if not page_posts.allow(ip_hash(request)):
        return RedirectResponse(f"{target}?erreur=limite", status_code=303)
    try:
        form = await request.form(max_files=0, max_fields=4, max_part_size=MAX_PAGE_CHARS * 2)
    except Exception:
        return RedirectResponse(f"{target}?erreur=trop-lourde", status_code=303)
    if form.get("lang") in ("fr", "en"):
        prefix = "/en" if form.get("lang") == "en" else ""
        target = f"{site_url()}{prefix}/audit/navigateur"
    url, page_html = form.get("url"), form.get("html")
    if not isinstance(url, str) or not isinstance(page_html, str) or not page_html.strip():
        return RedirectResponse(f"{target}?erreur=vide", status_code=303)
    if len(page_html) > MAX_PAGE_CHARS:
        return RedirectResponse(f"{target}?erreur=trop-lourde", status_code=303)
    try:
        url = site_audit.normalize_input_url(url, allow_private())
    except site_audit.AuditError:
        return RedirectResponse(f"{target}?erreur=adresse", status_code=303)
    page_id = sent_pages.put(url, page_html)
    if page_id is None:
        return RedirectResponse(f"{target}?erreur=occupe", status_code=303)
    log_event("site_audit_page_received", host=site_audit.urlsplit(url).hostname, chars=len(page_html))
    return RedirectResponse(f"{site_url()}{prefix}/audit?page={page_id}", status_code=303)


@router.get("/{token}", summary="Get an audit report")
async def get_audit(token: str, request: Request):
    """The report: in full once an email was given for it, the verdicts only before."""
    row = find_report(token, request)
    return public_report(row, full=bool(row.get("email")))


@router.post("/{token}/email", summary="Get the full audit report by email")
async def email_audit(token: str, payload: AuditEmail, request: Request, background_tasks: BackgroundTasks):
    """Records the email (the lead), answers the full report and emails it in the background."""
    row = find_report(token, request)
    email = payload.email.strip()
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail=pick(request, fr="Cette adresse email n'est pas valide.", en="This email address isn't valid."))
    visitor = ip_hash(request)
    if db.count_site_audits_since(visitor, db.utcnow() - timedelta(days=1), with_email=True) >= EMAILS_PER_DAY:
        raise HTTPException(status_code=429, detail=pick(request, fr="Plusieurs rapports ont déjà été envoyés aujourd'hui depuis cette connexion : réessayez demain.",
                                                         en="Several reports were already sent from this connection today: try again tomorrow."))
    db.set_site_audit_email(row["token"], email)
    row = db.get_site_audit(row["token"])
    background_tasks.add_task(send_report, row)
    log_event("site_audit_email_received", host=row.get("host"), score=row.get("score"))
    return public_report(row, full=True)


# ------------------------------------------------------------------------------------------
# The emails
# ------------------------------------------------------------------------------------------

C = {"ink": "#071A3D", "blue": "#2F6BFF", "mint": "#20C6A5", "amber": "#F6A92A", "coral": "#FF5B72", "bg": "#F7F9FC", "border": "#E3E9F2", "muted": "#5C6B82"}
FONT = "Inter, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
STATUS_STYLE = {
    "pass": ("OK", C["mint"], "#ECFBF7"),
    "warn": ("À améliorer", C["amber"], "#FFF8EB"),
    "fail": ("Problème", C["coral"], "#FFF1F3"),
    "unknown": ("Non mesuré", C["muted"], C["bg"]),
    "info": ("Info", C["blue"], "#F1F5FF"),
}
STATUS_LABELS_EN = {"pass": "OK", "warn": "To improve", "fail": "Problem", "unknown": "Not measured", "info": "Info"}
STATUS_ORDER = {"fail": 0, "warn": 1, "pass": 2, "unknown": 3, "info": 4}  # problems first
GRADE_COLOR = {"A": C["mint"], "B": C["mint"], "C": C["amber"], "D": C["coral"], "E": C["coral"]}


def e(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _p(text: str, extra: str = "") -> str:
    return f'<p style="margin:0 0 16px;font:400 15px/1.6 {FONT};color:{C["ink"]};{extra}">{text}</p>'


def _button(label: str, href: str) -> str:
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:8px 0 24px;"><tr>'
        f'<td align="center" bgcolor="{C["ink"]}" style="border-radius:12px;">'
        f'<a href="{e(href)}" target="_blank" style="display:inline-block;padding:14px 26px;font:600 15px/1 {FONT};color:#ffffff;text-decoration:none;border-radius:12px;">{e(label)}</a>'
        f"</td></tr></table>"
    )


def status_style(status: str) -> tuple[str, str, str]:
    """(label in the report's language, accent, tint) - inside `site_audit.language(...)`."""
    label, accent, tint = STATUS_STYLE.get(status, STATUS_STYLE["unknown"])
    return site_audit.t(label, STATUS_LABELS_EN.get(status, STATUS_LABELS_EN["unknown"])), accent, tint


def score_label(r: dict[str, Any]) -> str:
    """"58/100" - or, when too little could be read to grade the page, says so."""
    return f"{int(r['score'])}/100" if r.get("score") is not None else site_audit.t("sans note (audit partiel)", "no score (partial audit)")


def partial_note() -> str:
    return site_audit.t("Les recommandations de cette page se chargent dans le navigateur : l'audit n'a pas pu en lire assez pour donner une note. "
                        "Les contrôles mesurés sont détaillés ci-dessous.",
                        "This page's recommendations load in the browser: the audit couldn't read enough of them to give a score. "
                        "The checks that could be measured are detailed below.")


def until_label(row: dict[str, Any]) -> str:
    until = expires_at(row)
    return site_audit.t(until.strftime("%d/%m/%Y"), f"{until:%B} {until.day}, {until.year}")


def site_link(href: str) -> str:
    """A page of the website, in the report's language (English under /en)."""
    return f"{site_url()}{'/en' if site_audit.current_language() == 'en' and href.startswith('/') else ''}{href}"


def report_body(row: dict[str, Any]) -> str:
    """The email's body, in the language of the current `site_audit.language(...)` block."""
    t = site_audit.t
    r = row["result"]
    link = report_link(row)
    color = GRADE_COLOR.get(r["grade"], C["ink"])
    host_link = f'<a href="{e(r["final_url"])}" style="color:{C["blue"]};">{e(r["host"])}</a>'
    parts = [
        _p(t(f'Voici l\'audit des recommandations de {host_link}, analysé depuis la page « {e(r.get("item_name") or r["page_type"]["label"])} ».',
             f'Here is the recommendation audit of {host_link}, analyzed from the page “{e(r.get("item_name") or r["page_type"]["label"])}”.')),
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 24px;background:{C["bg"]};border:1px solid {C["border"]};border-radius:12px;">'
        f'<tr><td width="110" align="center" style="padding:18px 8px;border-right:1px solid {C["border"]};">'
        + (f'<div style="font:800 40px/1 Sora, {FONT};color:{color};">{int(r["score"])}</div>'
           f'<div style="font:600 12px/1.6 {FONT};color:{C["muted"]};">/ 100 · {t("note", "grade")} {e(r["grade"])}</div></td>'
           if r.get("score") is not None else
           f'<div style="font:800 32px/1 Sora, {FONT};color:{C["muted"]};">—</div>'
           f'<div style="font:600 12px/1.6 {FONT};color:{C["muted"]};">{t("audit partiel", "partial audit")}</div></td>')
        + f'<td style="padding:14px 18px;font:400 14px/1.7 {FONT};color:{C["ink"]};">'
        f'<strong>{t("Technologie", "Technology")}</strong>{t(" :", ":")} {e(r["cms"]["label"])}<br>'
        f'<strong>Page</strong>{t(" :", ":")} {e(r["page_type"]["label"])}<br>'
        f'<strong>{t("Outil de recommandation", "Recommendation tool")}</strong>{t(" :", ":")} {e(", ".join(v["label"] for v in r.get("vendors") or []) or t("aucun détecté", "none found"))}<br>'
        f'<strong>{t("Blocs trouvés", "Blocks found")}</strong>{t(" :", ":")} {len(r.get("blocks") or [])}</td></tr></table>',
    ]
    if r.get("score") is None:
        parts.append(_p(e(partial_note()), f"font-size:14px;color:{C['muted']};"))
    for kpi in sorted(r["kpis"], key=lambda k: STATUS_ORDER.get(k["status"], 9)):
        label, accent, tint = status_style(kpi["status"])
        about = f'<br><span style="font-size:12px;color:{C["muted"]};">{e(kpi.get("about"))}</span><br>' if kpi.get("about") else ""
        detail = f'<br><span style="color:{C["muted"]};">{e(kpi.get("detail"))}</span>' if kpi.get("detail") else ""
        advice = f'<br><span style="color:{C["ink"]};"><strong>{t("À faire", "To do")}</strong>{t(" :", ":")} {e(kpi.get("advice"))}</span>' if kpi.get("advice") and kpi["status"] != "pass" else ""
        parts.append(
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 12px;">'
            f'<tr><td style="background:{tint};border:1px solid {C["border"]};border-left:4px solid {accent};border-radius:12px;padding:12px 16px;font:400 14px/1.55 {FONT};color:{C["ink"]};">'
            f'<span style="font:700 11px/1 {FONT};letter-spacing:1px;text-transform:uppercase;color:{accent};">{e(label)}</span><br>'
            f'<strong>{e(kpi["label"])}</strong>{about} — {e(kpi["summary"])}{detail}{advice}</td></tr></table>'
        )
    blocks = [b for b in r.get("blocks") or [] if b.get("items")]
    if blocks:
        parts.append(f'<h2 style="margin:24px 0 12px;font:700 19px/1.3 Sora, {FONT};color:{C["ink"]};">{t("Ce que montrent vos blocs", "What your blocks show")}</h2>')
        for block in blocks[:3]:
            names = ", ".join(e(i["name"] or i["url"]) + t(" <em>(indisponible)</em>", " <em>(unavailable)</em>") if i.get("unavailable") else e(i["name"] or i["url"])
                              for i in block["items"][:8])
            parts.append(_p(f'<strong>{site_audit.q(e(block["title"]))}</strong> ({block["item_count"]}){t(" :", ":")} {names}', f"font-size:14px;color:{C['muted']};"))
    for note in r.get("notes") or []:
        parts.append(_p(e(note), f"font-size:13px;color:{C['muted']};"))
    step = r.get("next_step") or {}
    parts.append(f'<h2 style="margin:24px 0 12px;font:700 19px/1.3 Sora, {FONT};color:{C["ink"]};">{t("Et maintenant ?", "What now?")}</h2>')
    parts.append(_p(f'<strong>{e(step.get("title"))}</strong>. {e(step.get("text"))} '
                    + t("Gratuit jusqu'à 50 produits, sans carte bancaire.", "Free up to 50 products, no credit card.")))
    parts.append(_button(step.get("cta") or t("Découvrir LIKYLY", "Discover LIKYLY"), site_link(step.get("href") or "/")))
    anchor = f'<a href="{e(link)}" style="color:{C["blue"]};word-break:break-all;">{e(link)}</a>'
    parts.append(_p(t(f"Votre rapport reste consultable en ligne jusqu'au {until_label(row)} : {anchor}. Une question ? Répondez simplement à cet email.",
                      f"Your report stays online until {until_label(row)}: {anchor}. A question? Just reply to this email."),
                    f"font-size:13px;color:{C['muted']};"))
    return "".join(parts)


def report_text(row: dict[str, Any]) -> str:
    t = site_audit.t
    r = row["result"]
    grade = (t(f" (note {r['grade']})", f" (grade {r['grade']})") if r.get("grade") else "")
    lines = [
        t(f"Audit des recommandations de {r['host']} - {score_label(r)}", f"Recommendation audit of {r['host']} - {score_label(r)}") + grade,
        t(f"Page analysée : {r['final_url']}", f"Page analyzed: {r['final_url']}"),
        t(f"Technologie : {r['cms']['label']} · Page : {r['page_type']['label']}", f"Technology: {r['cms']['label']} · Page: {r['page_type']['label']}"),
        "",
    ]
    for kpi in sorted(r["kpis"], key=lambda k: STATUS_ORDER.get(k["status"], 9)):
        lines.append(f"[{status_style(kpi['status'])[0]}] {kpi['label']} - {kpi['summary']}")
        if kpi.get("detail"):
            lines.append(f"    {kpi['detail']}")
        if kpi.get("advice") and kpi["status"] != "pass":
            lines.append(t(f"    À faire : {kpi['advice']}", f"    To do: {kpi['advice']}"))
    step = r.get("next_step") or {}
    lines += ["", t(f"Et maintenant ? {step.get('title')} : {step.get('text')}", f"What now? {step.get('title')}: {step.get('text')}"), site_link(step.get("href") or "/"), "",
              t(f"Votre rapport en ligne, jusqu'au {until_label(row)} : {report_link(row)}", f"Your report online, until {until_label(row)}: {report_link(row)}"),
              t("Une question ? Répondez à cet email.", "A question? Reply to this email.")]
    return "\n".join(lines)


def build_report_email(row: dict[str, Any], sender: str) -> EmailMessage:
    """The report to the visitor, in the language the report was made in (audit-report.<lang>.html)."""
    lang = report_language(row)
    with site_audit.language(lang):
        return _report_email(row, sender, lang)


def _report_email(row: dict[str, Any], sender: str, lang: str) -> EmailMessage:
    t = site_audit.t
    r = row["result"]
    shell = (TEMPLATES / f"audit-report.{lang}.html").read_text(encoding="utf-8")
    title = t(f"Votre audit : {score_label(r)}", f"Your audit: {score_label(r)}")
    preheader = t(f"{score_label(r).capitalize()} pour {r['host']} - le détail de chaque contrôle et quoi corriger.",
                  f"{score_label(r).capitalize()} for {r['host']} - every check in detail and what to fix.")
    filled = (
        shell.replace("{{ .Body }}", report_body(row))
        .replace("{{ .Title }}", e(title))
        .replace("{{ .Preheader }}", e(preheader))
        .replace("{{ .Host }}", e(r["host"]))
        .replace("{{ .Email }}", e(row["email"]))
    )
    message = EmailMessage()
    message["Subject"] = t(f"Votre audit de recommandations : {score_label(r)} pour {r['host']}", f"Your recommendation audit: {score_label(r)} for {r['host']}")
    message["From"] = formataddr(("LIKYLY", sender))
    message["To"] = row["email"]
    message["Reply-To"] = "contact@likyly.com"
    message.set_content(report_text(row))
    message.add_alternative(filled, subtype="html")
    return message


def build_lead_email(row: dict[str, Any], sender: str) -> EmailMessage:
    r = row["result"]
    message = EmailMessage()
    message["Subject"] = f"[Likyly] Audit : {row['email']} - {r['host']} ({score_label(r)})"
    message["From"] = formataddr(("Likyly", sender))
    message["To"] = os.environ.get("UPGRADE_REQUEST_TO", DEFAULT_RECIPIENT)
    message["Reply-To"] = row["email"]
    message.set_content("\n".join([
        "Nouveau rapport d'audit demandé sur likyly.com/audit.",
        "",
        f"Email : {row['email']}",
        f"Page : {r['final_url']}",
        f"Technologie : {r['cms']['label']} · Page : {r['page_type']['label']}",
        f"Outils détectés : {', '.join(v['label'] for v in r.get('vendors') or []) or 'aucun'}",
        f"Score : {score_label(r)}" + (f" (note {r['grade']})" if r.get("grade") else ""),
        f"Langue du rapport : {report_language(row)}",
        "",
        *(f"[{k['status']}] {k['label']} - {k['summary']}" for k in r["kpis"]),
        "",
        f"Rapport : {report_link(row)}",
        "Répondre à cet email écrit directement au visiteur.",
    ]))
    return message


def send_report(row: dict[str, Any]) -> None:
    """Background task: the report to the visitor, then a heads-up to the team. Never raises -
    the visitor already sees the full report on the page."""
    settings = smtp_settings()
    if settings is None or not settings["sender"]:
        db.mark_site_audit_email_failed(row["token"], "SMTP is not configured (SMTP_HOST / SMTP_USER / SMTP_FROM)")
        log_event("site_audit_email_skipped", reason="smtp_not_configured")
        return
    try:
        deliver(build_report_email(row, settings["sender"]), settings)
    except Exception as error:  # noqa: BLE001
        db.mark_site_audit_email_failed(row["token"], f"{type(error).__name__}: {error}")
        log_event("site_audit_email_failed", error=type(error).__name__)
        return
    db.mark_site_audit_emailed(row["token"])
    log_event("site_audit_emailed", host=row.get("host"))
    try:
        deliver(build_lead_email(row, settings["sender"]), settings)
    except Exception as error:  # noqa: BLE001
        log_event("site_audit_lead_email_failed", error=type(error).__name__)
