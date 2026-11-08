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
        "Ton compte Likyly est prêt. Likyly ajoute des recommandations personnalisées par l'IA à ton site "
        "ou ton application.\n\n"
        "1. INSTALLE LIKYLY AVEC TON ASSISTANT IA (recommandé)\n\n"
        "Dans le dossier de ton projet :\n\n    npx @likyly/wizard\n\n"
        "Il détecte ton framework et installe le bon SDK, te connecte à ton compte (tes clés reviennent "
        "seules dans le terminal) et branche ton assistant IA (Claude Code, Cursor, VS Code) sur le serveur "
        "MCP Likyly, qui importe ton catalogue, crée ton premier emplacement et écrit le code avec toi.\n"
        "Guide : {site}/docs/installation\n\n"
        "2. OU INSTALLE UN SDK TOI-MÊME\n\n"
        "    npm install @likyly/sdk\n    composer require likyly/sdk\n    pip install likyly\n\n"
        "TypeScript, PHP, Python, Java, .NET, Go, Ruby, Rust, Android, Swift et l'API REST : {site}/sdks\n\n"
        "CE QUE FAIT LIKYLY\n\n"
        "- Contenu : similarité et embeddings sémantiques, dès le premier jour, sans historique.\n"
        "- Session : les contenus vus dans la visite orientent la sélection, sans compte ni cookie tiers.\n"
        "- Collaboratif : un modèle entraîné sur tes interactions révèle des affinités.\n"
        "- Hybride : contenu et comportement mélangés ; en mode Automatique, la méthode s'adapte à chaque affichage.\n"
        "- Emplacements et règles métier pilotés depuis la console ou en langage naturel avec ton assistant IA.\n"
        "- Mesure : recommandations servies, impressions, clics, santé de l'intégration ; revenu attribué avec le plan Pro.\n\n"
        "Démo : {site}/demo\n\n"
        "Une question ? Réponds à cet email ou écris à support@likyly.com"
    ),
    "en": (
        "Your Likyly account is ready. Likyly adds AI-powered personalized recommendations to your site "
        "or app.\n\n"
        "1. INSTALL LIKYLY WITH YOUR AI ASSISTANT (recommended)\n\n"
        "In your project's folder:\n\n    npx @likyly/wizard\n\n"
        "It detects your framework and installs the right SDK, signs you in (your keys come back to the "
        "terminal on their own) and plugs your AI assistant (Claude Code, Cursor, VS Code) into the Likyly "
        "MCP server, which imports your catalog, creates your first placement and writes the code with you.\n"
        "Guide: {site}/docs/installation\n\n"
        "2. OR INSTALL AN SDK YOURSELF\n\n"
        "    npm install @likyly/sdk\n    composer require likyly/sdk\n    pip install likyly\n\n"
        "TypeScript, PHP, Python, Java, .NET, Go, Ruby, Rust, Android, Swift and the REST API: {site}/sdks\n\n"
        "WHAT LIKYLY DOES\n\n"
        "- Content: similarity and semantic embeddings, from day one, with no history.\n"
        "- Session: items viewed during the visit steer the selection, no account or third-party cookie.\n"
        "- Collaborative: a model trained on your interactions reveals affinities.\n"
        "- Hybrid: content and behavior blended; in Automatic mode, the method adapts on every display.\n"
        "- Placements and business rules driven from the console or in plain language with your AI assistant.\n"
        "- Measurement: recommendations served, impressions, clicks, integration health; attributed revenue on Pro.\n\n"
        "Demo: {site}/demo\n\n"
        "A question? Reply to this email or write to support@likyly.com"
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
