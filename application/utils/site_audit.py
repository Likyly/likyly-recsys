"""The free recommendation audit behind www.likyly.com/audit: a visitor gives the URL of a page of
their site (ideally a product, article, book or event page), and this reads it the way a crawler
does - plain HTML, no browser - to say what recommends what on it, and how well.

What it reads (at most a handful of requests, all public pages):
  - the submitted page, and the site's home page;
  - on Shopify, the store's own public recommendations endpoint for the product (what the
    theme's "You may also like" shows), which tells availability too;
  - /.well-known/ucp, the Universal Commerce Protocol's discovery profile (AI shopping agents).

What it finds: the CMS, the page type (schema.org JSON-LD first), the recommendation tools
loaded (Nosto, Clerk, Shopify apps…), and the recommendation blocks - from each CMS's native
markup, from their headings ("Vous aimerez aussi", "Related products"…), or from a third-party
tool's placeholder that only fills in with JavaScript. Then a grid of KPIs (self-recommendation,
duplicates, out-of-stock items, personalization, home page, block size, structured data, agent
readiness) and a score out of 100.

Blocks rendered only by JavaScript can't be read without a browser: they are reported as found
but unreadable, and the KPIs that need their content say "unknown" rather than guess.

Every fetch goes through SafeFetcher: http(s) on ports 80/443 only, every hostname resolved and
refused if any address is private/loopback/link-local/reserved (also on every redirect hop), and
the connection pinned to the address that was checked (no DNS rebinding between check and
connect). Bodies are capped. The text is French: the site is.
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup, Tag

USER_AGENT = "Mozilla/5.0 (compatible; LikylyAudit/1.0; +https://www.likyly.com/audit)"
TIMEOUT_SECONDS = 8.0
MAX_BODY_BYTES = 3 * 1024 * 1024
MAX_REDIRECTS = 5


class AuditError(Exception):
    """A reason the audit can't run at all, worded for the visitor (shown as is). `blocked`: the site
    turns robots away - the visitor can still audit the page from their own browser."""

    def __init__(self, message: str, blocked: bool = False):
        super().__init__(message)
        self.blocked = blocked


# The page a site puts in front of robots it doesn't let through (Cloudflare, DataDome, Akamai,
# PerimeterX, Imperva...). The audit stops there: it never tries to get past such a page.
CHALLENGE_TITLE_RE = re.compile(
    r"<title>\s*(just a moment|attention required|client challenge|access denied|pardon our interruption|"
    r"are you a (robot|human)|vérification|un instant|security check|bot verification|one more step)",
    re.IGNORECASE,
)
# The protection's own scripts: a challenge page loads them, but so do many real pages behind the
# same protection (Le Monde loads /_fs-ch-…/init.js, Cloudflare adds challenge-platform to normal
# pages). They mean a challenge only on a page with almost no text of its own.
CHALLENGE_MARKER_RE = re.compile(
    r"challenge-platform|cf-chl-|_fs-ch-|px-captcha|captcha-delivery\.com|geo\.captcha|/_Incapsula_Resource|hcaptcha\.com/1/api|"
    r"recaptcha/(api|enterprise)\.js\?render=explicit",
    re.IGNORECASE,
)
_NOT_TEXT_RE = re.compile(r"<(script|style|noscript|template|svg)\b.*?</\1\s*>|<[^>]+>", re.IGNORECASE | re.DOTALL)
CHALLENGE_MAX_TEXT = 1500


def is_challenge(html: str) -> bool:
    head = html[:200_000]
    if CHALLENGE_TITLE_RE.search(head):
        return True
    if not CHALLENGE_MARKER_RE.search(head):
        return False
    return len(" ".join(_NOT_TEXT_RE.sub(" ", html[:2_000_000]).split())) < CHALLENGE_MAX_TEXT


BLOCKED_HINT = " Vous pouvez l'auditer depuis votre navigateur."


# --------------------------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------------------------

@dataclass
class Page:
    url: str  # final URL, after redirects
    status: int
    text: str
    content_type: str = ""
    challenge: bool = False  # the browser was shown an anti-robot page (renderer)


def normalize_input_url(raw: str, allow_private: bool = False) -> str:
    """What the visitor typed -> an absolute http(s) URL, or AuditError."""
    value = (raw or "").strip()
    if not value:
        raise AuditError("Indiquez l'adresse d'une page de votre site.")
    if len(value) > 2000:
        raise AuditError("Cette adresse est trop longue.")
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        value = "https://" + value
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname or ("." not in parts.hostname and not allow_private):
        raise AuditError("Cette adresse n'est pas valide : collez l'URL complète d'une page, par exemple https://www.maboutique.fr/produit/…")
    if parts.username or parts.password:
        raise AuditError("Cette adresse n'est pas valide.")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


class SafeFetcher:
    """GETs public pages only - see the module docstring. `allow_private` is for local
    development against a sandbox store (SITE_AUDIT_ALLOW_PRIVATE=1), never production."""

    def __init__(self, allow_private: bool = False, client: Optional[httpx.Client] = None):
        self.allow_private = allow_private
        self.client = client or httpx.Client(timeout=TIMEOUT_SECONDS, verify=True, follow_redirects=False)

    def close(self) -> None:
        self.client.close()

    def _resolve(self, host: str, port: int) -> str:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror:
            raise AuditError(f"Le site {host} est introuvable : vérifiez l'adresse.")
        # IPv4 first: a server without IPv6 connectivity would otherwise time out on AAAA records.
        addresses = sorted(dict.fromkeys(info[4][0] for info in infos), key=lambda a: ":" in a)
        if not addresses:
            raise AuditError(f"Le site {host} est introuvable : vérifiez l'adresse.")
        if not self.allow_private and not all(_is_public(a) for a in addresses):
            raise AuditError("Cette adresse ne mène pas à un site public.")
        return addresses[0]

    def get(self, url: str, accept: str = "text/html,application/xhtml+xml") -> Page:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            parts = urlsplit(current)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                raise AuditError("La page redirige vers une adresse qui n'est pas un site web.")
            port = parts.port or (443 if parts.scheme == "https" else 80)
            if port not in (80, 443) and not self.allow_private:
                raise AuditError("Seuls les sites web standards (ports 80 et 443) peuvent être analysés.")
            host = parts.hostname
            address = self._resolve(host, port)
            pinned_host = f"[{address}]" if ":" in address else address
            netloc = pinned_host if parts.port is None else f"{pinned_host}:{parts.port}"
            target = urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))
            headers = {
                "Host": parts.netloc.split("@")[-1],
                "User-Agent": USER_AGENT,
                "Accept": accept,
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            }
            extensions = {"sni_hostname": host} if parts.scheme == "https" else {}
            try:
                with self.client.stream("GET", target, headers=headers, extensions=extensions) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise AuditError("La page redirige sans dire où.")
                        current = urljoin(current, location)
                        continue
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_BODY_BYTES:
                            break
                    del body[MAX_BODY_BYTES:]
                    encoding = response.encoding or "utf-8"
                    try:
                        text = bytes(body).decode(encoding, errors="replace")
                    except LookupError:
                        text = bytes(body).decode("utf-8", errors="replace")
                    return Page(url=current, status=response.status_code, text=text, content_type=response.headers.get("content-type", ""))
            except httpx.TimeoutException:
                raise AuditError(f"Le site {host} ne répond pas à notre robot d'analyse : il est trop lent, ou il ignore les visites automatiques." + BLOCKED_HINT, blocked=True)
            except httpx.HTTPError:
                raise AuditError(f"Impossible de joindre {host} : le site est hors ligne, ou il coupe la connexion aux visites automatiques." + BLOCKED_HINT, blocked=True)
        raise AuditError("La page redirige trop de fois.")


Fetch = Callable[[str], Page]
Render = Callable[[str], Page]  # the page as a browser shows it, after JavaScript (renderer/server.py)


class RendererClient:
    """The audit's browser: a separate container (renderer/server.py), with its own memory cap and an
    egress limited to public websites. Not configured (AUDIT_RENDERER_URL unset) = no rendering."""

    def __init__(self, base_url: str, token: Optional[str] = None, client: Optional[httpx.Client] = None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.client = client or httpx.Client()

    def close(self) -> None:
        self.client.close()

    def render(self, url: str, timeout_ms: int = 15000) -> Page:
        if not url.startswith(("http://", "https://")):
            raise AuditError("Adresse invalide.")
        headers = {"X-Renderer-Token": self.token} if self.token else {}
        try:
            answer = self.client.post(f"{self.base_url}/render", json={"url": url, "timeout_ms": timeout_ms}, headers=headers, timeout=timeout_ms / 1000 + 8)
        except httpx.HTTPError:
            raise AuditError("le navigateur d'audit ne répond pas")
        if answer.status_code == 503:
            raise AuditError("le navigateur d'audit est occupé par une autre analyse")
        if answer.status_code == 504:
            raise AuditError("la page met trop de temps à s'afficher")
        if answer.status_code != 200:
            raise AuditError("le navigateur d'audit n'a pas pu ouvrir la page")
        body = answer.json()
        return Page(url=body["url"], status=int(body["status"]), text=body["html"], content_type="text/html", challenge=bool(body.get("challenge")))


# --------------------------------------------------------------------------------------------
# Detection tables
# --------------------------------------------------------------------------------------------

CMS_LABELS = {
    "shopify": "Shopify", "woocommerce": "WooCommerce", "wordpress": "WordPress", "magento": "Magento / Adobe Commerce",
    "prestashop": "PrestaShop", "drupal": "Drupal", "bigcommerce": "BigCommerce", "shopware": "Shopware",
    "wix": "Wix", "squarespace": "Squarespace", "webflow": "Webflow", "joomla": "Joomla", "ghost": "Ghost",
    "nextjs": "Site sur mesure (Next.js)", "nuxt": "Site sur mesure (Nuxt)", "unknown": "Non identifié",
}

# (vendor id, label, personalizes per visitor?, patterns searched in the raw HTML)
VENDORS: list[tuple[str, str, bool, tuple[str, ...]]] = [
    ("inhouse", "Moteur de personnalisation du site", True, ("p13n-", "p13n_", "personalized-recommendations", "personalised-recommendations")),
    ("likyly", "LIKYLY", True, ("api.likyly.com", "cdn.likyly.com", "likyly-recommendations", "@likyly/")),
    ("nosto", "Nosto", True, ("connect.nosto.com", "nosto_element", "nostojs")),
    ("clerk", "Clerk.io", True, ("cdn.clerk.io", "clerk.io/", 'class="clerk')),
    ("dynamic_yield", "Dynamic Yield", True, ("cdn.dynamicyield.com", "dy-api.com")),
    ("algolia_recommend", "Algolia Recommend", True, ("@algolia/recommend", "recommend-js")),
    ("bloomreach", "Bloomreach", True, ("exponea.com", "bloomreach.com", "brtrk")),
    ("salesforce", "Salesforce Commerce (Einstein)", True, ("cquotient.com", "evgnet.com")),
    ("klevu", "Klevu", True, ("js.klevu.com", "klevu.com/")),
    ("recombee", "Recombee", True, ("recombee.com",)),
    ("barilliance", "Barilliance", True, ("barilliance.net",)),
    ("rebuy", "Rebuy (Shopify)", True, ("rebuyengine.com",)),
    ("limespot", "LimeSpot (Shopify)", True, ("limespot.com", "personalizer.io")),
    ("fbt", "Frequently Bought Together (app)", False, ("frequently-bought-together", "codeblackbelt")),
    ("also_bought", "Also Bought (app)", False, ("also-bought", "alsobought")),
    ("outbrain", "Outbrain (contenus sponsorisés)", False, ("widgets.outbrain.com",)),
    ("taboola", "Taboola (contenus sponsorisés)", False, ("cdn.taboola.com",)),
]

# Headings that announce a recommendation block, by family (French and English). What a block's
# title says about how it is chosen:
#   behavior   what other customers did with this item ("ont aussi acheté", "bought together")
#   complement what goes with it ("complétez le look", "get everything you need") - a cross-sell
#   popular    what everybody reads or buys ("les plus lus", "most read", "meilleures ventes")
#   personal   what this visitor did ("vus récemment") - adapts to each visitor by nature
#   similar    everything else that announces recommendations - related to the item shown
# (a title saying "pour vous" is kept as a claim: what a block really does is checked by opening a
# second item page and reloading the page - see compare_blocks)
BEHAVIOR_HEADING_RE = re.compile(
    r"ont (aussi|également) (aimé|acheté|lu|vu|consulté|regardé|réservé|écouté)|clients (ayant|qui ont) (consulté|acheté|vu|aimé)|clients achètent|achètent après|"
    r"(souvent|fréquemment) achetés?|achetés? ensemble|lecteurs ont aussi|also (bought|liked|viewed|read|enjoyed|purchased)|customers (who|also)|bought together|"
    r"(people|readers|shoppers|others|fans) (also|who)|what (other )?(customers|people) (buy|bought)|you might also need",
    re.IGNORECASE)
COMPLEMENT_HEADING_RE = re.compile(
    r"compl(é|e)tez|complète(z)? (le|votre|ton)|complete (the|your) (look|set|outfit|purchase)|pairs? (well )?with|get everything you need|"
    r"accessoires? (recommandés|compatibles|indispensables|associés)|style it with|shop the look|à associer|vous aurez aussi besoin|you'?ll (also )?need|goes well with|wear it with",
    re.IGNORECASE)
POPULAR_HEADING_RE = re.compile(
    r"most (read|viewed|popular|shared|watched)|les plus (lus|vus|populaires|partagés|consultés|vendus)|trending|tendances?\b|top stories|à la une|"
    r"best[- ]?sellers?|meilleures ventes|top ventes|popular (now|right now|products|items|stories)|populaires|nos coups de c(œ|oe)ur",
    re.IGNORECASE)
PERSONAL_HEADING_RE = re.compile(
    r"récemment (consultés|vus)|derniers? (articles )?(consultés|vus)|vus récemment|recently viewed|inspired by your|inspiré de votre|"
    r"based on your (browsing|history|recent)|d'après votre (historique|navigation)|your browsing history",
    re.IGNORECASE)
CLAIMS_PERSONAL_RE = re.compile(
    r"^\W*(rien que |juste |spécialement |sélection )?pour (vous|toi)\W*$|(recommandés?|sélectionnés?|choisis|selected|picked|recommended|chosen) (rien que )?(pour|for) (vous|toi|you)|"
    r"^\W*(just )?for you\W*$|just for you|spécialement pour (vous|toi)", re.IGNORECASE)
SIMILAR_HEADING_RE = re.compile(
    r"vous aimerez (aussi|peut-être|également)|vous (pourriez|devriez) aimer|tu (aimeras|vas aimer|pourrais aimer)|ça pourrait (vous|te) plaire|"
    r"produits? (similaires|apparentés|associés|liés|recommandés|complémentaires|alternatifs)|articles? (similaires|recommandés|liés|associés)|"
    r"(à|a) lire (aussi|également)|lire (aussi|ensuite|également)|sur le même (thème|sujet)|dans la même (catégorie|collection|série|gamme|rubrique)|"
    r"du même (auteur|artiste|rayon|univers|thème|créateur)|autres? (livres|produits|articles|œuvres|titres|albums|spectacles|événements|modèles) (de|du|des|classés|dans)|"
    r"(autres|d'autres) (produits|articles|livres|spectacles|événements)|que (lire|voir|écouter) (après|ensuite)|nous vous (conseillons|recommandons)|"
    r"nos recommandations|suggestions?( pour| de| d')|comparez|découvrez (aussi|également)|à découvrir (aussi|également)|vous avez aimé|"
    r"you (may|might|will|could|'ll) (also )?(like|love|enjoy)|related (products|items|articles|posts|stories|books|events|content|coverage|reading)|"
    r"similar (products|items|articles|books|events|styles)|compare (with )?similar|more (like this|to explore|on this|about this|from this|in this)|"
    r"(read|watch|listen) next|explore more|discover more|other (products|items) (from|in|by)|recommended (products|items|reading|for you)|products related to",
    re.IGNORECASE)
HEADING_RE = re.compile("|".join(r.pattern for r in (BEHAVIOR_HEADING_RE, COMPLEMENT_HEADING_RE, POPULAR_HEADING_RE, PERSONAL_HEADING_RE, CLAIMS_PERSONAL_RE, SIMILAR_HEADING_RE)), re.IGNORECASE)
# Titles that look like the above but announce tags, topics or searches, not items.
NOISE_HEADING_RE = re.compile(r"related (topics|tags|searches|categories|links)|(on )?these topics|more on this topic\b|sujets? (associés|liés)|mots[- ]clés|tags?\b|catégories (associées|liées)|recherches (associées|liées)|more about (us|the)", re.IGNORECASE)


# Lists built on a shared trait (same collection, author, section): identical on another member of
# the group by design - not a fixed collection.
GROUPED_LIST_RE = re.compile(r"même (collection|auteur|série|rubrique|catégorie|gamme|artiste|marque)|du même|autres? \w+ (de|du|des|classés)|more from|same (collection|author|series|brand)", re.IGNORECASE)


def heading_kind(text: str) -> str:
    for kind, regex in (("behavior", BEHAVIOR_HEADING_RE), ("complement", COMPLEMENT_HEADING_RE), ("personal", PERSONAL_HEADING_RE),
                        ("popular", POPULAR_HEADING_RE)):
        if regex.search(text):
            return kind
    return "similar"


# CMS-native recommendation containers: (platform, css selector, title used when it has no heading, kind)
# kind: "similar" (related to the item: same category, content, platform's "related"),
# "behavior" (what other customers bought), "manual" (picked by hand in the back office).
NATIVE_BLOCKS: list[tuple[str, str, str, str]] = [
    ("woocommerce", "section.related.products, div.related.products, .wp-block-woocommerce-related-products", "Produits apparentés (WooCommerce)", "similar"),
    ("woocommerce", "section.up-sells, div.up-sells, .upsells.products", "Produits suggérés (WooCommerce)", "manual"),
    ("woocommerce", ".cross-sells", "Ventes croisées (WooCommerce)", "manual"),
    ("magento", ".block.related", "Produits associés (Magento)", "manual"),
    ("magento", ".block.upsell", "Produits suggérés (Magento)", "manual"),
    ("magento", ".block.crosssell", "Ventes croisées (Magento)", "manual"),
    ("prestashop", ".product-accessories", "Accessoires (PrestaShop)", "manual"),
    ("prestashop", "#products-in-same-category, .products-in-same-category", "Produits de la même catégorie (PrestaShop)", "similar"),
    ("prestashop", ".crossselling-product, #crossselling", "Les clients ont aussi acheté (PrestaShop)", "behavior"),
    ("shopify", "product-recommendations, .product-recommendations, [id*='product-recommendations']", "Recommandations Shopify", "similar"),
    ("shopify", "complementary-products, .complementary-products", "Produits complémentaires (Shopify)", "manual"),
    ("wordpress", "#jp-relatedposts, .jp-relatedposts", "Articles similaires (Jetpack)", "similar"),
    ("wordpress", ".yarpp, .yarpp-related", "Articles similaires (YARPP)", "similar"),
    ("wordpress", ".crp_related", "Articles similaires (Contextual Related Posts)", "similar"),
    ("bigcommerce", "[data-similar-by-views]", "Les clients ont aussi consulté (BigCommerce)", "behavior"),
    ("bigcommerce", ".productView-related, #tab-related", "Produits associés (BigCommerce)", "manual"),
]

VENDOR_PLACEHOLDERS = ".nosto_element, [id^='nosto-'], .clerk, span.clerk, [data-dy-embedded-object], .rebuy-widget, [data-rebuy-id], .limespot-recommendation-box, [data-likyly-placement], .likyly-recommendations"

NON_ITEM_PATH = re.compile(
    r"(^|/)(cart|panier|checkout|commande|account|mon-compte|my-account|login|connexion|register|wishlist|compare|search|recherche|"
    r"tag|tags|topics?|bestsellers|best-sellers|author|authors|auteur|auteurs|feed|wp-admin|wp-login|cdn-cgi|product-reviews|reviews|avis|help|aide|faq|contact|stores?|magasins?)(/|$)|"
    r"add-to-cart|addtocart|\.(jpe?g|png|webp|gif|svg|css|js|pdf)$",
    re.IGNORECASE,
)
MORE_LINK_RE = re.compile(r"^(voir|tout voir|afficher|découvrir|see|view|show|shop)?\s*(plus|tout|tous|toutes|la suite|all|more)?\s*[›»→>]*$", re.IGNORECASE)
UI_LABEL_RE = re.compile(r"^(coup d.?(œ|oe)il|aperçu( rapide)?|quick ?(view|shop|buy)|ajouter( au panier)?|add to (cart|bag)|choisir( les)? options?|voir (le produit|les options)|acheter|buy( now)?|shop now|précédent|suivant|previous|next)$", re.IGNORECASE)
OUT_OF_STOCK_RE = re.compile(r"rupture de stock|épuisé|indisponible|sold out|out of stock|plus disponible", re.IGNORECASE)


# --------------------------------------------------------------------------------------------
# Page analysis
# --------------------------------------------------------------------------------------------

@dataclass
class Item:
    key: str
    url: str
    name: str = ""
    unavailable: bool = False


@dataclass
class Block:
    title: str
    source: str  # "native" | "heading" | "vendor" | "shopify_api"
    kind: str  # "rule" | "manual" | "auto" | "vendor" | "unknown"
    items: list[Item] = field(default_factory=list)
    js_only: bool = False
    platform: str = ""
    claims_personal: bool = False  # titled "Pour vous" whatever actually chooses it
    browser_only: bool = False  # absent from the HTML, drawn by JavaScript (seen in the rendered page)
    sponsored: bool = False  # labelled "Sponsorisé" / "Sponsored": paid placements
    # What was seen, not assumed: "varies" (other products on another item page), "fixed" (the same
    # products there), "random" (other products on a second load of the same page), None (not compared).
    verified: Optional[str] = None
    evidence: str = ""
    element: Optional[Tag] = field(default=None, repr=False, compare=False)

    def public(self) -> dict[str, Any]:
        return {
            "title": self.title, "source": self.source, "kind": self.kind, "js_only": self.js_only, "claims_personal": self.claims_personal,
            "browser_only": self.browser_only, "sponsored": self.sponsored, "verified": self.verified, "evidence": self.evidence,
            "items": [{"name": i.name, "url": i.url, "unavailable": i.unavailable} for i in self.items[:24]],
            "item_count": len(self.items),
        }


# Marketplaces put an item id in the path and decorate it (slug, /ref=… tracking): the id is the item.
ITEM_ID_PATTERNS = [
    (re.compile(r"/(?:dp|gp/product|gp/aw/d)/([a-z0-9]{10})(?:[/?]|$)", re.IGNORECASE), "/dp/{}"),  # Amazon ASIN
    (re.compile(r"/itm/(?:[^/]+/)?(\d{9,})"), "/itm/{}"),  # eBay
    (re.compile(r"/listing/(\d+)"), "/listing/{}"),  # Etsy
    (re.compile(r"/ip/(?:[^/]+/)?(\d{5,})"), "/ip/{}"),  # Walmart
    (re.compile(r"/site/[^/]+/(\d+)\.p"), "/site/{}.p"),  # Best Buy
    (re.compile(r"/(?:[^/]+/)?p/[^/]*?-?(\d{6,})(?:\.html?)?$"), "/p/{}"),  # many retailers: /p/slug-123456
]


def item_key(url: str) -> str:
    """Two links to the same item compare equal: no scheme/host/query/fragment/trailing slash or
    /ref=… tracking segment; Shopify's /collections/x/products/y is /products/y; a marketplace
    item id (Amazon, eBay, Etsy, Walmart…) stands for the whole link."""
    path = urlsplit(url).path
    for regex, template in ITEM_ID_PATTERNS:
        match = regex.search(path)
        if match:
            return template.format(match.group(1).upper() if template.startswith("/dp/") else match.group(1))
    path = re.sub(r"/ref=[^/]*", "", path.lower()).rstrip("/") or "/"
    shopify = re.search(r"/products/([^/]+)$", path)
    if shopify:
        return "/products/" + shopify.group(1)
    return path


def _text(node: Tag) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def _json_ld(soup: BeautifulSoup) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for n in node:
                walk(n)
        elif isinstance(node, dict):
            found.append(node)
            for key in ("@graph", "mainEntity", "itemListElement"):
                if key in node:
                    walk(node[key])

    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        try:
            walk(json.loads(script.string or script.get_text() or ""))
        except (ValueError, TypeError):
            continue
    return found


def _types(node: dict[str, Any]) -> set[str]:
    value = node.get("@type")
    values = value if isinstance(value, list) else [value]
    return {str(v) for v in values if v}


GENERIC_LD_TYPES = {"WebPage", "WebSite", "Organization", "BreadcrumbList", "SearchAction", "ItemPage", "CollectionPage", "ImageObject", "ListItem", "SiteNavigationElement", "Person", "LocalBusiness", "PerformingArtsTheater", "Place", "PostalAddress", "ReadAction", "EntryPoint"}
EVENT_TYPES = {"Event", "MusicEvent", "TheaterEvent", "SportsEvent", "ComedyEvent", "DanceEvent", "Festival", "ScreeningEvent", "ExhibitionEvent", "BusinessEvent", "EducationEvent", "SocialEvent", "LiteraryEvent", "ChildrensEvent"}
ARTICLE_TYPES = {"Article", "NewsArticle", "BlogPosting", "Report", "ScholarlyArticle", "TechArticle", "AnalysisNewsArticle", "OpinionNewsArticle", "ReviewNewsArticle"}
ADD_TO_CART_RE = re.compile(r"id=[\"']add-?to-?cart|name=[\"']add-to-cart|data-action=[\"']add-to-cart|>\s*(ajouter au panier|add to (cart|basket|bag)|acheter maintenant|buy now)\s*<", re.IGNORECASE)
TICKETING_RE = re.compile(r"billetterie|représentations?|réserv(?:er|ez) (?:vos|des) places|acheter (?:vos|des) billets|tickets?\b|spectacle", re.IGNORECASE)
PAGE_TYPE_LABELS = {"product": "Fiche produit", "book": "Fiche livre", "event": "Événement / billetterie", "article": "Article", "home": "Page d'accueil", "category": "Page catégorie", "other": "Autre page"}


@dataclass
class PageFacts:
    url: str
    canonical_key: str
    page_type: str
    item_name: str
    structured: dict[str, Any]
    cms: str
    vendors: list[dict[str, Any]]
    blocks: list[Block]
    shopify_product_id: Optional[str] = None
    lang: str = ""
    js_reco_signals: bool = False  # the page's scripts talk about recommendations it renders itself
    text_chars: int = 0
    rendered: bool = False  # read in a browser (ours, or the visitor's), not only from the HTML
    render_failed: bool = False  # it needed a browser, and ours couldn't show it: what isn't in the HTML wasn't seen


def detect_cms(html: str, soup: BeautifulSoup) -> str:
    generator = " ".join(m.get("content", "") for m in soup.find_all("meta", attrs={"name": re.compile("^generator$", re.I)})).lower()
    low = html.lower()
    if "cdn.shopify.com" in low or "shopify.theme" in low or ".myshopify.com" in low:
        return "shopify"
    if "woocommerce" in generator or "/plugins/woocommerce/" in low or re.search(r'class="[^"]*\bwoocommerce\b', low):
        return "woocommerce"
    if "prestashop" in generator or re.search(r"var prestashop\s*=", low) or "/modules/ps_" in low:
        return "prestashop"
    if "magento" in generator or "mage/cookies" in low or "data-mage-init" in low or "magento_" in low or "/static/version" in low:
        return "magento"
    if "drupal" in generator or "drupalsettings" in low or "drupal.settings" in low:
        return "drupal"
    if "wordpress" in generator or "/wp-content/" in low or "/wp-includes/" in low:
        return "wordpress"
    if "cdn11.bigcommerce.com" in low or "bigcommerce" in generator:
        return "bigcommerce"
    if "shopware" in generator or "/bundles/storefront/" in low:
        return "shopware"
    if "wix.com" in generator or "static.wixstatic.com" in low:
        return "wix"
    if "squarespace" in generator or "static1.squarespace.com" in low:
        return "squarespace"
    if "data-wf-site" in low or "webflow" in generator:
        return "webflow"
    if "joomla" in generator:
        return "joomla"
    if "ghost" in generator:
        return "ghost"
    if "__next_data__" in low or "/_next/static/" in low:
        return "nextjs"
    if "__nuxt" in low or "/_nuxt/" in low:
        return "nuxt"
    return "unknown"


def detect_vendors(html: str) -> list[dict[str, Any]]:
    low = html.lower()
    return [
        {"id": vid, "label": label, "personalized": personal}
        for vid, label, personal, patterns in VENDORS
        if any(p.lower() in low for p in patterns)
    ]


def detect_page_type(soup: BeautifulSoup, html: str, url: str, nodes: list[dict[str, Any]], cms: str) -> tuple[str, str, dict[str, Any]]:
    """(page_type, item name, structured-data facts)."""
    product = next((n for n in nodes if _types(n) & {"Product", "ProductGroup", "IndividualProduct", "ProductModel"}), None)
    book = next((n for n in nodes if "Book" in _types(n)), None)
    event = next((n for n in nodes if _types(n) & EVENT_TYPES), None)
    article = next((n for n in nodes if _types(n) & ARTICLE_TYPES), None)
    chosen, page_type = (book, "book") if book else (product, "product") if product else (event, "event") if event else (article, "article") if article else (None, "")

    structured: dict[str, Any] = {"json_ld_types": sorted({t for n in nodes for t in _types(n)})[:20]}
    name = ""
    if chosen is not None:
        name = str(chosen.get("name") or chosen.get("headline") or "")[:200]
        offers = chosen.get("offers")
        offer = offers[0] if isinstance(offers, list) and offers else offers if isinstance(offers, dict) else None
        if isinstance(offer, dict) and offer.get("@type") == "AggregateOffer" and isinstance(offer.get("offers"), list) and offer["offers"]:
            offer = offer["offers"][0]
        structured.update({
            "type": page_type,
            "has_name": bool(name),
            "has_image": bool(chosen.get("image")),
            "has_offer": isinstance(offer, dict),
            "has_price": isinstance(offer, dict) and (offer.get("price") is not None or offer.get("lowPrice") is not None),
            "has_availability": isinstance(offer, dict) and bool(offer.get("availability")),
            "has_identifier": any(chosen.get(k) for k in ("sku", "gtin", "gtin13", "gtin12", "gtin8", "mpn", "isbn", "productID")),
            "has_brand_or_author": bool(chosen.get("brand") or chosen.get("author") or chosen.get("organizer") or chosen.get("publisher")),
            "has_date": bool(chosen.get("startDate") or chosen.get("datePublished")),
            "related_in_markup": bool(chosen.get("isRelatedTo") or chosen.get("isSimilarTo")),
        })

    if not page_type:
        og_meta = soup.find("meta", attrs={"property": "og:type"})
        og_type = (og_meta.get("content", "") if og_meta else "").lower()
        body = soup.find("body")
        body_class = " ".join(body.get("class", [])) if body else ""
        body_id = body.get("id", "") if body else ""
        low = html.lower()
        if og_type.startswith("product") or "single-product" in body_class or "catalog-product-view" in body_class or body_id == "product" or '"pagetype":"product"' in low.replace(" ", ""):
            page_type = "product"
        elif og_type == "book":
            page_type = "book"
        elif og_type == "article" or "single-post" in body_class:
            page_type = "article"
        elif urlsplit(url).path.strip("/") == "":
            page_type = "home"
        elif "archive" in body_class or "catalog-category-view" in body_class or body_id == "category" or "/collections/" in url:
            page_type = "category"
        elif ADD_TO_CART_RE.search(html) or soup.find("meta", attrs={"property": "product:price:amount"}):
            page_type = "product"  # an add-to-cart button or a product price: a product page whose markup doesn't say so
        elif len(TICKETING_RE.findall(low)) >= 5:
            page_type = "event"  # a show or event page whose markup doesn't say so (ticketing words all over it)
        else:
            page_type = "other"
        if not name:
            og_title = soup.find("meta", attrs={"property": "og:title"})
            heading = soup.find("h1")
            # "Turandot - Opéra - Programmation Saison 26/27" -> "Turandot"; a heading like "retour" is not a name
            title = re.split(r"\s+[-|–—]\s+", (og_title.get("content", "") if og_title else "").strip())[0]
            name = title or (_text(heading) if heading else "")
            name = name[:200]
        structured.setdefault("type", None)
    return page_type, name, structured


def _canonical(soup: BeautifulSoup, url: str) -> str:
    link = soup.find("link", attrs={"rel": lambda v: v and "canonical" in (v if isinstance(v, list) else [v])})
    href = link.get("href") if link else None
    if not href:
        og = soup.find("meta", attrs={"property": "og:url"})
        href = og.get("content") if og else None
    return urljoin(url, href) if href else url


def _item_link(anchor: Tag, base_url: str, host: str) -> Optional[tuple[str, str]]:
    """(item key, absolute URL) when `anchor` links to an item of the same site, else None:
    not navigation (cart, account, author, tag…), not a file, not a "see all" link."""
    href = anchor.get("href", "").strip()
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
        return None
    absolute = _unwrap_redirect(urljoin(base_url, href))
    parts = urlsplit(absolute)
    if parts.scheme not in ("http", "https") or (parts.hostname or "").removeprefix("www.") != host:
        return None
    if (parts.path or "/").strip("/") == "" or NON_ITEM_PATH.search(parts.path) or "add-to-cart" in parts.query:
        return None
    label = _text(anchor) or anchor.get("aria-label", "") or anchor.get("title", "")
    if not anchor.find("img") and not anchor.find(["h2", "h3", "h4"]) and (not label or MORE_LINK_RE.match(label)):
        return None  # "Voir plus", "See all", an icon: a link to the full list, not an item
    return item_key(absolute), absolute


REDIRECT_PARAMS = ("url", "u", "redirect", "redirect_url", "redirectUrl", "to", "target", "dest", "destination")


def _unwrap_redirect(absolute: str) -> str:
    """A click-tracking link (Amazon's /sspa/click?…&url=/dp/…, a widget's /redirect?to=…) stands
    for the page it leads to, when that page is on the same site."""
    parts = urlsplit(absolute)
    if not parts.query:
        return absolute
    params = parse_qs(parts.query)
    for name in REDIRECT_PARAMS:
        for value in params.get(name, []):
            if value.startswith("/") and not value.startswith("//"):
                return urljoin(absolute, value)
            if value.startswith(("http://", "https://")) and urlsplit(value).hostname == parts.hostname:
                return value
    return absolute


def _card_of(anchor: Tag, node: Tag, base_url: str, host: str, key: str) -> Tag:
    """The item's card: the largest element under `node` whose item links all point to this item
    (the image link and the title link of one card share it; two cards for the same item don't).
    Climbs at most 8 levels, and remembers each element's item links (pages of 2 MB have
    thousands of anchors)."""
    card: Tag = anchor
    for depth, parent in enumerate(anchor.parents):
        if parent is node or not isinstance(parent, Tag) or depth >= 8:
            break
        keys = _link_keys_cached(parent, base_url, host)
        if keys - {key}:
            break
        card = parent
    return card


_KEYS_CACHE: dict[int, tuple[Tag, set[str]]] = {}


def _link_keys_cached(element: Tag, base_url: str, host: str) -> set[str]:
    """Item links under `element`, computed once per element (cleared for each page analyzed)."""
    hit = _KEYS_CACHE.get(id(element))
    if hit is not None and hit[0] is element:
        return hit[1]
    keys = {link[0] for a in element.find_all("a", href=True) if (link := _item_link(a, base_url, host))}
    _KEYS_CACHE[id(element)] = (element, keys)
    return keys


TITLE_CLASS_RE = re.compile(r"(^|[-_ ])(title|name|heading)($|[-_ ])|__title|__name|card__heading", re.IGNORECASE)


def _card_name(card: Tag, anchor: Tag) -> str:
    """The item's name as the card shows it: its title element, else a heading, else the image's
    alt text, else the longest link text that isn't a button label ("Coup d'oeil", "Ajouter")."""
    scope = card if isinstance(card, Tag) else anchor
    for node in [scope.find(class_=TITLE_CLASS_RE), scope.find(["h2", "h3", "h4", "h5"])]:
        if isinstance(node, Tag) and _text(node) and not UI_LABEL_RE.match(_text(node)):
            return _text(node)[:160]
    for image in scope.find_all("img"):
        if (image.get("alt") or "").strip():
            return image["alt"].strip()[:160]
    texts = [_text(a) for a in scope.find_all("a")] + [_text(anchor), anchor.get("title", ""), anchor.get("aria-label", "")]
    texts = [t for t in texts if t and not UI_LABEL_RE.match(t) and not MORE_LINK_RE.match(t)]
    return max(texts, key=len)[:160] if texts else ""


