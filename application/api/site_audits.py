"""The free recommendation audit of www.likyly.com/audit - no account needed.

    POST /audits                 {url, captcha_token?}  runs the audit, answers the verdicts (teaser)
    POST /audits/{token}/email   {email}                 records the lead, answers the full report and
                                                          emails it (in the background)
    GET  /audits/{token}                                  the report again: full once an email was given
                                                          (the link in the email), teaser before

The audit itself is application/utils/site_audit.py. `token` is unguessable (144 random bits) and is
the only key to a report; a report link works for REPORT_TTL_DAYS after the audit, then answers 410.
Limits per visitor (salted hash of the IP, never the address): a few audits an hour, a few emails a
day, and a cap on report reads - tighter on unknown tokens, so guessing links goes nowhere. When TURNSTILE_SECRET_KEY is set, POST /audits also needs a Cloudflare Turnstile
token (the site sends it); without it, the check is skipped (local development).

Marketing surface of the website, not part of the public API: hidden from /openapi.json.
"""
import hashlib
import html
import os
import re
import sys
import time
from datetime import timedelta
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

current_dir = os.path.dirname(os.path.abspath(__file__))
_utils_dir = os.path.abspath(os.path.join(current_dir, "../utils"))
if _utils_dir not in sys.path:
    sys.path.insert(0, _utils_dir)

import db  # noqa: E402
import site_audit  # noqa: E402
from observability import log_event  # noqa: E402
from upgrade_requests import DEFAULT_RECIPIENT, deliver, smtp_settings  # noqa: E402

router = APIRouter(prefix="/audits", tags=["siteAudit"], include_in_schema=False)

AUDITS_PER_HOUR = 6
AUDITS_PER_DAY = 20
EMAILS_PER_DAY = 5
AUDIT_DEADLINE_SECONDS = 30
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
    url: str = Field(..., min_length=1, max_length=2000)
    captcha_token: Optional[str] = Field(None, max_length=4096)


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
        raise HTTPException(status_code=400, detail="Vérification anti-robot manquante : rechargez la page et réessayez.")
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
        raise HTTPException(status_code=400, detail="La vérification anti-robot a échoué : rechargez la page et réessayez.")


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


def expires_at(row: dict[str, Any]):
    return row["created_at"] + timedelta(days=REPORT_TTL_DAYS)


def find_report(token: str, request: Request) -> dict[str, Any]:
    """The report behind a link, or the reason it can't be shown (429 / 404 / 410)."""
    visitor = ip_hash(request)
    if not reads.allow(visitor):
        raise HTTPException(status_code=429, detail="Trop de consultations depuis cette connexion : réessayez dans une heure.")
    row = db.get_site_audit(token[:64])
    if row is None:
        if not misses.allow(visitor):
            raise HTTPException(status_code=429, detail="Trop de liens invalides depuis cette connexion : réessayez dans une heure.")
        raise HTTPException(status_code=404, detail="Ce rapport n'existe pas.")
    if db.utcnow() >= expires_at(row):
        raise HTTPException(status_code=410, detail=f"Ce rapport a expiré : un lien d'audit reste valable {REPORT_TTL_DAYS} jours. Relancez un audit pour un rapport à jour.")
    return row


def public_report(row: dict[str, Any], full: bool) -> dict[str, Any]:
    result = row["result"]
    body = result if full else site_audit.teaser(result)
    if full:
        body = {**result, "block_count": len(result.get("blocks", []))}
    return {
        "token": row["token"], "created_at": row["created_at"], "expires_at": expires_at(row), "unlocked": full,
        "report_url": f"{site_url()}/audit/r/{row['token']}",
        **body,
    }


def _run(url: str) -> dict[str, Any]:
    deadline = time.monotonic() + AUDIT_DEADLINE_SECONDS
    fetcher = site_audit.SafeFetcher(allow_private=allow_private())

    def fetch(target: str) -> site_audit.Page:
        if time.monotonic() > deadline:
            raise site_audit.AuditError("Le site met trop de temps à répondre.")
        return fetcher.get(target)

    try:
        return site_audit.run_audit(url, fetch, allow_private=allow_private())
    finally:
        fetcher.close()


