"""Plan upgrade requests: what the form's answers look like in the notification email, and how that
email leaves. There is no online payment yet, so a request is the whole upgrade flow - the row in
`upgrade_requests` is the source of truth and the email is a notification of it: if SMTP is down or
unconfigured the request is still stored, with the failure recorded next to it.

SMTP is configured through the environment (application/api/.env is loaded by db.py):
    SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD,
    SMTP_FROM (defaults to SMTP_USER), SMTP_SECURITY ("starttls" default, "ssl" or "none"),
    UPGRADE_REQUEST_TO (defaults to contact@likyly.com).
"""
import os
import smtplib
import sys
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from typing import Any, Optional

current_dir = os.path.dirname(os.path.abspath(__file__))
_utils_dir = os.path.abspath(os.path.join(current_dir, "../utils"))
if _utils_dir not in sys.path:
    sys.path.insert(0, _utils_dir)

import db  # noqa: E402
from observability import log_event  # noqa: E402

DEFAULT_RECIPIENT = "contact@likyly.com"
SMTP_TIMEOUT_SECONDS = 15

# A form anyone with an account can submit is also a way to mail the team; a handful a day is
# plenty for a real request (typos, a follow-up) and stops the rest.
UPGRADE_REQUESTS_PER_DAY = 3

SECTOR_LABELS = {
    "ecommerce": "E-commerce", "media": "Média / contenu", "saas": "SaaS / logiciel",
    "marketplace": "Marketplace", "culture_leisure": "Culture / loisirs", "travel": "Voyage / tourisme",
    "other": "Autre",
}
CATALOG_SIZE_LABELS = {
    "lt_1k": "Moins de 1 000 éléments", "1k_10k": "1 000 à 10 000", "10k_100k": "10 000 à 100 000",
    "100k_1m": "100 000 à 1 million", "gt_1m": "Plus d'1 million",
}
INDEXES_LABELS = {"1": "1 catalogue", "2_5": "2 à 5 catalogues", "6_10": "6 à 10 catalogues", "gt_10": "Plus de 10 catalogues"}
VISITORS_LABELS = {
    "lt_10k": "Moins de 10 000 / mois", "10k_100k": "10 000 à 100 000 / mois",
    "100k_1m": "100 000 à 1 million / mois", "gt_1m": "Plus d'1 million / mois", "unknown": "Ne sait pas",
}
SYNC_LABELS = {
    "daily": "Une fois par jour", "hourly": "Toutes les heures", "near_realtime": "Quasi temps réel (webhooks)",
    "unsure": "Pas encore défini",
}
MATURITY_LABELS = {
    "prototype": "Prototype / projet en cours de conception", "preproduction": "Préproduction (pas encore en ligne)",
    "production_early": "En production, audience naissante", "production_established": "En production, trafic établi",
}
GO_LIVE_LABELS = {
    "asap": "Dès que possible", "lt_1m": "Sous 1 mois", "1_3m": "Dans 1 à 3 mois",
    "gt_3m": "Dans plus de 3 mois", "exploring": "Simple exploration",
}
SOLUTION_LABELS = {"none": "Aucune", "in_house": "Solution maison", "third_party": "Solution tierce"}


NOT_PROVIDED = "non renseigné"


def _label(labels: dict[str, str], key: Optional[str]) -> str:
    return labels.get(key, key) if key else NOT_PROVIDED