def _is_list_card(element: Any) -> bool:
    """A list item or an article is a card of its own: two of them are two cards."""
    return isinstance(element, Tag) and element.name in ("li", "article")


def _items_in(node: Tag, base_url: str) -> list[Item]:
    """Links to items of the same site inside `node`, one per card: the image and the title of a
    card usually both link to the item, and count once; two cards for the same item count twice
    (a real duplicate)."""
    host = (urlsplit(base_url).hostname or "").removeprefix("www.")
    items: list[Item] = []
    cards: list[Any] = []
    list_cards: list[Optional[Tag]] = []  # the <li>/<article> each item was found in
    strong: list[bool] = []  # its link carries the card's title or image
    for anchor in node.find_all("a", href=True):
        link = _item_link(anchor, base_url, host)
        if link is None:
            continue
        key, absolute = link
        card = _card_of(anchor, node, base_url, host, key)
        name = _card_name(card, anchor)
        card_text = _text(card)[:300] if isinstance(card, Tag) else ""
        card_classes = " ".join(card.get("class", [])) if isinstance(card, Tag) else ""
        unavailable = "outofstock" in card_classes or "out-of-stock" in card_classes or bool(OUT_OF_STOCK_RE.search(card_text))
        same_card = next((i for i, (it, c) in enumerate(zip(items, cards)) if it.key == key and c is card), None)
        if same_card is None and items and items[-1].key == key and not (cards[-1] is not card and _is_list_card(cards[-1]) and _is_list_card(card)):
            same_card = len(items) - 1  # the next link of the same card (image, then title), however deep the markup
        if same_card is not None:
            existing = items[same_card]
            existing.name = existing.name or name
            existing.unavailable = existing.unavailable or unavailable
            continue
        items.append(Item(key=key, url=absolute, name=name, unavailable=unavailable))
        cards.append(card)
        list_cards.append(next((p for p in anchor.parents if p is node or _is_list_card(p)), None))
        strong.append(bool(anchor.find("img") or anchor.find(["h2", "h3", "h4"]) or anchor.find(class_=TITLE_CLASS_RE) or TITLE_CLASS_RE.search(" ".join(anchor.get("class", [])))))
    return _one_item_per_list_card(items, list_cards, strong, node)