@router.post("", status_code=201, summary="Run a recommendation audit")
async def create_audit(payload: AuditCreate, request: Request):
    """Runs the audit of one page and answers the verdicts; the explanations come with the email."""
    visitor = ip_hash(request)
    now = db.utcnow()
    if db.count_site_audits_since(visitor, now - timedelta(hours=1)) >= AUDITS_PER_HOUR or db.count_site_audits_since(visitor, now - timedelta(days=1)) >= AUDITS_PER_DAY:
        raise HTTPException(status_code=429, detail="Vous avez lancé beaucoup d'audits : réessayez dans une heure.")
    await verify_captcha(payload.captcha_token, request)
    try:
        result = await run_in_threadpool(_run, payload.url)
    except site_audit.AuditError as error:
        log_event("site_audit_failed", reason=str(error)[:120])
        raise HTTPException(status_code=422, detail=str(error))
    row = db.create_site_audit(result["url"], result.get("host"), result.get("score"), result, visitor)
    log_event("site_audit_run", host=result.get("host"), cms=result["cms"]["id"], page_type=result["page_type"]["id"], score=result["score"])
    return public_report(row, full=False)


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
        raise HTTPException(status_code=422, detail="Cette adresse email n'est pas valide.")
    visitor = ip_hash(request)
    if db.count_site_audits_since(visitor, db.utcnow() - timedelta(days=1), with_email=True) >= EMAILS_PER_DAY:
        raise HTTPException(status_code=429, detail="Plusieurs rapports ont déjà été envoyés aujourd'hui depuis cette connexion : réessayez demain.")
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


def report_body(row: dict[str, Any]) -> str:
    r = row["result"]
    report_link = f"{site_url()}/audit/r/{row['token']}"
    color = GRADE_COLOR.get(r["grade"], C["ink"])
    parts = [
        _p(f'Voici l\'audit des recommandations de <a href="{e(r["final_url"])}" style="color:{C["blue"]};">{e(r["host"])}</a>, '
           f'analysé depuis la page « {e(r.get("item_name") or r["page_type"]["label"])} ».'),
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 24px;background:{C["bg"]};border:1px solid {C["border"]};border-radius:12px;">'
        f'<tr><td width="110" align="center" style="padding:18px 8px;border-right:1px solid {C["border"]};">'
        f'<div style="font:800 40px/1 Sora, {FONT};color:{color};">{int(r["score"])}</div>'
        f'<div style="font:600 12px/1.6 {FONT};color:{C["muted"]};">/ 100 · note {e(r["grade"])}</div></td>'
        f'<td style="padding:14px 18px;font:400 14px/1.7 {FONT};color:{C["ink"]};">'
        f'<strong>Technologie</strong> : {e(r["cms"]["label"])}<br>'
        f'<strong>Page</strong> : {e(r["page_type"]["label"])}<br>'
        f'<strong>Outil de recommandation</strong> : {e(", ".join(v["label"] for v in r.get("vendors") or []) or "aucun détecté")}<br>'
        f'<strong>Blocs trouvés</strong> : {len(r.get("blocks") or [])}</td></tr></table>',
    ]
    for kpi in sorted(r["kpis"], key=lambda k: STATUS_ORDER.get(k["status"], 9)):
        label, accent, tint = STATUS_STYLE.get(kpi["status"], STATUS_STYLE["unknown"])
        about = f'<br><span style="font-size:12px;color:{C["muted"]};">{e(kpi.get("about"))}</span><br>' if kpi.get("about") else ""
        detail = f'<br><span style="color:{C["muted"]};">{e(kpi.get("detail"))}</span>' if kpi.get("detail") else ""
        advice = f'<br><span style="color:{C["ink"]};"><strong>À faire</strong> : {e(kpi.get("advice"))}</span>' if kpi.get("advice") and kpi["status"] != "pass" else ""
        parts.append(
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 12px;">'
            f'<tr><td style="background:{tint};border:1px solid {C["border"]};border-left:4px solid {accent};border-radius:12px;padding:12px 16px;font:400 14px/1.55 {FONT};color:{C["ink"]};">'
            f'<span style="font:700 11px/1 {FONT};letter-spacing:1px;text-transform:uppercase;color:{accent};">{e(label)}</span><br>'
            f'<strong>{e(kpi["label"])}</strong>{about} — {e(kpi["summary"])}{detail}{advice}</td></tr></table>'
        )
    blocks = [b for b in r.get("blocks") or [] if b.get("items")]
    if blocks:
        parts.append(f'<h2 style="margin:24px 0 12px;font:700 19px/1.3 Sora, {FONT};color:{C["ink"]};">Ce que montrent vos blocs</h2>')
        for block in blocks[:3]:
            names = ", ".join(e(i["name"] or i["url"]) + (" <em>(indisponible)</em>" if i.get("unavailable") else "") for i in block["items"][:8])
            parts.append(_p(f'<strong>« {e(block["title"])} »</strong> ({block["item_count"]}) : {names}', f"font-size:14px;color:{C['muted']};"))
    for note in r.get("notes") or []:
        parts.append(_p(e(note), f"font-size:13px;color:{C['muted']};"))
    step = r.get("next_step") or {}
    parts.append(f'<h2 style="margin:24px 0 12px;font:700 19px/1.3 Sora, {FONT};color:{C["ink"]};">Et maintenant ?</h2>')
    parts.append(_p(f'<strong>{e(step.get("title"))}</strong>. {e(step.get("text"))} Gratuit jusqu\'à 50 produits, sans carte bancaire.'))
    parts.append(_button(step.get("cta") or "Découvrir LIKYLY", f'{site_url()}{step.get("href") or "/"}'))
    until = expires_at(row).strftime("%d/%m/%Y")
    parts.append(_p(f'Votre rapport reste consultable en ligne jusqu\'au {until} : <a href="{e(report_link)}" style="color:{C["blue"]};word-break:break-all;">{e(report_link)}</a>. '
                    f'Une question ? Répondez simplement à cet email.', f"font-size:13px;color:{C['muted']};"))
    return "".join(parts)


