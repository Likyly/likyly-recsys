"""The welcome email, sent once per account when its first workspace is created (POST /clients/me)
- whatever the sign-in method: Supabase mails an email/password sign-up its confirmation link, but
sends nothing at all to a Google or GitHub sign-up, so the welcome can't live in Supabase.

Templates: email_templates/welcome.{fr,en}.html, built by likyly-website/emails/build.mjs and copied
here (copy them again after changing them there). Same SMTP settings as upgrade_requests.py.
"""
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Optional

from observability import log_event
from upgrade_requests import deliver, smtp_settings

TEMPLATES = Path(__file__).resolve().parent / "email_templates"
SITE_URL = "https://www.likyly.com"
SUBJECTS = {"fr": "Bienvenue sur Likyly", "en": "Welcome to Likyly"}
TEXT = {
    "fr": (
        "Ton compte Likyly est prêt.\n\n"
        "Installe Likyly en une commande, dans le dossier de ton site :\n\n    npx @likyly/wizard\n\n"
        "Il détecte ton framework, installe le SDK, te connecte à ton compte et relie ton assistant IA "
        "(Claude Code, Cursor, VS Code), qui termine l'intégration.\n"
        "Guide : {site}/docs/installation - SDK et API REST : {site}/sdks\n\n"
        "Une question ? support@likyly.com"
    ),
    "en": (
        "Your Likyly account is ready.\n\n"
        "Install Likyly with one command, in your site's folder:\n\n    npx @likyly/wizard\n\n"
        "It detects your framework, installs the SDK, signs you in and connects your AI assistant "
        "(Claude Code, Cursor, VS Code), which finishes the integration.\n"
        "Guide: {site}/docs/installation - SDKs and REST API: {site}/sdks\n\n"
        "A question? support@likyly.com"
    ),
}


def welcome_language(locale: Optional[str]) -> str:
    """Same rule as the Supabase templates: English only when the account's locale says so."""
    return "en" if locale == "en" else "fr"


def build_welcome(email: str, locale: Optional[str], sender: str) -> EmailMessage:
    lang = welcome_language(locale)
    html = (TEMPLATES / f"welcome.{lang}.html").read_text(encoding="utf-8")
    html = html.replace("{{ .SiteURL }}", SITE_URL).replace("{{ .Email }}", email)
    message = EmailMessage()
    message["Subject"] = SUBJECTS[lang]
    message["From"] = formataddr(("Likyly", sender))
    message["To"] = email
    message["Reply-To"] = "support@likyly.com"
    message.set_content(TEXT[lang].format(site=SITE_URL))
    message.add_alternative(html, subtype="html")
    return message


def send_welcome(email: Optional[str], locale: Optional[str]) -> bool:
    """Background task. Never raises: a missing or failing mail must not break the sign-up."""
    if not email:
        return False
    settings = smtp_settings()
    if settings is None or not settings["sender"]:
        log_event("welcome_email_skipped", reason="smtp_not_configured")
        return False
    try:
        deliver(build_welcome(email, locale, settings["sender"]), settings)
    except Exception as error:  # noqa: BLE001
        log_event("welcome_email_failed", error=type(error).__name__)
        return False
    log_event("welcome_email_sent", lang=welcome_language(locale))
    return True