def _one_item_per_list_card(items: list[Item], list_cards: list[Optional[Tag]], strong: list[bool], node: Tag) -> list[Item]:
    """In a list card (<li>, <article>), the item is the link with the card's title or image: the
    other links of the card (its author, brand, the e-book format of the same product) are not
    other recommendations."""
    groups: dict[int, list[int]] = {}
    for index, card in enumerate(list_cards):
        if card is not None and card is not node and card.name in ("li", "article"):
            groups.setdefault(id(card), []).append(index)
    drop: set[int] = set()
    for indexes in groups.values():
        if len(indexes) < 2:
            continue
        primary = next((i for i in indexes if strong[i]), None)
        if primary is None:
            continue
        for i in indexes:
            if i == primary:
                continue
            if items[i].key == items[primary].key:
                items[primary].unavailable = items[primary].unavailable or items[i].unavailable
            drop.add(i)
    return [item for i, item in enumerate(items) if i not in drop]


def _nearest_heading_text(node: Tag) -> str:
    heading = node.find(["h2", "h3", "h4", "h5"]) or node.find(class_=re.compile(r"title|heading", re.I))
    return _text(heading)[:120] if heading else ""


def _clean_title(text: str) -> str:
    """Drop slider button labels a heading carries ("Précédent POUR TOI: Suivant")."""
    # only when the title sits between a slider's two buttons
    cleaned = re.sub(r"^(précédent|previous)\s+(.*?)\s+(suivant|next)$", r"\2", text, flags=re.IGNORECASE).strip(" :")
    return cleaned or text