def render_email(request_id: int, workspace: dict[str, Any], usage: dict[str, Any], account_email: Optional[str], form: dict[str, Any]) -> tuple[str, str]:
    """(subject, plain-text body). Workspace and usage come from our own records, not the form, so
    whoever reads it sees the account's real plan and how close it is to its caps."""
    def cap(used: int, limit: Optional[int]) -> str:
        return f"{used} / {limit}" if limit is not None else f"{used} (illimité)"

    subject = f"[Likyly] Demande plan Pro - {form['company_name']} ({workspace['name']})"
    lines = [
        f"Nouvelle demande de passage au plan Pro (#{request_id})",
        "",
        "COMPTE",
        f"  Workspace : {workspace.get('display_name') or '(nom non défini)'}",
        f"  Identifiant : {workspace['name']} (#{workspace['id']})",
        f"  Email du compte : {account_email or '-'}",
        f"  Plan actuel : {usage['plan']}",
        f"  Catalogues : {cap(usage['index_count'], usage['index_limit'])}",
        f"  Sources de données : {cap(usage['data_source_count'], usage['data_source_limit'])}",
        f"  Produits : {cap(usage['product_count'], usage['product_limit'])}",
        "",
        "ENTREPRISE",
        f"  Nom : {form['company_name']}",
        f"  Site / application : {form.get('website_url') or NOT_PROVIDED}",
        f"  Secteur : {_label(SECTOR_LABELS, form['activity_sector'])}",
        f"  Activité : {form.get('activity_description') or NOT_PROVIDED}",
        f"  Contact : {form['contact_first_name']} {form['contact_last_name']}" + (f" ({form['contact_role']})" if form.get("contact_role") else ""),
        "",
        "BESOIN",
        f"  Taille du catalogue : {_label(CATALOG_SIZE_LABELS, form.get('catalog_size'))}",
        f"  Catalogues souhaités : {_label(INDEXES_LABELS, form.get('indexes_needed'))}",
        f"  Visiteurs mensuels : {_label(VISITORS_LABELS, form.get('monthly_visitors'))}",
        f"  Fréquence de synchronisation : {_label(SYNC_LABELS, form.get('sync_frequency'))}",
        "",
        "MATURITÉ",
        f"  Stade : {_label(MATURITY_LABELS, form.get('maturity'))}",
        f"  Mise en production : {_label(GO_LIVE_LABELS, form.get('go_live'))}",
        f"  Recommandations aujourd'hui : {_label(SOLUTION_LABELS, form.get('current_solution'))}",
    ]
    if form.get("message"):
        lines += ["", "MESSAGE", *(f"  {line}" for line in form["message"].splitlines())]
    lines += ["", "L'utilisateur a accepté d'être recontacté. Répondre à cet email écrit directement à l'adresse du compte."]
    return subject, "\n".join(lines)


def smtp_settings() -> Optional[dict[str, Any]]:
    host = os.environ.get("SMTP_HOST")
    if not host:
        return None
    user = os.environ.get("SMTP_USER")
    return {
        "host": host, "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": user, "password": os.environ.get("SMTP_PASSWORD"),
        "sender": os.environ.get("SMTP_FROM") or user,
        "security": os.environ.get("SMTP_SECURITY", "starttls").lower(),
    }


def build_message(subject: str, body: str, sender: str, reply_to: Optional[str]) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr(("Likyly", sender))
    message["To"] = os.environ.get("UPGRADE_REQUEST_TO", DEFAULT_RECIPIENT)
    address = parseaddr(reply_to or "")[1]
    if address and "@" in address:
        message["Reply-To"] = address
    message.set_content(body)
    return message


def deliver(message: EmailMessage, settings: dict[str, Any]) -> None:
    """The one place that talks SMTP (tests replace it)."""
    if settings["security"] == "ssl":
        server: smtplib.SMTP = smtplib.SMTP_SSL(settings["host"], settings["port"], timeout=SMTP_TIMEOUT_SECONDS)
    else:
        server = smtplib.SMTP(settings["host"], settings["port"], timeout=SMTP_TIMEOUT_SECONDS)
    with server:
        if settings["security"] == "starttls":
            server.starttls()
        if settings["user"] and settings["password"]:
            server.login(settings["user"], settings["password"])
        server.send_message(message)


def notify_team(request_id: int, subject: str, body: str, reply_to: Optional[str]) -> None:
    """Background task: emails the request and records the outcome on its row. Never raises -
    the request is already saved, and a mail failure must not surface as an error to the user."""
    settings = smtp_settings()
    if settings is None or not settings["sender"]:
        db.mark_upgrade_request_email_failed(request_id, "SMTP is not configured (SMTP_HOST / SMTP_USER / SMTP_FROM)")
        log_event("upgrade_request_email_skipped", client_request_id=request_id, reason="smtp_not_configured")
        return
    try:
        deliver(build_message(subject, body, settings["sender"], reply_to), settings)
    except Exception as error:
        db.mark_upgrade_request_email_failed(request_id, f"{type(error).__name__}: {error}")
        log_event("upgrade_request_email_failed", client_request_id=request_id, error=type(error).__name__)
        return
    db.mark_upgrade_request_emailed(request_id)
    log_event("upgrade_request_emailed", client_request_id=request_id)