def report_text(row: dict[str, Any]) -> str:
    r = row["result"]
    lines = [
        f"Audit des recommandations de {r['host']} - {r['score']}/100 (note {r['grade']})",
        f"Page analysée : {r['final_url']}",
        f"Technologie : {r['cms']['label']} · Page : {r['page_type']['label']}",
        "",
    ]
    for kpi in sorted(r["kpis"], key=lambda k: STATUS_ORDER.get(k["status"], 9)):
        lines.append(f"[{STATUS_STYLE.get(kpi['status'], STATUS_STYLE['unknown'])[0]}] {kpi['label']} - {kpi['summary']}")
        if kpi.get("detail"):
            lines.append(f"    {kpi['detail']}")
        if kpi.get("advice") and kpi["status"] != "pass":
            lines.append(f"    À faire : {kpi['advice']}")
    step = r.get("next_step") or {}
    lines += ["", f"Et maintenant ? {step.get('title')} : {step.get('text')}", f"{site_url()}{step.get('href') or '/'}", "",
              f"Votre rapport en ligne, jusqu'au {expires_at(row).strftime('%d/%m/%Y')} : {site_url()}/audit/r/{row['token']}", "Une question ? Répondez à cet email."]
    return "\n".join(lines)


def build_report_email(row: dict[str, Any], sender: str) -> EmailMessage:
    r = row["result"]
    shell = (TEMPLATES / "audit-report.fr.html").read_text(encoding="utf-8")
    title = f"Votre audit : {int(r['score'])}/100"
    preheader = f"Score {int(r['score'])}/100 pour {r['host']} - le détail de chaque contrôle et quoi corriger."
    filled = (
        shell.replace("{{ .Body }}", report_body(row))
        .replace("{{ .Title }}", e(title))
        .replace("{{ .Preheader }}", e(preheader))
        .replace("{{ .Host }}", e(r["host"]))
        .replace("{{ .Email }}", e(row["email"]))
    )
    message = EmailMessage()
    message["Subject"] = f"Votre audit de recommandations : {int(r['score'])}/100 pour {r['host']}"
    message["From"] = formataddr(("LIKYLY", sender))
    message["To"] = row["email"]
    message["Reply-To"] = "contact@likyly.com"
    message.set_content(report_text(row))
    message.add_alternative(filled, subtype="html")
    return message


def build_lead_email(row: dict[str, Any], sender: str) -> EmailMessage:
    r = row["result"]
    message = EmailMessage()
    message["Subject"] = f"[Likyly] Audit : {row['email']} - {r['host']} ({r['score']}/100)"
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
        f"Score : {r['score']}/100 (note {r['grade']})",
        "",
        *(f"[{k['status']}] {k['label']} - {k['summary']}" for k in r["kpis"]),
        "",
        f"Rapport : {site_url()}/audit/r/{row['token']}",
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