def _related(a: Tag, b: Tag) -> bool:
    return a is b or a in b.parents or b in a.parents


def find_blocks(soup: BeautifulSoup, base_url: str, cms: str, vendors: list[dict[str, Any]], current_key: str) -> list[Block]:
    blocks: list[Block] = []

    def add(block: Block) -> None:
        for existing in blocks:
            if existing.element is not None and block.element is not None and _related(existing.element, block.element):
                return
        blocks.append(block)

    # 1. Each CMS's own markup.
    for platform, selector, default_title, kind in NATIVE_BLOCKS:
        for element in soup.select(selector):
            items = _items_in(element, base_url)
            heading = _nearest_heading_text(element)
            add(Block(title=heading or default_title, source="native", kind=kind, items=items, js_only=not items, platform=platform, element=element))

    # 2. Third-party placeholders, filled in by JavaScript.
    vendor_label = next((v["label"] for v in vendors if v["personalized"]), None)
    for element in soup.select(VENDOR_PLACEHOLDERS):
        items = _items_in(element, base_url)
        add(Block(title=_nearest_heading_text(element) or f"Bloc {vendor_label or 'tiers'}", source="vendor", kind="vendor", items=items, js_only=not items, element=element))

    # 3. Headings that announce recommendations: climb to the smallest container holding item links.
    page_title = soup.find("h1")
    candidates: list[Block] = []
    js_titled: list[Block] = []
    for heading in soup.find_all(["h2", "h3", "h4", "h5", "p", "span", "div"]):
        if heading.find(["h2", "h3", "h4", "div", "ul", "section"]) or heading.find_parent("a") is not None or len(heading.find_all("a")) > 1:
            continue  # a wrapper, a link, or a menu line - not a block title
        text = _text(heading)
        if not (3 <= len(text) <= 120) or not HEADING_RE.search(text) or NOISE_HEADING_RE.search(text):
            continue
        if _in_breadcrumb(heading) or _in_reviews(heading):
            continue
        if heading_kind(text) == "popular" and not _looks_like_title(heading) and len(text.split()) <= 2:
            continue  # "Best seller" on a product card is a badge, not the title of a block
        # an article's own <footer> ("À lire aussi" under the text, as 20 Minutes does) is not the site's
        chrome = next((p for p in heading.parents if isinstance(p, Tag) and (p.name in ("nav", "form") or (p.name == "footer" and p.find_parent("article") is None)
                                                                          or (p.name == "header" and _is_page_header(p)))), None)
        if chrome is not None and (page_title is None or chrome not in page_title.parents):
            continue  # site navigation - unless broken markup wrapped the whole page in it
        container: Optional[Tag] = heading
        items: list[Item] = []
        for _ in range(5):
            container = container.parent if container is not None else None
            if container is None or container.name in ("body", "html", "main"):
                break
            items = _items_in(container, base_url)
            if len({i.key for i in items}) >= 2:
                break
        if len({i.key for i in items}) > MAX_BLOCK_ITEMS:
            # climbed into a whole listing: the block is there, its own items aren't readable
            js_titled.append(Block(title=_clean_title(text), source="js", kind=heading_kind(text), js_only=True,
                                   claims_personal=bool(CLAIMS_PERSONAL_RE.search(text)), element=heading.parent or heading))
            continue
        if container is None or container.name in ("body", "html", "main") or len({i.key for i in items}) < 2:
            # A real title (h2-h4 or a "title" element) whose own box holds no link at all: the
            # block is there, filled in by the browser. Its title still says what kind it is.
            box = heading.find_parent(["section", "aside", "div", "li"]) if _looks_like_title(heading) else None
            if box is not None and box.name != "body" and not box.find("a", href=True) and len(_text(box)) < 200 and not _in_breadcrumb(box):
                js_titled.append(Block(title=_clean_title(text), source="js", kind=heading_kind(text), js_only=True,
                                       claims_personal=bool(CLAIMS_PERSONAL_RE.search(text)), element=box))
            continue
        if _in_breadcrumb(container):
            continue
        claims = bool(CLAIMS_PERSONAL_RE.search(text))
        kind = heading_kind(text)
        candidates.append(Block(title=_clean_title(text), source="heading", kind=kind, items=items, claims_personal=claims, element=container))

    # A section title ("Que lire après ?") and its sub-blocks ("Ceux qui ont aimé…", "Du même
    # auteur") both match: keep the most specific blocks, never the section that wraps them.
    innermost = [b for b in candidates if not any(o is not b and o.element is not b.element and b.element in o.element.parents for o in candidates)]
    for block in innermost:
        add(block)
    for block in js_titled[:4]:
        add(block)

    # 4. Empty containers named after recommendations, filled in by the site's own JavaScript.
    for element in _js_reco_containers(soup)[:40]:
        if element.name not in ("div", "section", "aside", "ul") or _in_breadcrumb(element) or len(_text(element)) > 60:
            continue
        if _items_in(element, base_url):
            continue
        add(Block(title=_nearest_heading_text(element) or "Bloc chargé en JavaScript", source="js", kind="unknown", js_only=True, element=element))
        if sum(1 for b in blocks if b.source == "js") >= 3:
            break
    return blocks


JS_RECO_ATTR_RE = re.compile(
    r"recommend|related-?products?|similar-?products?|also-(bought|viewed)|you-?may-?also|\bymal\b|cross-?sell|upsell|p13n|frequently-bought", re.IGNORECASE)


def _js_reco_containers(soup: BeautifulSoup) -> list[Tag]:
    """Elements whose id, class or data-testid/component/module names recommendations - one pass
    over the page (a CSS selector with these 75 variants took seconds on a 1 MB page)."""
    found = []
    for element in soup.find_all(["div", "section", "aside", "ul"]):
        attrs = element.attrs
        names = " ".join(str(v) if not isinstance(v, list) else " ".join(v) for k, v in attrs.items()
                         if k in ("id", "class", "data-testid", "data-component", "data-module"))
        if names and JS_RECO_ATTR_RE.search(names):
            found.append(element)
    return found


MAX_BLOCK_ITEMS = 40  # a recommendation block shows a handful to a few dozen items, not a whole listing


def _in_reviews(node: Tag) -> bool:
    """Customer reviews and comments: their titles are sentences, not block titles."""
    for depth, element in enumerate([node, *node.parents]):
        if not isinstance(element, Tag) or depth > 12:
            break
        attrs = f"{element.get('id', '')} {' '.join(element.get('class', []))} {element.get('data-hook', '')}".lower()
        if re.search(r"review|avis-client|customer-?reviews|comments?\b|commentaires|rating-list|testimonial", attrs):
            return True
    return False


def _is_page_header(header: Tag) -> bool:
    """The site's banner, not the <header> of a section ("Les internautes ont aussi acheté" in its
    own <header>): marked as the banner, or outside any main/section/article/aside with the site's
    menu or search in it."""
    if header.get("role") == "banner":
        return True
    if header.find_parent(["main", "section", "article", "aside"]) is not None:
        return False
    return header.find(["nav", "input"]) is not None or header.find_parent("body") is header.parent or header.parent is None


def _looks_like_title(node: Tag) -> bool:
    return node.name in ("h2", "h3", "h4") or bool(re.search(r"title|heading|headline", " ".join(node.get("class", [])), re.IGNORECASE))


def _in_breadcrumb(node: Tag) -> bool:
    for element in [node, *node.parents]:
        if not isinstance(element, Tag):
            continue
        attrs = f"{element.get('id', '')} {' '.join(element.get('class', []))} {element.get('aria-label', '')}".lower()
        if "breadcrumb" in attrs or "ariane" in attrs or element.get("itemtype", "").endswith("BreadcrumbList"):
            return True
    return False


def analyze_page(page: Page, soup: Optional[BeautifulSoup] = None) -> PageFacts:
    _KEYS_CACHE.clear()
    soup = soup or BeautifulSoup(page.text, "lxml")
    html = page.text
    cms = detect_cms(html, soup)
    vendors = detect_vendors(html)
    nodes = _json_ld(soup)
    page_type, name, structured = detect_page_type(soup, html, page.url, nodes, cms)
    canonical_key = item_key(_canonical(soup, page.url))
    blocks = find_blocks(soup, page.url, cms, vendors, canonical_key)
    for block in blocks:
        if block.element is not None and block.items and SPONSORED_RE.search(_text(block.element)[:400]):
            block.sponsored = True
    shopify_id = None
    if cms == "shopify":
        match = re.search(r'"product"\s*:\s*\{\s*"id"\s*:\s*(\d+)', html) or re.search(r"product_id=(\d+)", html) or re.search(r'"productId"\s*:\s*"?(\d+)', html)
        shopify_id = match.group(1) if match else None
    html_tag = soup.find("html")
    scripts = " ".join(script.get_text() for script in soup.find_all("script"))
    return PageFacts(
        url=page.url, canonical_key=canonical_key, page_type=page_type, item_name=name, structured=structured,
        cms=cms, vendors=vendors, blocks=blocks, shopify_product_id=shopify_id,
        lang=(html_tag.get("lang", "") if html_tag else "")[:10],
        js_reco_signals=bool(SCRIPT_RECO_RE.search(scripts)),
        text_chars=len(soup.get_text(" ", strip=True)),
    )


SPONSORED_RE = re.compile(r"\bsponsoris[ée]e?s?\b|\bsponsored\b", re.IGNORECASE)
SCRIPT_RECO_RE = re.compile(r"you (may|might) also like|customers (also|who)|also (bought|viewed|enjoyed)|frequently bought|similar (products|items)|"
                            r"vous aimerez aussi|recommendations?\W{1,3}(widget|carousel|placement|slot)|ont aussi (aimé|acheté)|readers also|more like this|"
                            r"(à |a )?lire aussi|related (articles|stories|content)|\brecommendations\b|\brecommandations\b|read next|most (read|viewed)", re.IGNORECASE)


def shopify_section_items(fetch: Fetch, page_url: str, data_url: str) -> Optional[list[Item]]:
    """What a Shopify theme's recommendations section shows, exactly: the section the theme loads
    in JavaScript (its data-url), fetched as HTML - so the count is what visitors see."""
    try:
        page = fetch(urljoin(page_url, data_url))
    except AuditError:
        return None
    if page.status != 200:
        return None
    return _items_in(BeautifulSoup(page.text, "lxml"), page_url)


def shopify_flag_out_of_stock(fetch: Fetch, page_url: str, items: list[Item], deadline_items: int = 12) -> bool:
    """Marks the Shopify items that are out of stock, from each product's public /products/x.js
    (`available` false = no variant can be bought). False when nothing could be read."""
    parts = urlsplit(page_url)
    checked = False
    for item in items[:deadline_items]:
        handle = item.key.rsplit("/", 1)[-1]
        if not item.key.startswith("/products/") or not handle:
            continue
        try:
            answer = fetch(f"{parts.scheme}://{parts.netloc}/products/{handle}.js")
            data = json.loads(answer.text) if answer.status == 200 else None
        except (AuditError, ValueError):
            data = None
        if isinstance(data, dict) and "available" in data:
            checked = True
            item.unavailable = data["available"] is False
    return checked


def fill_shopify_sections(fetch: Fetch, facts: PageFacts) -> None:
    """A Shopify theme's recommendations section fills in with JavaScript from its data-url: fetch
    that section to read exactly what it shows. Never invents a block the theme doesn't have."""
    if facts.cms != "shopify":
        return
    for block in facts.blocks:
        data_url = block.element.get("data-url") if block.platform == "shopify" and block.element is not None else None
        if block.js_only and data_url:
            items = shopify_section_items(fetch, facts.url, data_url)
            if items:
                block.items, block.js_only, block.source = items, False, "shopify_section"


def _signature(title: str) -> str:
    """A block's title, comparable from one page to another ("Autres livres de X (26)" -> "autres livres de x")."""
    return re.sub(r"[^\w]+", " ", re.sub(r"\(\d+\)", "", title.lower())).strip()


def _keys(block: Block, *exclude: str) -> set[str]:
    return {i.key for i in block.items} - set(exclude)


def compare_blocks(facts: PageFacts, reload: Optional[PageFacts], other: Optional[PageFacts], other_name: str) -> None:
    """Sets each readable block's `verified` and `evidence` from what the two extra loads showed:
    the same page loaded again (a block that changes is drawn at random) and another item's page
    (a block that shows the same products there is a fixed collection). Then its kind follows."""
    reloaded = {_signature(b.title): b for b in (reload.blocks if reload else []) if b.items}
    elsewhere = {_signature(b.title): b for b in (other.blocks if other else []) if b.items}
    other_label = f"« {other_name} »" if other_name else "une autre fiche"
    for block in facts.blocks:
        if not block.items:
            continue
        sig = _signature(block.title)
        twin = reloaded.get(sig)
        if twin is not None and _keys(twin) != _keys(block):
            if block.kind == "similar":
                block.verified, block.kind = "random", "random"
                block.evidence = "Rechargée, la page affiche d'autres éléments dans ce bloc : il est tiré au hasard à chaque affichage."
                continue
            # a block learned from customers that rotates is still that block: say so, judge it as titled
            block.evidence = "Rechargée, la page affiche d'autres éléments dans ce bloc : sa sélection tourne d'un affichage à l'autre. "
        if other is None:
            block.evidence += "Pas comparé à une autre fiche : elle n'a pas pu être ouverte."
            continue
        peer = elsewhere.get(sig)
        if peer is None:
            block.evidence += f"Pas comparé : le bloc n'apparaît pas sur la fiche {other_label}."
            continue
        mine, theirs = _keys(block, facts.canonical_key, other.canonical_key), _keys(peer, facts.canonical_key, other.canonical_key)
        if len(mine) >= 3 and mine == theirs and not GROUPED_LIST_RE.search(block.title):
            block.verified = "fixed"
            if block.kind in ("similar", "behavior", "complement"):
                block.kind = "fixed"
            block.evidence += f"Sur la fiche {other_label}, ce bloc affiche exactement les mêmes éléments : il ne dépend pas de la fiche affichée."
        else:
            block.verified = "varies"
            block.evidence += f"Sur la fiche {other_label}, ce bloc affiche d'autres éléments : il dépend bien de la fiche affichée."


def _comparison_target(facts: PageFacts) -> Optional[Item]:
    """The item page to compare with: the first recommended item that isn't the page itself."""
    for block in facts.blocks:
        for item in block.items:
            if item.key != facts.canonical_key:
                return item
    return None


# --------------------------------------------------------------------------------------------
# KPIs and score
# --------------------------------------------------------------------------------------------

PASS, WARN, FAIL, UNKNOWN, INFO = "pass", "warn", "fail", "unknown", "info"
STATUS_VALUE = {PASS: 1.0, WARN: 0.5, FAIL: 0.0}
# Recommending the item being viewed is the most visible mistake: whatever else is right, the
# score can't go above this.
SELF_RECO_SCORE_CAP = 35


@dataclass
class Kpi:
    id: str
    label: str
    weight: int
    status: str
    summary: str
    detail: str = ""
    advice: str = ""
    method: str = ""  # how it was measured, in one short sentence (the page's tooltip)
    about: str = ""  # what the check is, in plain words (shown under the title)

    def public(self, full: bool) -> dict[str, Any]:
        data = {"id": self.id, "label": self.label, "about": self.about, "status": self.status, "summary": self.summary, "weight": self.weight, "method": self.method}
        if full:
            data.update({"detail": self.detail, "advice": self.advice})
        return data


METHODS = {
    "self_reco": "Adresse et nom du produit affiché comparés à chaque élément des blocs.",
    "similar_block": "Bloc comparé sur une deuxième fiche du site et sur un rechargement de la page : mêmes produits ailleurs = collection fixe, autres produits au rechargement = hasard.",
    "behavior_block": "Bloc titré « ont aussi aimé / acheté », ou bloc natif du CMS fondé sur les achats, et qui n'est pas une collection fixe (vérifié sur une deuxième fiche).",
    "personalization": "Outil de personnalisation chargé par la page (Nosto, Clerk, LIKYLY…) ou bloc « vus récemment ». Ne se constate pas de l'extérieur : un robot sans historique voit la version commune.",
    "duplicates": "Liens et noms comparés à l'intérieur de chaque bloc et d'un bloc à l'autre.",
    "block_size": "Nombre d'éléments réellement affichés dans chaque bloc.",
    "structured_data": "Données schema.org (JSON-LD) lues dans le code de la page.",
    "agent_ready": "Présence du fichier /.well-known/ucp sur le site.",
}
STOCK_METHODS = {
    "shopify": "Stock lu sur la fiche publique Shopify de chaque produit recommandé (/products/….js).",
    "woocommerce": "Stock lu dans le code WooCommerce de chaque carte produit (en stock / en rupture).",
}
STOCK_METHOD_DEFAULT = "Mentions « épuisé », « rupture », « sold out » cherchées sur chaque carte produit."


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


ITEM_WORD = {"product": ("produit", "produits"), "book": ("livre", "livres"), "event": ("événement", "événements"), "article": ("article", "articles")}
ITEM_THE = {"product": "le produit", "book": "le livre", "event": "l'événement", "article": "l'article"}
BEHAVIOR_TITLE = {"product": "« Les clients ont aussi acheté »", "book": "« Les lecteurs ont aussi aimé »", "article": "« Les lecteurs ont aussi lu »", "event": "« Les spectateurs ont aussi réservé »"}
BEHAVIOR_LABEL = {"product": "Recommandations basées sur les achats", "book": "Recommandations basées sur les lectures", "article": "Recommandations basées sur les lectures", "event": "Recommandations basées sur les réservations"}
BEHAVIOR_SOURCE = {"product": "acheté", "book": "aimé", "article": "lu", "event": "réservé"}
UNAVAILABLE_LABEL = {"product": "Stock des produits recommandés", "book": "Stock des livres recommandés", "event": "Disponibilité des événements recommandés"}


def _quote(blocks: list[Block]) -> str:
    return ", ".join(f"« {b.title} »" for b in blocks[:3])


def build_kpis(page: PageFacts, ucp: Optional[bool], stock_checked: bool = False) -> list[Kpi]:
    """The grid. What a product (or article, book, event) page should have: at least two blocks -
    one related to the item (similar products: the cold start, may be the same for everyone) and
    one learned from what other customers did (also liked / also bought) - with at least one block
    adapted to the visitor, never the item itself, no duplicates, nothing out of stock."""
    one, many = ITEM_WORD.get(page.page_type, ("élément", "éléments"))
    the = ITEM_THE.get(page.page_type, "l'élément")
    The = the[0].upper() + the[1:]
    behavior_title = BEHAVIOR_TITLE.get(page.page_type, "« Les visiteurs ont aussi aimé »")
    behavior_label = BEHAVIOR_LABEL.get(page.page_type, "Recommandations basées sur les autres visiteurs")
    personal_vendor = next((v for v in page.vendors if v["personalized"]), None)
    readable = [b for b in page.blocks if b.items]
    js_only = [b for b in page.blocks if b.js_only]
    # Recommendations the page renders in the browser: an empty block in the HTML - or, when no block
    # can be read at all, the page's scripts talking about recommendations.
    # (best-of lists don't count: they're site chrome, not the item's own recommendations)
    in_javascript = bool(js_only) or (page.js_reco_signals and not [b for b in readable if b.kind != "popular"])
    # Our browser couldn't show a page that needed it: blocks missing from the HTML were not seen, not proven absent.
    in_javascript = in_javascript or (page.render_failed and not [b for b in readable if b.kind != "popular"])
    if page.render_failed and not page.rendered:
        js_summary = "Non mesuré : notre navigateur d'audit n'a pas pu afficher la page"
        js_detail = ("Les recommandations de cette page ne sont pas dans son HTML, et notre navigateur d'audit n'a pas pu l'afficher "
                     "(page trop lente, erreur ou vérification anti-robots). On ne sait donc pas si elles existent : ce contrôle n'est pas noté.")
    elif page.rendered:
        js_summary = "Non lisible : le bloc reste vide, même affiché dans un navigateur"
        js_detail = ("La page a été affichée dans un navigateur, mais ce bloc ne s'y est pas rempli pour notre visite (souvent : il dépend de l'historique "
                     "du visiteur, ou le site ne le montre pas à un robot). On ne note donc pas ce contrôle.")
    else:
        js_summary = "Non lisible sans navigateur : les recommandations de la page se chargent en JavaScript"
        js_detail = ("La page remplit ses blocs de recommandations dans le navigateur : on ne peut pas les lire ici, donc on ne note pas ce contrôle. "
                     "Les moteurs de recherche et les assistants IA ne les voient pas non plus.")
    by_kind = lambda *kinds: [b for b in page.blocks if b.kind in kinds]  # noqa: E731
    kpis: list[Kpi] = []

    # 1. The item recommended on its own page - the first thing to get right.
    # Best-of lists ("les plus lus") and comparison tables include the item shown by nature.
    checked = [b for b in readable if b.kind != "popular" and not re.search(r"compar", b.title, re.IGNORECASE)]
    if page.page_type not in ("home", "category") and checked:
        same = [b for b in checked if any(i.key == page.canonical_key for i in b.items)]
        name = page.item_name.strip().lower()
        twins = [b for b in checked if name and any(i.key != page.canonical_key and i.name.strip().lower() == name for i in b.items)]
        if same:
            kpis.append(Kpi("self_reco", "Auto-recommandation", 25, FAIL,
                            f"{The} affiché est recommandé sur sa propre page, dans {_quote(same)}",
                            f"Le visiteur regarde déjà {the} : le lui proposer gâche une place du bloc et montre que la sélection ne tient pas compte de la page. "
                            "C'est l'erreur la plus visible, la première à corriger.",
                            f"Excluez toujours {the} affiché de ses propres recommandations."))
        elif twins:
            kpis.append(Kpi("self_reco", "Auto-recommandation", 25, WARN,
                            f"Le même modèle, sous un autre lien (autre couleur ou variante), est recommandé dans {_quote(twins)}",
                            f"Ce n'est pas exactement {the} affiché, mais pour le visiteur c'est le même : une place de suggestion utilisée pour rien.",
                            "Excluez aussi les variantes du même modèle, ou regroupez-les en une seule fiche."))
        else:
            kpis.append(Kpi("self_reco", "Auto-recommandation", 25, PASS,
                            f"{The} affiché n'est pas dans ses propres recommandations",
                            "Aucun bloc ne propose ce que le visiteur regarde déjà."))
    else:
        kpis.append(Kpi("self_reco", "Auto-recommandation", 25, UNKNOWN,
                        "Non vérifiable : aucun bloc lisible" if page.blocks else "Aucun bloc : rien à vérifier",
                        "Ce contrôle a besoin de lire les éléments recommandés."))

    # 2. Block 1: related to the item (content, category - the cold start). Judged on what was
    # seen: on another item page, on a second load - never on the CMS or the theme's names.
    similar = [b for b in by_kind("similar") if b.items]
    proven = [b for b in similar if b.verified == "varies"]
    similar_js = [b for b in by_kind("similar") if b.js_only and b.source == "js" and b.title != "Bloc chargé en JavaScript"]
    manual = [b for b in by_kind("manual") if b.items]
    random_blocks, fixed = by_kind("random"), by_kind("fixed")
    label = "Recommandations similaires"
    if proven:
        kpis.append(Kpi("similar_block", label, 20, PASS, f"Présent : {_quote(proven)}, propre à chaque fiche (vérifié)",
                        f"Un bloc lié à {the} affiché : c'est la base, utile même pour un visiteur dont on ne sait rien (démarrage à froid). {proven[0].evidence}"))
    elif similar:
        kpis.append(Kpi("similar_block", label, 20, WARN, f"Présent : {_quote(similar)}, mais non vérifié",
                        f"{similar[0].evidence} On ne peut donc pas dire si ce bloc change avec {the} affiché.",
                        "Relancez l'audit sur une autre fiche pour comparer."))
    elif manual:
        kpis.append(Kpi("similar_block", label, 20, WARN, f"Présent : {_quote(manual)}, choisi à la main",
                        "Une sélection faite à la main dans le back-office, produit par produit : longue à tenir à jour, jamais complète.",
                        "Calculez ce bloc automatiquement à partir du catalogue (contenu, attributs), pour tous les produits."))
    elif random_blocks:
        kpis.append(Kpi("similar_block", label, 20, WARN, f"{_quote(random_blocks)} est tiré au hasard : il change à chaque affichage (constaté)",
                        f"{random_blocks[0].evidence} Les {many} montrés ne sont pas choisis pour leur ressemblance avec {the} affiché.",
                        f"Choisissez les {many} proches par leur contenu (description, attributs, style), pas au hasard."))
    elif fixed:
        kpis.append(Kpi("similar_block", label, 20, FAIL, f"Absent : {_quote(fixed)} affiche les mêmes {many} sur une autre fiche (constaté)",
                        f"{fixed[0].evidence} C'est une collection fixe, sans rapport avec {the} que le visiteur regarde.",
                        f"Remplacez-la par des {many} proches de celui affiché : même usage, même style, même gamme de prix."))
    elif similar_js:
        kpis.append(Kpi("similar_block", label, 20, WARN,
                        f"Présent : {_quote(similar_js)}, " + ("mais vide, même affiché dans un navigateur" if page.rendered else "chargé en JavaScript (contenu non lisible)"),
                        "Le bloc existe sur la page, mais il se remplit dans le navigateur : on ne peut pas vérifier ce qu'il montre, "
                        "et les moteurs de recherche comme les assistants IA ne le voient pas.",
                        "Rendez ce bloc côté serveur pour qu'il soit lisible par tous."))
    elif in_javascript:
        kpis.append(Kpi("similar_block", label, 20, UNKNOWN, js_summary, js_detail, "Rendez au moins ce bloc côté serveur."))
    else:
        kpis.append(Kpi("similar_block", label, 20, FAIL, "Absent",
                        f"Le visiteur qui hésite sur {the} n'a aucune alternative proche sous les yeux : s'il ne trouve pas son bonheur, il repart.",
                        f"Ajoutez sous {the} un bloc de 4 à 8 {many} proches (contenu, catégorie, attributs)."))

    # 3. Block 2: learned from what other customers did (or a cross-sell of what goes with the item).
    behavior = by_kind("behavior")
    complement = by_kind("complement")
    popular = by_kind("popular")
    if behavior:
        unreadable = all(b.js_only for b in behavior)
        kpis.append(Kpi("behavior_block", behavior_label, 20, PASS,
                        f"Présent : {_quote(behavior)}" + (", chargé en JavaScript" if unreadable else ""),
                        "Un bloc appris de ce que font les autres clients : il révèle des associations que la fiche seule ne dit pas. " + behavior[0].evidence))
    elif complement:
        kpis.append(Kpi("behavior_block", behavior_label, 20, PASS,
                        f"Présent, en ventes croisées : {_quote(complement)}",
                        f"Un bloc propose ce qui va avec {the} (accessoires, compléments) : c'est le rôle du second bloc, qui augmente le panier. " + complement[0].evidence))
    elif popular:
        kpis.append(Kpi("behavior_block", behavior_label, 20, WARN,
                        f"Seulement la popularité : {_quote(popular)}",
                        f"Ce bloc montre ce que tout le monde regarde, pas ce que les clients intéressés par {the} ont choisi ensuite : il est le même partout.",
                        f"Ajoutez un bloc {behavior_title}, propre à chaque fiche."))
    elif in_javascript:
        kpis.append(Kpi("behavior_block", behavior_label, 20, UNKNOWN, js_summary, js_detail,
                        f"Vérifiez qu'un des blocs est bien {behavior_title}, appris des visites, paniers et commandes."))
    else:
        kpis.append(Kpi("behavior_block", behavior_label, 20, FAIL,
                        "Absent",
                        "Aucun bloc ne s'appuie sur ce que les autres clients ont aimé ou acheté avec cet élément. C'est le deuxième bloc attendu sur une fiche : "
                        "il fait découvrir des associations réelles (le sac et sa pochette, le livre et sa suite) et augmente le panier.",
                        f"Ajoutez un second bloc {behavior_title}, calculé à partir des visites, paniers et commandes."))

    # 4. Personalization: does any block adapt to the visitor?
    personal = by_kind("personal")
    claims = [b for b in page.blocks if b.claims_personal]
    if personal_vendor:
        kpis.append(Kpi("personalization", "Personnalisation", 20, PASS,
                        f"Outil de personnalisation détecté : {personal_vendor['label']}",
                        "Un outil capable d'adapter les blocs à chaque visiteur est chargé. La personnalisation elle-même ne se voit pas de l'extérieur : "
                        "mesurez ce que rapporte chaque bloc pour vérifier qu'elle fonctionne."))
    elif personal:
        kpis.append(Kpi("personalization", "Personnalisation", 20, WARN,
                        f"Seulement l'historique du visiteur : {_quote(personal)}",
                        "Montrer ce que le visiteur a déjà vu l'aide à revenir en arrière, mais ne lui fait rien découvrir.",
                        "Ajoutez des suggestions qui partent de son historique pour proposer d'autres éléments."))
    elif behavior or in_javascript:
        # The site computes its recommendations itself (from purchases, or in its own scripts): whether
        # they adapt to each visitor can't be seen by a robot without history. Not scored.
        kpis.append(Kpi("personalization", "Personnalisation", 20, UNKNOWN,
                        "Non mesurable de l'extérieur : le site calcule ses recommandations lui-même",
                        "Aucun outil de personnalisation connu n'est chargé, mais le site produit ses propres recommandations "
                        + ("à partir des achats de ses clients" if behavior else "dans ses scripts")
                        + ". Si elles s'adaptent à chaque visiteur, cela ne se voit pas depuis une visite sans historique : ce contrôle n'est pas noté."))
    else:
        proven_fixed = [b for b in claims if b.verified == "fixed"]
        why = (f"Le bloc {_quote(proven_fixed)} s'intitule « pour vous », mais affiche les mêmes produits sur une autre fiche : ce n'est pas une sélection pour le visiteur. "
               if proven_fixed else
               f"Le bloc {_quote(claims)} s'intitule « pour vous », mais aucun outil de personnalisation n'est chargé. " if claims else "")
        kpis.append(Kpi("personalization", "Personnalisation", 20, FAIL,
                        "Aucune : tous les visiteurs voient les mêmes recommandations",
                        why + "Aucun outil de personnalisation n'est chargé par la page. Un bloc « similaires » identique pour tous est normal (démarrage à froid) ; "
                        "mais au moins un bloc doit s'adapter à ce que le visiteur a regardé et à ses goûts.",
                        "Faites dépendre un bloc de la visite en cours et de l'historique du visiteur."))

    # 5. Duplicates.
    if readable:
        inside = [b for b in readable if len({i.key for i in b.items}) < len(b.items)]
        keys_per_block = [set(i.key for i in b.items) for b in readable]
        across: set[str] = set()
        for idx, keys in enumerate(keys_per_block):
            for other in keys_per_block[idx + 1:]:
                across |= keys & other
        if inside:
            kpis.append(Kpi("duplicates", "Doublons", 5, FAIL, f"Un même {one} apparaît deux fois dans « {inside[0].title} »",
                            "Un doublon prend la place d'une autre suggestion et donne l'impression d'un catalogue pauvre.", "Dédoublonnez chaque bloc avant affichage."))
        elif twins_inside := [b for b in readable if len({i.name.strip().lower() for i in b.items if i.name}) < len([i for i in b.items if i.name])]:
            kpis.append(Kpi("duplicates", "Doublons", 5, WARN, f"Le même modèle apparaît deux fois (autre couleur ou variante) dans « {twins_inside[0].title} »",
                            "Deux variantes du même modèle occupent deux places : le visiteur voit moins de choix qu'il n'y paraît.",
                            "Ne gardez qu'une variante par modèle dans un même bloc."))
        elif across:
            kpis.append(Kpi("duplicates", "Doublons", 5, WARN, f"{_plural(len(across), one, many)} dans plusieurs blocs de la page",
                            "Les blocs se répètent : le visiteur voit moins de choix qu'il n'y paraît.", "Faites écarter à chaque bloc ce que les blocs au-dessus montrent déjà."))
        else:
            kpis.append(Kpi("duplicates", "Doublons", 5, PASS, "Aucun doublon", "Chaque suggestion est unique sur la page."))
    else:
        kpis.append(Kpi("duplicates", "Doublons", 5, UNKNOWN, "Non vérifiable sans bloc lisible", ""))

    # 6. Out of stock (products, books) / sold out or past (events). Not broken links.
    label = UNAVAILABLE_LABEL.get(page.page_type)
    flagged = [i for b in readable for i in b.items if i.unavailable]
    knows_stock = stock_checked or page.cms == "woocommerce"
    if label is None:
        kpis.append(Kpi("unavailable", "Stock des éléments recommandés", 5, UNKNOWN, "Sans objet : pas de stock pour ce type de page", ""))
    elif flagged:
        kpis.append(Kpi("unavailable", label, 5, FAIL,
                        _plural(len(flagged), one + " recommandé n'est plus disponible", many + " recommandés ne sont plus disponibles") + " : " + ", ".join(i.name or i.url for i in flagged[:3]),
                        "En rupture de stock (ou complet, pour un événement) : le visiteur clique, et ne peut pas acheter. Le lien fonctionne, c'est le produit qui manque.",
                        "Filtrez les éléments en rupture dans chaque bloc."))
    elif readable and knows_stock:
        kpis.append(Kpi("unavailable", label, 5, PASS, "Tout ce qui est recommandé est disponible", "Chaque suggestion peut être achetée."))
    else:
        kpis.append(Kpi("unavailable", label, 5, UNKNOWN, "Impossible de savoir si les recommandations sont disponibles",
                        "Ni la page ni le site n'indiquent si les éléments recommandés sont en stock."))

    # 7. Block size, counted on what the page shows.
    if readable:
        counts = [len({i.key for i in b.items}) for b in readable]
        span = f"{min(counts)} {many}" if min(counts) == max(counts) else f"entre {min(counts)} et {max(counts)} {many}"
        # a "bought together" bundle of 2 or 3 is fine: judged on the largest block
        if max(counts) < 4:
            kpis.append(Kpi("block_size", "Nombre de recommandations par bloc", 3, WARN, f"{span.capitalize()} recommandés par bloc : pas assez",
                            "Avec moins de 4 suggestions, un bloc laisse peu de chances de trouver quelque chose.", f"Visez 4 à 8 {many} par bloc."))
        elif max(counts) > 16:
            kpis.append(Kpi("block_size", "Nombre de recommandations par bloc", 3, WARN, f"{span.capitalize()} recommandés par bloc : trop",
                            "Au-delà d'une quinzaine, les meilleures suggestions se perdent.", f"Visez 4 à 12 {many}, les meilleurs en premier."))
        else:
            kpis.append(Kpi("block_size", "Nombre de recommandations par bloc", 3, PASS, f"{span.capitalize()} recommandés par bloc", "Assez de choix sans noyer le visiteur."))
    else:
        kpis.append(Kpi("block_size", "Nombre de recommandations par bloc", 3, UNKNOWN, "Non vérifiable sans bloc lisible", ""))

    # 8. Structured data (search engines and AI agents read it), on every page that shows one thing.
    st = page.structured
    label = "Fiche lisible par Google et les IA"
    declared = [t for t in st.get("json_ld_types", []) if t not in GENERIC_LD_TYPES]
    what = {"product": "un produit", "book": "un livre", "event": "un spectacle ou un événement", "article": "un article"}.get(page.page_type, "ce qu'elle présente")
    if page.page_type in ("home", "category"):
        kpis.append(Kpi("structured_data", label, 2, UNKNOWN, "Analysez une fiche (produit, article, événement) pour ce contrôle", ""))
    elif st.get("type"):
        sells = page.page_type == "product" or st.get("has_offer")
        needed = ["has_name", "has_image"] + (["has_offer", "has_price", "has_availability"] if sells else ["has_date"] if page.page_type in ("article", "event") else [])
        missing = [k for k in needed if not st.get(k)]
        names = {"has_name": "nom", "has_image": "image", "has_offer": "offre", "has_price": "prix", "has_availability": "stock", "has_date": "date"}
        if not missing:
            kpis.append(Kpi("structured_data", label, 2, PASS, "Bien décrite : nom, image, prix et disponibilité",
                            "Google, Bing et les agents IA (ChatGPT, Gemini…) comprennent ce que la page présente.",
                            "" if st.get("related_in_markup") else "Ajoutez les éléments liés (isRelatedTo, isSimilarTo) pour que les agents connaissent aussi vos associations."))
        else:
            kpis.append(Kpi("structured_data", label, 2, WARN, "Incomplète : " + ", ".join(names[k] for k in missing) + " manquant",
                            "Les moteurs et les agents IA lisent ces données pour présenter vos produits.", "Complétez le balisage schema.org de la page."))
    elif st.get("json_ld_types"):
        generic = ", ".join(f"« {t} »" for t in st["json_ld_types"][:3])
        kpis.append(Kpi("structured_data", label, 2, FAIL, f"La page se décrit seulement comme {generic}, pas comme {what}",
                        f"Des données schema.org existent, mais elles ne disent pas que la page présente {what} : ni nom, ni date, ni prix, ni disponibilité "
                        "pour Google et les assistants IA." + (f" Types trouvés : {', '.join(declared)}." if declared else ""),
                        f"Décrivez la page avec le bon type schema.org ({'Event' if page.page_type == 'event' else 'Product' if page.page_type == 'product' else 'Product, Event, Book ou Article'}) : nom, image, date ou prix, disponibilité."))
    else:
        kpis.append(Kpi("structured_data", label, 2, FAIL, "Aucune donnée structurée schema.org",
                        f"Rien dans le code de la page ne décrit {what} : moteurs de recherche et assistants IA doivent le deviner.",
                        "Ajoutez un bloc JSON-LD schema.org (Product, Event, Book ou Article) avec nom, image, prix et disponibilité."))

    # 9. AI shopping agents (UCP) - informative, not scored.
    if ucp is True:
        kpis.append(Kpi("agent_ready", "Achats via les assistants IA (UCP)", 0, PASS, "Le site est déclaré aux assistants d'achat IA",
                        "Le site se déclare aux agents d'achat IA (Gemini, mode IA de Google…) via le Universal Commerce Protocol."))
    else:
        kpis.append(Kpi("agent_ready", "Achats via les assistants IA (UCP)", 0, INFO, "Le site n'est pas encore déclaré aux assistants d'achat IA",
                        "Le Universal Commerce Protocol (Google, Shopify…) permet aux agents d'achat IA de chercher dans votre catalogue et d'acheter chez vous. "
                        "Il se déploie d'abord aux États-Unis : à surveiller.",
                        "Shopify l'active d'office ; ailleurs, il passe par une extension."))
    source = BEHAVIOR_SOURCE.get(page.page_type, "aimé")
    about = {
        "self_reco": f"{The} affiché ne doit jamais apparaître dans ses propres recommandations.",
        "similar_block": f"Des {many} proches de celui affiché (style, usage, catégorie), souvent titrées « Vous aimerez aussi ». Utiles dès la première visite.",
        "behavior_block": f"Ce que les autres clients ont {source} avec ce {one}, souvent titré {behavior_title}. Un second bloc, différent du précédent.",
        "personalization": "Au moins un bloc doit s'adapter au visiteur : ce qu'il a regardé, ses goûts.",
        "duplicates": f"Un même {one} ne doit pas apparaître deux fois dans les recommandations.",
        "unavailable": ("Recommander un événement complet ou passé mène le visiteur à une impasse." if page.page_type == "event" else
                        f"Recommander un {one} en rupture de stock mène le visiteur à une impasse."),
        "block_size": f"Combien de {many} chaque bloc propose : entre 4 et 8, assez de choix sans noyer les meilleurs.",
        "structured_data": "Nom, prix et stock décrits dans un format standard (schema.org) que lisent Google, Bing et les assistants IA comme ChatGPT.",
        "agent_ready": "UCP (Universal Commerce Protocol) : le standard de Google et Shopify qui permet aux assistants IA (Gemini, ChatGPT…) de chercher dans votre catalogue et d'acheter chez vous.",
    }
    for k in kpis:
        k.method = STOCK_METHODS.get(page.cms, STOCK_METHOD_DEFAULT) if k.id == "unavailable" else METHODS.get(k.id, "")
        k.about = about.get(k.id, "")
    return kpis


MIN_MEASURED_WEIGHT = 50  # below half of the points measured, a score would mean nothing


def measured_weight(kpis: Iterable[Kpi]) -> int:
    return sum(k.weight for k in kpis if k.status in STATUS_VALUE and k.weight)


# The checks that make the audit: when one can't be measured (blocks drawn by the browser, a site's
# own engine), it counts as half - neither a win nor a loss. Excluding it would reward the sites
# that can't be read.
NEUTRAL_WHEN_UNKNOWN = {"similar_block", "behavior_block", "personalization"}


def score(kpis: Iterable[Kpi]) -> Optional[int]:
    kpis = list(kpis)
    by_id = {k.id: k for k in kpis}
    if all(by_id.get(i) is None or by_id[i].status == UNKNOWN for i in ("self_reco", "similar_block", "behavior_block")):
        return None  # nothing about the recommendations themselves could be read
    weighted = [(k.weight, STATUS_VALUE[k.status]) for k in kpis if k.status in STATUS_VALUE and k.weight]
    weighted += [(k.weight, 0.5) for k in kpis if k.status == UNKNOWN and k.id in NEUTRAL_WHEN_UNKNOWN]
    total = sum(w for w, _ in weighted)
    if total < MIN_MEASURED_WEIGHT:
        return None
    value = round(100 * sum(w * v for w, v in weighted) / total) if total else 0
    if any(k.id == "self_reco" and k.status == FAIL for k in kpis):
        value = min(value, SELF_RECO_SCORE_CAP)
    return value


def grade(value: Optional[int]) -> Optional[str]:
    if value is None:
        return None
    return "A" if value >= 85 else "B" if value >= 70 else "C" if value >= 55 else "D" if value >= 40 else "E"


NEXT_STEPS = {
    "woocommerce": {"title": "L'extension LIKYLY pour WooCommerce", "text": "Deux blocs sur chaque fiche : « Vous aimerez aussi » (produits proches, dès le premier jour) et « Les clients ont aussi aimé », appris de vos commandes et adapté à chaque visiteur. Sans code.", "href": "/wordpress", "cta": "Découvrir l'extension"},
    "wordpress": {"title": "L'extension LIKYLY pour WordPress", "text": "« À lire ensuite » à la fin de vos articles : les articles proches, puis ceux que vos lecteurs ont lus ensuite. Sans code.", "href": "/wordpress", "cta": "Découvrir l'extension"},
    "shopify": {"title": "LIKYLY pour Shopify", "text": "Une section « Vous aimerez aussi » et une section « Les clients ont aussi aimé » dans votre thème, adaptées à chaque visiteur, et les achats suivis par le pixel de Shopify.", "href": "/docs/installation", "cta": "Voir l'installation"},
    "magento": {"title": "LIKYLY pour Magento", "text": "L'assistant d'installation envoie votre catalogue et ajoute les deux blocs à votre thème : npx @likyly/wizard.", "href": "/docs/installation", "cta": "Voir l'installation"},
}
DEFAULT_NEXT_STEP = {"title": "Installez LIKYLY sur votre site", "text": "Les deux blocs, produits proches et « les clients ont aussi aimé », adaptés à chaque visiteur. Un assistant détecte votre technologie et les installe : npx @likyly/wizard, ou un SDK pour chaque langage.", "href": "/docs/installation", "cta": "Voir l'installation"}


def needs_rendering(facts: PageFacts) -> bool:
    """Read the page in a browser too when what makes the audit - a block related to the item and one
    learned from other visitors - can't be read in its HTML, or a block is left empty for JavaScript."""
    readable = [b for b in facts.blocks if b.items]
    has_similar = any(b.kind == "similar" for b in readable)
    has_behavior = any(b.kind in ("behavior", "complement") for b in readable)
    return not (has_similar and has_behavior) or any(b.js_only for b in facts.blocks) or facts.text_chars < 600


def merge_rendered(static: PageFacts, rendered: PageFacts) -> PageFacts:
    """The rendered page's blocks (what visitors see), with what robots that don't run JavaScript read
    taken from the HTML: structured data, and which blocks exist only once JavaScript has run."""
    static_titles = {_signature(b.title) for b in static.blocks if b.items}
    static_keys = [{i.key for i in b.items} for b in static.blocks if b.items]
    for block in rendered.blocks:
        keys = {i.key for i in block.items}
        block.browser_only = bool(block.items) and _signature(block.title) not in static_titles and not any(len(keys & k) >= 2 for k in static_keys)
    # A block of the HTML that the rendered page doesn't show (another version of the page served
    # to browsers, a consent layer over it...) is still there for robots: kept.
    rendered_titles = {_signature(b.title) for b in rendered.blocks if b.items}
    rendered_keys = [{i.key for i in b.items} for b in rendered.blocks if b.items]
    for block in static.blocks:
        keys = {i.key for i in block.items}
        if block.items and _signature(block.title) not in rendered_titles and not any(len(keys & k) >= 2 for k in rendered_keys):
            rendered.blocks.append(block)
    rendered.structured = static.structured
    if static.page_type != "other":
        rendered.page_type = static.page_type
    rendered.item_name = static.item_name or rendered.item_name
    if static.cms != "unknown":
        rendered.cms = static.cms
    known = {v["id"] for v in static.vendors}
    rendered.vendors = static.vendors + [v for v in rendered.vendors if v["id"] not in known]
    rendered.canonical_key = static.canonical_key or rendered.canonical_key
    rendered.shopify_product_id = static.shopify_product_id or rendered.shopify_product_id
    rendered.js_reco_signals = static.js_reco_signals or rendered.js_reco_signals
    rendered.text_chars = max(static.text_chars, rendered.text_chars)
    rendered.rendered = True
    return rendered


def _blocked(status: int) -> AuditError:
    return AuditError(f"Le site refuse les visites automatiques (erreur {status}) : sa protection anti-robots empêche notre robot de lire la page." + BLOCKED_HINT, blocked=True)


CHALLENGE_ERROR = "Le site présente une vérification anti-robots à notre robot d'analyse : il ne lit pas les pages protégées ainsi." + BLOCKED_HINT


def run_audit(raw_url: str, fetch: Fetch, allow_private: bool = False, render: Optional[Render] = None,
              provided: Optional[Page] = None, source: str = "robot") -> dict[str, Any]:
    """The whole audit of one page, as stored. `fetch` is SafeFetcher.get in production, a fake in tests.
    `render` (optional) shows a page as a browser does. `provided`: the page as the visitor's own browser
    gave it (source "browser": rendered, from the bookmarklet; "paste": its source code) - for sites
    that turn robots away."""
    url = normalize_input_url(raw_url, allow_private)
    if provided is None:
        page = fetch(url)
        if page.status in (401, 403, 429, 503):
            raise _blocked(page.status)
        if page.status >= 400:
            raise AuditError(f"La page répond avec une erreur {page.status} : vérifiez l'adresse.")
        if "html" not in (page.content_type or "text/html").lower():
            raise AuditError("Cette adresse ne mène pas à une page web.")
    else:
        page = provided
    if is_challenge(page.text):
        if provided is not None:
            raise AuditError("Ce code est celui de la page de vérification anti-robots du site, pas celui de la fiche : ouvrez la fiche elle-même dans votre navigateur, puis recommencez.")
        raise AuditError(CHALLENGE_ERROR, blocked=True)
    facts = analyze_page(page)
    facts.rendered = source == "browser"
    notes: list[str] = []
    stock_checked = False

    # Recommendations drawn by JavaScript: read the page as a browser shows it.
    rendered = source == "browser"
    if render is not None and provided is None and needs_rendering(facts):
        try:
            shown = render(page.url)
            if shown.challenge:
                facts.render_failed = True
                notes.append("Notre navigateur d'audit a reçu une vérification anti-robots : seuls les blocs présents dans le HTML ont été lus.")
            elif shown.status >= 400:
                facts.render_failed = True
                notes.append(f"Notre navigateur d'audit a reçu une erreur {shown.status} : seuls les blocs présents dans le HTML ont été lus.")
            else:
                facts = merge_rendered(facts, analyze_page(shown))
                rendered = True
        except AuditError as error:
            facts.render_failed = True
            notes.append(f"Lecture dans un navigateur impossible ({error}) : seuls les blocs présents dans le HTML ont été lus.")
    if facts.text_chars < 600 and not facts.blocks:
        raise AuditError("Cette page est construite entièrement en JavaScript : son contenu n'est pas lisible sans navigateur, l'audit ne peut pas l'analyser.")

    fill_shopify_sections(fetch, facts)

    def open_page(target_url: str) -> Page:
        """Another page, read the way the audited page was: in a browser when its blocks needed one."""
        if rendered and render is not None:
            shown = render(target_url)
            if shown.challenge:
                raise AuditError("le site a présenté une vérification anti-robots")
            return shown
        return fetch(target_url)

    # Seen, not assumed: the page of another recommended item, and the same page loaded again.
    reload: Optional[PageFacts] = None
    other: Optional[PageFacts] = None
    target = _comparison_target(facts)
    if target is not None:
        try:
            second = open_page(target.url)
            if second.status == 200 and "html" in (second.content_type or "text/html").lower():
                other = analyze_page(second)
                fill_shopify_sections(fetch, other)
            else:
                notes.append(f"La fiche « {target.name or target.url} » n'a pas pu être ouverte pour comparer les blocs (erreur {second.status}).")
        except AuditError as error:
            notes.append(f"La fiche « {target.name or target.url} » n'a pas pu être ouverte pour comparer les blocs : {error}")
        try:
            again = open_page(page.url)
            if again.status == 200:
                reload = analyze_page(again)
                fill_shopify_sections(fetch, reload)
        except AuditError:
            reload = None
        compare_blocks(facts, reload, other, (other.item_name if other else "") or target.name)

    if facts.cms == "shopify":
        items = [i for b in facts.blocks for i in b.items]
        if items:
            stock_checked = shopify_flag_out_of_stock(fetch, page.url, items)

    js_blocks = [b for b in facts.blocks if b.js_only]
    if js_blocks:
        if rendered:
            notes.append(f"{_plural(len(js_blocks), 'bloc reste vide', 'blocs restent vides')} même dans notre navigateur d'audit : souvent un bloc lié à "
                         "l'historique du visiteur (articles vus récemment), vide pour une première visite, ou un bloc qui attend une action.")
        else:
            notes.append(f"{_plural(len(js_blocks), 'bloc se remplit', 'blocs se remplissent')} en JavaScript : leur contenu n'a pas pu être lu sans navigateur.")
    sponsored = [b for b in facts.blocks if b.sponsored]
    if sponsored:
        notes.append(f"{_quote(sponsored)} : emplacement{'s' if len(sponsored) > 1 else ''} sponsorisé{'s' if len(sponsored) > 1 else ''} (publicité), compté{'s' if len(sponsored) > 1 else ''} comme recommandation{'s' if len(sponsored) > 1 else ''} de la page.")
    browser_only = [b for b in facts.blocks if b.browser_only]
    if browser_only and source != "browser":
        verb = "n'existe" if len(browser_only) == 1 else "n'existent"
        notes.append(f"{_quote(browser_only)} {verb} qu'une fois le JavaScript exécuté : "
                     "lu dans notre navigateur d'audit. Google le voit ; la plupart des assistants IA, qui lisent le HTML, non.")
    if source == "browser":
        notes.append("Page lue depuis votre navigateur, telle qu'elle s'affichait chez vous : le site refuse notre robot.")
    elif source == "paste":
        notes.append("Page lue depuis le code source collé : les blocs que le JavaScript ajoute après coup n'y figurent pas.")

    origin = f"{urlsplit(page.url).scheme}://{urlsplit(page.url).netloc}/"
    # A yes or a no: published (200 with a JSON profile) or not - a file that doesn't answer isn't published.
    ucp = False
    try:
        well_known = fetch(origin + ".well-known/ucp")
        ucp = well_known.status == 200 and well_known.text.lstrip().startswith("{")
    except AuditError:
        ucp = False

    kpis = build_kpis(facts, ucp, stock_checked)
    total = score(kpis)
    return {
        "url": url,
        "final_url": page.url,
        "host": urlsplit(page.url).hostname,
        "source": source,
        "rendered": rendered,
        "cms": {"id": facts.cms, "label": CMS_LABELS.get(facts.cms, facts.cms)},
        "page_type": {"id": facts.page_type, "label": PAGE_TYPE_LABELS.get(facts.page_type, facts.page_type)},
        "item_name": facts.item_name,
        "vendors": facts.vendors,
        "score": total,
        "grade": grade(total),
        "partial": total is None,
        "kpis": [k.public(full=True) for k in kpis],
        "blocks": [b.public() for b in facts.blocks],
        "notes": notes,
        "compared_with": {"url": target.url, "name": (other.item_name if other else "") or target.name} if target is not None and other is not None else None,
        "next_step": NEXT_STEPS.get(facts.cms, DEFAULT_NEXT_STEP),
    }


def teaser(result: dict[str, Any]) -> dict[str, Any]:
    """What the page shows before the email: the verdicts, not the explanations or the evidence."""
    return {
        **{k: result.get(k) for k in ("url", "final_url", "host", "source", "rendered", "cms", "page_type", "item_name", "vendors", "score", "grade", "partial")},
        "kpis": [{k: v for k, v in kpi.items() if k in ("id", "label", "about", "status", "summary", "weight", "method")} for kpi in result["kpis"]],
        "block_count": len(result["blocks"]),
    }
