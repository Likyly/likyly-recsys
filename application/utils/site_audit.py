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
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup, Tag

USER_AGENT = "Mozilla/5.0 (compatible; LikylyAudit/1.0; +https://www.likyly.com/audit)"
TIMEOUT_SECONDS = 8.0
MAX_BODY_BYTES = 3 * 1024 * 1024
MAX_REDIRECTS = 5


class AuditError(Exception):
    """A reason the audit can't run at all, worded for the visitor (shown as is)."""


# --------------------------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------------------------

@dataclass
class Page:
    url: str  # final URL, after redirects
    status: int
    text: str
    content_type: str = ""


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
                raise AuditError(f"Le site {host} ne répond pas à notre robot d'analyse : il est trop lent, ou il ignore les visites automatiques.")
            except httpx.HTTPError:
                raise AuditError(f"Impossible de joindre {host}.")
        raise AuditError("La page redirige trop de fois.")


Fetch = Callable[[str], Page]


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

# Headings that announce a recommendation block. Lowercased, accents kept (we compare lowercased text).
HEADING_PATTERNS = [
    r"vous aimerez (aussi|peut-être|également)", r"vous (pourriez|devriez) aimer", r"tu (aimeras|vas aimer|pourrais aimer)", r"produits? (similaires|apparentés|associés|liés|recommandés|complémentaires)",
    r"articles? (similaires|recommandés|liés|associés)", r"(à|a) lire aussi", r"lire (aussi|ensuite)", r"sur le même (thème|sujet)",
    r"(les )?(clients|lecteurs|internautes|visiteurs)( qui)? ont (aussi|également) (acheté|aimé|consulté|lu|vu|regardé)",
    r"(souvent|fréquemment) achetés? ensemble", r"complétez (votre|le|ton) (look|panier|commande)", r"recommandés? pour (vous|toi)", r"(sélectionnés?|choisis) pour (vous|toi)",
    r"^\W*pour (vous|toi)\W*$", r"nos recommandations", r"(récemment|derniers?) (consultés|vus)", r"vus récemment", r"(autres|d'autres) (produits|articles|livres|spectacles|événements)",
    r"vous avez aimé", r"ça pourrait (vous|te) plaire", r"ont aussi (aimé|acheté|lu|vu|consulté|regardé|réservé)", r"que (lire|voir|écouter) (après|ensuite)",
    r"autres? (livres|produits|articles|œuvres|titres|albums|spectacles|événements) (de|du|des|classés|dans)", r"dans la même (catégorie|collection|série|gamme|rubrique)",
    r"du même (auteur|artiste|rayon|univers|thème)", r"suggestions? (pour (vous|toi)|de produits|d'articles)", r"^\W*accessoires?( recommandés)?\W*$",
    r"you (may|might|will|could) (also )?(like|love|enjoy)", r"related (products|items|articles|posts|stories|books|events)", r"similar (products|items|articles|books|events)",
    r"customers (who|also)", r"(frequently|often) bought together", r"recommended (for you|products|items|reading)", r"(read|watch) next", r"more like this",
    r"recently viewed", r"complete the look", r"(people|readers|shoppers) also (bought|liked|viewed|read)", r"picked for you", r"^\W*for you\W*$",
    r"\bupsell", r"cross-?sell",
]
HEADING_RE = re.compile("|".join(f"(?:{p})" for p in HEADING_PATTERNS), re.IGNORECASE)

# What a block's title says about how it is chosen.
#   behavior: what other customers did ("ont aussi aimé", "achetés ensemble") - the second block
#   personal: what this visitor did ("vus récemment") - adapts to each visitor by nature
#   claims_personal: "Pour vous" - only true if a personalization tool is loaded
#   similar: everything else that announces recommendations - related to the item shown
BEHAVIOR_HEADING_RE = re.compile(r"ont (aussi|également) (aimé|acheté|lu|vu|consulté|regardé|réservé)|also (bought|liked|viewed|read)|customers who|bought together|achetés? ensemble", re.IGNORECASE)
PERSONAL_HEADING_RE = re.compile(r"récemment (consultés|vus)|derniers? (consultés|vus)|vus récemment|recently viewed", re.IGNORECASE)
CLAIMS_PERSONAL_RE = re.compile(r"pour (vous|toi)|for you|picked for you|choisis pour|sélectionnés? pour", re.IGNORECASE)

# Shopify theme sections that show a collection the merchant picked - the same for every product
# and every visitor, whatever their title says.
FIXED_SECTION_RE = re.compile(r"featured[-_]?collection|product[-_]?list|collection[-_]?list|featured[-_]?products", re.IGNORECASE)

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
    r"tag|tags|author|auteur|feed|wp-admin|wp-login|cdn-cgi)(/|$)|add-to-cart|\.(jpe?g|png|webp|gif|svg|css|js|pdf)$",
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
    element: Optional[Tag] = field(default=None, repr=False, compare=False)

    def public(self) -> dict[str, Any]:
        return {
            "title": self.title, "source": self.source, "kind": self.kind, "js_only": self.js_only, "claims_personal": self.claims_personal,
            "items": [{"name": i.name, "url": i.url, "unavailable": i.unavailable} for i in self.items[:24]],
            "item_count": len(self.items),
        }


def item_key(url: str) -> str:
    """Two links to the same item compare equal: no scheme/host/query/fragment/trailing slash,
    and Shopify's /collections/x/products/y is /products/y."""
    path = urlsplit(url).path.lower().rstrip("/") or "/"
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


EVENT_TYPES = {"Event", "MusicEvent", "TheaterEvent", "SportsEvent", "ComedyEvent", "DanceEvent", "Festival", "ScreeningEvent", "ExhibitionEvent", "BusinessEvent", "EducationEvent", "SocialEvent", "LiteraryEvent", "ChildrensEvent"}
ARTICLE_TYPES = {"Article", "NewsArticle", "BlogPosting", "Report", "ScholarlyArticle", "TechArticle", "AnalysisNewsArticle", "OpinionNewsArticle", "ReviewNewsArticle"}
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
        else:
            page_type = "other"
        if not name:
            og_title = soup.find("meta", attrs={"property": "og:title"})
            heading = soup.find("h1")
            name = (og_title.get("content", "") if og_title else "") or (_text(heading) if heading else "")
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
    absolute = urljoin(base_url, href)
    parts = urlsplit(absolute)
    if parts.scheme not in ("http", "https") or (parts.hostname or "").removeprefix("www.") != host:
        return None
    if (parts.path or "/").strip("/") == "" or NON_ITEM_PATH.search(parts.path) or "add-to-cart" in parts.query:
        return None
    label = _text(anchor) or anchor.get("aria-label", "") or anchor.get("title", "")
    if not anchor.find("img") and not anchor.find(["h2", "h3", "h4"]) and (not label or MORE_LINK_RE.match(label)):
        return None  # "Voir plus", "See all", an icon: a link to the full list, not an item
    return item_key(absolute), absolute


def _card_of(anchor: Tag, node: Tag, base_url: str, host: str, key: str) -> Tag:
    """The item's card: the largest element under `node` whose item links all point to this item
    (the image link and the title link of one card share it; two cards for the same item don't)."""
    card: Tag = anchor
    for parent in anchor.parents:
        if parent is node or not isinstance(parent, Tag):
            break
        keys = {link[0] for a in parent.find_all("a", href=True) if (link := _item_link(a, base_url, host))}
        if keys - {key}:
            break
        card = parent
    return card


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


def _items_in(node: Tag, base_url: str) -> list[Item]:
    """Links to items of the same site inside `node`, one per card: the image and the title of a
    card usually both link to the item, and count once; two cards for the same item count twice
    (a real duplicate)."""
    host = (urlsplit(base_url).hostname or "").removeprefix("www.")
    items: list[Item] = []
    cards: list[Any] = []
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
        if same_card is not None:
            existing = items[same_card]
            existing.name = existing.name or name
            existing.unavailable = existing.unavailable or unavailable
            continue
        items.append(Item(key=key, url=absolute, name=name, unavailable=unavailable))
        cards.append(card)
    return items


def _nearest_heading_text(node: Tag) -> str:
    heading = node.find(["h2", "h3", "h4", "h5"]) or node.find(class_=re.compile(r"title|heading", re.I))
    return _text(heading)[:120] if heading else ""


def _in_fixed_section(node: Tag) -> bool:
    """Inside a theme section that shows a collection picked by the merchant (Shopify "featured
    collection" / "product list" sections): the same products for every page and visitor."""
    for element in [node, *node.parents]:
        if not isinstance(element, Tag):
            continue
        attrs = f"{element.get('id', '')} {' '.join(element.get('class', []))}"
        if "section" in attrs.lower() and FIXED_SECTION_RE.search(attrs):
            return True
    return False


def _clean_title(text: str) -> str:
    """Drop slider button labels a heading carries ("Précédent POUR TOI: Suivant")."""
    cleaned = re.sub(r"^(précédent|previous)\s+|\s+(suivant|next)$", "", text, flags=re.IGNORECASE).strip(" :")
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
    for heading in soup.find_all(["h2", "h3", "h4", "h5", "p", "span", "div"]):
        if heading.find(["h2", "h3", "h4", "div", "ul", "section"]) or heading.find_parent("a") is not None or len(heading.find_all("a")) > 1:
            continue  # a wrapper, a link, or a menu line - not a block title
        text = _text(heading)
        if not (3 <= len(text) <= 90) or not HEADING_RE.search(text):
            continue
        chrome = heading.find_parent(["nav", "footer", "header", "form"])
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
        if container is None or container.name in ("body", "html", "main") or len({i.key for i in items}) < 2:
            continue
        claims = bool(CLAIMS_PERSONAL_RE.search(text))
        kind = "behavior" if BEHAVIOR_HEADING_RE.search(text) else "personal" if PERSONAL_HEADING_RE.search(text) else "similar"
        if kind == "similar" and (claims or _in_fixed_section(container)):
            kind = "fixed" if _in_fixed_section(container) else "claims_personal"
        candidates.append(Block(title=_clean_title(text), source="heading", kind=kind, items=items, claims_personal=claims, element=container))

    # A section title ("Que lire après ?") and its sub-blocks ("Ceux qui ont aimé…", "Du même
    # auteur") both match: keep the most specific blocks, never the section that wraps them.
    innermost = [b for b in candidates if not any(o is not b and o.element is not b.element and b.element in o.element.parents for o in candidates)]
    for block in innermost:
        add(block)
    return blocks


def analyze_page(page: Page, soup: Optional[BeautifulSoup] = None) -> PageFacts:
    soup = soup or BeautifulSoup(page.text, "lxml")
    html = page.text
    cms = detect_cms(html, soup)
    vendors = detect_vendors(html)
    nodes = _json_ld(soup)
    page_type, name, structured = detect_page_type(soup, html, page.url, nodes, cms)
    canonical_key = item_key(_canonical(soup, page.url))
    blocks = find_blocks(soup, page.url, cms, vendors, canonical_key)
    shopify_id = None
    if cms == "shopify":
        match = re.search(r'"product"\s*:\s*\{\s*"id"\s*:\s*(\d+)', html) or re.search(r"product_id=(\d+)", html) or re.search(r'"productId"\s*:\s*"?(\d+)', html)
        shopify_id = match.group(1) if match else None
    html_tag = soup.find("html")
    return PageFacts(
        url=page.url, canonical_key=canonical_key, page_type=page_type, item_name=name, structured=structured,
        cms=cms, vendors=vendors, blocks=blocks, shopify_product_id=shopify_id,
        lang=(html_tag.get("lang", "") if html_tag else "")[:10],
    )


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

    def public(self, full: bool) -> dict[str, Any]:
        data = {"id": self.id, "label": self.label, "status": self.status, "summary": self.summary, "weight": self.weight, "method": self.method}
        if full:
            data.update({"detail": self.detail, "advice": self.advice})
        return data


METHODS = {
    "self_reco": "Adresse et nom du produit affiché comparés à chaque élément des blocs.",
    "similar_block": "Blocs repérés par leur titre et le code du CMS ; leur origine (catalogue, règle, collection fixe) est lue dans le thème.",
    "behavior_block": "Bloc titré « ont aussi aimé / acheté », ou bloc natif du CMS fondé sur les achats.",
    "personalization": "Outil de personnalisation chargé par la page (Nosto, Clerk, LIKYLY…) ou bloc « vus récemment ».",
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
BEHAVIOR_TITLE = {"product": "« Les clients ont aussi aimé / acheté »", "book": "« Les lecteurs ont aussi aimé »", "article": "« Les lecteurs ont aussi lu »", "event": "« Les spectateurs ont aussi réservé »"}
SIMILAR_TITLE = {"product": "« Produits similaires »", "book": "« Livres similaires »", "article": "« Articles similaires »", "event": "« Événements similaires »"}
UNAVAILABLE_LABEL = {"product": "Produits en rupture de stock recommandés", "book": "Livres en rupture de stock recommandés", "event": "Événements complets ou passés recommandés"}


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
    similar_title = SIMILAR_TITLE.get(page.page_type, "« Éléments similaires »")
    personal_vendor = next((v for v in page.vendors if v["personalized"]), None)
    readable = [b for b in page.blocks if b.items]
    js_only = [b for b in page.blocks if b.js_only]
    by_kind = lambda *kinds: [b for b in page.blocks if b.kind in kinds]  # noqa: E731
    kpis: list[Kpi] = []

    # 1. The item recommended on its own page - the first thing to get right.
    if page.page_type in ("product", "book", "event", "article") and readable:
        same = [b for b in readable if any(i.key == page.canonical_key for i in b.items)]
        name = page.item_name.strip().lower()
        twins = [b for b in readable if name and any(i.key != page.canonical_key and i.name.strip().lower() == name for i in b.items)]
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

    # 2. Block 1: related to the item (content, category - the cold start).
    similar = [b for b in by_kind("similar", "manual") if b.items]
    fixed = by_kind("fixed", "claims_personal")
    if similar:
        manual_only = all(b.kind == "manual" for b in similar)
        # WooCommerce's "related products" are drawn at random among the same categories/tags.
        random_only = all(b.kind == "manual" or (b.platform == "woocommerce" and b.source == "native") for b in similar) and not manual_only
        if manual_only:
            status, summary = WARN, f"Présent : {_quote(similar)}, choisi à la main"
            detail = "Une sélection faite à la main, produit par produit : longue à tenir à jour, jamais complète, et sans rapport avec ce qui se vend."
            advice = "Calculez ce bloc automatiquement à partir du catalogue (contenu, attributs), pour tous les produits."
        elif random_only:
            status, summary = WARN, f"Présent : {_quote(similar)}, mais tiré au hasard dans la catégorie"
            detail = ("Les « produits apparentés » de WooCommerce sont tirés au hasard parmi les produits des mêmes catégories ou étiquettes, à chaque affichage : "
                      f"ils ne sont pas choisis pour leur ressemblance avec {the} affiché.")
            advice = f"Choisissez les {many} proches par leur contenu (description, attributs, style), pas au hasard."
        else:
            status, summary = PASS, f"Présent : {_quote(similar)}"
            detail = f"Un bloc lié à {the} affiché : c'est la base, utile même pour un visiteur dont on ne sait rien (démarrage à froid)."
            advice = ""
        kpis.append(Kpi("similar_block", f"Bloc {similar_title}", 20, status, summary, detail, advice))
    elif fixed:
        kpis.append(Kpi("similar_block", f"Bloc {similar_title}", 20, FAIL,
                        f"Absent : {_quote(fixed)} est une collection fixe, la même quel que soit {the} affiché",
                        f"Le bloc montre des {many} choisis une fois pour toutes dans le thème, sans rapport avec {the} que le visiteur regarde.",
                        f"Remplacez-le par des {many} proches de celui affiché : même usage, même style, même gamme de prix."))
    elif js_only:
        kpis.append(Kpi("similar_block", f"Bloc {similar_title}", 20, WARN,
                        f"{_plural(len(js_only), 'bloc chargé', 'blocs chargés')} en JavaScript : contenu illisible pour les robots",
                        "Un emplacement existe, mais il ne se remplit qu'une fois la page affichée dans un navigateur : son contenu ne peut pas être contrôlé ici, "
                        "et les moteurs de recherche comme les agents IA n'y voient rien.",
                        "Rendez au moins ce bloc côté serveur."))
    else:
        kpis.append(Kpi("similar_block", f"Bloc {similar_title}", 20, FAIL,
                        "Absent",
                        f"Le visiteur qui hésite sur {the} n'a aucune alternative proche sous les yeux : s'il ne trouve pas son bonheur, il repart.",
                        f"Ajoutez sous {the} un bloc de 4 à 8 {many} proches (contenu, catégorie, attributs)."))

    # 3. Block 2: learned from what other customers did.
    behavior = by_kind("behavior")
    if behavior:
        kpis.append(Kpi("behavior_block", f"Bloc {behavior_title}", 20, PASS,
                        f"Présent : {_quote(behavior)}",
                        "Un bloc appris de ce que font les autres clients : il révèle des associations que la fiche seule ne dit pas."))
    elif js_only:
        kpis.append(Kpi("behavior_block", f"Bloc {behavior_title}", 20, WARN,
                        f"Non lisible : {_plural(len(js_only), 'bloc chargé', 'blocs chargés')} en JavaScript",
                        "Un bloc se remplit dans le navigateur : on ne peut pas dire ici s'il montre ce que les autres clients ont aimé ou acheté.",
                        f"Vérifiez qu'un des blocs est bien {behavior_title}, appris des visites, paniers et commandes."))
    else:
        kpis.append(Kpi("behavior_block", f"Bloc {behavior_title}", 20, FAIL,
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
    else:
        why = (f"Le bloc {_quote(claims)} s'intitule « pour vous », mais aucun outil de personnalisation n'est chargé : tous les visiteurs voient la même chose. "
               if claims else "")
        kpis.append(Kpi("personalization", "Personnalisation", 20, FAIL,
                        "Aucune : tous les visiteurs voient les mêmes recommandations",
                        why + "Rien sur la page ne tient compte du visiteur : aucun outil de personnalisation n'est chargé, et les blocs viennent de règles, de "
                        "choix manuels ou de collections fixes. Un bloc « similaires » identique pour tous est normal (démarrage à froid) ; mais au moins un bloc "
                        "doit s'adapter à ce que le visiteur a regardé et à ses goûts.",
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
        kpis.append(Kpi("unavailable", "Éléments indisponibles recommandés", 5, UNKNOWN, "Sans objet pour cette page", ""))
    elif flagged:
        kpis.append(Kpi("unavailable", label, 5, FAIL,
                        _plural(len(flagged), one + " recommandé n'est plus disponible", many + " recommandés ne sont plus disponibles") + " : " + ", ".join(i.name or i.url for i in flagged[:3]),
                        "En rupture de stock (ou complet, pour un événement) : le visiteur clique, et ne peut pas acheter. Le lien fonctionne, c'est le produit qui manque.",
                        "Filtrez les éléments en rupture dans chaque bloc."))
    elif readable and knows_stock:
        kpis.append(Kpi("unavailable", label, 5, PASS, "Tout ce qui est recommandé est en stock", "Chaque suggestion peut être achetée."))
    else:
        kpis.append(Kpi("unavailable", label, 5, UNKNOWN, "Stock non lisible depuis la page", "La page n'indique pas si les éléments recommandés sont en stock."))

    # 7. Block size, counted on what the page shows.
    if readable:
        counts = [len({i.key for i in b.items}) for b in readable]
        span = f"{min(counts)} {many}" if min(counts) == max(counts) else f"entre {min(counts)} et {max(counts)} {many}"
        if min(counts) < 4:
            kpis.append(Kpi("block_size", "Taille des blocs", 3, WARN, f"{span.capitalize()} affichés par bloc : trop court",
                            "Avec moins de 4 suggestions, un bloc laisse peu de chances de trouver quelque chose.", f"Visez 4 à 8 {many} par bloc."))
        elif max(counts) > 16:
            kpis.append(Kpi("block_size", "Taille des blocs", 3, WARN, f"{span.capitalize()} affichés par bloc : trop long",
                            "Au-delà d'une quinzaine, les meilleures suggestions se perdent.", f"Visez 4 à 12 {many}, les meilleurs en premier."))
        else:
            kpis.append(Kpi("block_size", "Taille des blocs", 3, PASS, f"{span.capitalize()} affichés par bloc", "Assez de choix sans noyer le visiteur."))
    else:
        kpis.append(Kpi("block_size", "Taille des blocs", 3, UNKNOWN, "Non vérifiable sans bloc lisible", ""))

    # 8. Structured data (search engines and AI agents read it).
    s = page.structured
    if page.page_type in ("product", "book", "event", "article") and s.get("type"):
        sells = page.page_type == "product" or s.get("has_offer")
        needed = ["has_name", "has_image"] + (["has_offer", "has_price", "has_availability"] if sells else ["has_date"] if page.page_type in ("article", "event") else [])
        missing = [k for k in needed if not s.get(k)]
        names = {"has_name": "nom", "has_image": "image", "has_offer": "offre", "has_price": "prix", "has_availability": "stock", "has_date": "date"}
        if not missing:
            kpis.append(Kpi("structured_data", "Données lisibles par les machines", 2, PASS, "Données structurées complètes (schema.org)",
                            "Google, Bing et les agents IA (ChatGPT, Gemini…) comprennent ce que la page présente.",
                            "" if s.get("related_in_markup") else "Ajoutez les éléments liés (isRelatedTo, isSimilarTo) pour que les agents connaissent aussi vos associations."))
        else:
            kpis.append(Kpi("structured_data", "Données lisibles par les machines", 2, WARN, "Incomplètes : " + ", ".join(names[k] for k in missing) + " manquant",
                            "Les moteurs et les agents IA lisent ces données pour présenter vos produits.", "Complétez le balisage schema.org de la page."))
    elif page.page_type in ("product", "book", "event", "article"):
        kpis.append(Kpi("structured_data", "Données lisibles par les machines", 2, FAIL, "Pas de données structurées schema.org",
                        "Moteurs de recherche et agents IA doivent deviner ce que la page présente.", "Ajoutez un bloc JSON-LD avec nom, image, prix et stock."))
    else:
        kpis.append(Kpi("structured_data", "Données lisibles par les machines", 2, UNKNOWN, "Analysez une fiche produit ou un article pour ce contrôle", ""))

    # 9. AI shopping agents (UCP) - informative, not scored.
    if ucp is True:
        kpis.append(Kpi("agent_ready", "Prêt pour les agents IA (UCP)", 0, PASS, "Profil UCP publié (/.well-known/ucp)",
                        "Le site se déclare aux agents d'achat IA (Gemini, mode IA de Google…) via le Universal Commerce Protocol."))
    elif ucp is False:
        kpis.append(Kpi("agent_ready", "Prêt pour les agents IA (UCP)", 0, INFO, "Pas de profil UCP",
                        "Le Universal Commerce Protocol (Google, Shopify…) permet aux agents d'achat IA de chercher dans votre catalogue et d'acheter chez vous. "
                        "Il se déploie d'abord aux États-Unis : à surveiller.",
                        "Shopify l'active d'office ; ailleurs, il passe par une extension."))
    for k in kpis:
        k.method = STOCK_METHODS.get(page.cms, STOCK_METHOD_DEFAULT) if k.id == "unavailable" else METHODS.get(k.id, "")
    return kpis


def score(kpis: Iterable[Kpi]) -> int:
    kpis = list(kpis)
    weighted = [(k.weight, STATUS_VALUE[k.status]) for k in kpis if k.status in STATUS_VALUE and k.weight]
    total = sum(w for w, _ in weighted)
    value = round(100 * sum(w * v for w, v in weighted) / total) if total else 0
    if any(k.id == "self_reco" and k.status == FAIL for k in kpis):
        value = min(value, SELF_RECO_SCORE_CAP)
    return value


def grade(value: int) -> str:
    return "A" if value >= 85 else "B" if value >= 70 else "C" if value >= 55 else "D" if value >= 40 else "E"


NEXT_STEPS = {
    "woocommerce": {"title": "L'extension LIKYLY pour WooCommerce", "text": "Deux blocs sur chaque fiche : « Vous aimerez aussi » (produits proches, dès le premier jour) et « Les clients ont aussi aimé », appris de vos commandes et adapté à chaque visiteur. Sans code.", "href": "/wordpress", "cta": "Découvrir l'extension"},
    "wordpress": {"title": "L'extension LIKYLY pour WordPress", "text": "« À lire ensuite » à la fin de vos articles : les articles proches, puis ceux que vos lecteurs ont lus ensuite. Sans code.", "href": "/wordpress", "cta": "Découvrir l'extension"},
    "shopify": {"title": "LIKYLY pour Shopify", "text": "Une section « Vous aimerez aussi » et une section « Les clients ont aussi aimé » dans votre thème, adaptées à chaque visiteur, et les achats suivis par le pixel de Shopify.", "href": "/docs/installation", "cta": "Voir l'installation"},
    "magento": {"title": "LIKYLY pour Magento", "text": "L'assistant d'installation envoie votre catalogue et ajoute les deux blocs à votre thème : npx @likyly/wizard.", "href": "/docs/installation", "cta": "Voir l'installation"},
}
DEFAULT_NEXT_STEP = {"title": "Installez LIKYLY sur votre site", "text": "Les deux blocs, produits proches et « les clients ont aussi aimé », adaptés à chaque visiteur. Un assistant détecte votre technologie et les installe : npx @likyly/wizard, ou un SDK pour chaque langage.", "href": "/docs/installation", "cta": "Voir l'installation"}


def run_audit(raw_url: str, fetch: Fetch, allow_private: bool = False) -> dict[str, Any]:
    """The whole audit of one page, as stored. `fetch` is SafeFetcher.get in production, a fake in tests."""
    url = normalize_input_url(raw_url, allow_private)
    page = fetch(url)
    if page.status in (401, 403, 429, 503):
        raise AuditError(f"Le site refuse les visites automatiques (erreur {page.status}) : sa protection anti-robots empêche l'analyse.")
    if page.status >= 400:
        raise AuditError(f"La page répond avec une erreur {page.status} : vérifiez l'adresse.")
    if "html" not in (page.content_type or "text/html").lower():
        raise AuditError("Cette adresse ne mène pas à une page web.")
    facts = analyze_page(page)
    notes: list[str] = []
    stock_checked = False

    if facts.cms == "shopify":
        # The theme's recommendations section fills in with JavaScript from its data-url: fetch that
        # section to read exactly what it shows. Never invent a block the theme doesn't have.
        for block in facts.blocks:
            data_url = block.element.get("data-url") if block.platform == "shopify" and block.element is not None else None
            if block.js_only and data_url:
                items = shopify_section_items(fetch, page.url, data_url)
                if items:
                    block.items, block.js_only, block.source = items, False, "shopify_section"
        items = [i for b in facts.blocks for i in b.items]
        if items:
            stock_checked = shopify_flag_out_of_stock(fetch, page.url, items)

    js_blocks = [b for b in facts.blocks if b.js_only]
    if js_blocks:
        notes.append(f"{_plural(len(js_blocks), 'bloc se remplit', 'blocs se remplissent')} en JavaScript : leur contenu n'a pas pu être lu sans navigateur.")

    origin = f"{urlsplit(page.url).scheme}://{urlsplit(page.url).netloc}/"
    ucp: Optional[bool] = None
    try:
        well_known = fetch(origin + ".well-known/ucp")
        ucp = well_known.status == 200 and well_known.text.lstrip().startswith("{")
    except AuditError:
        ucp = None

    kpis = build_kpis(facts, ucp, stock_checked)
    total = score(kpis)
    return {
        "url": url,
        "final_url": page.url,
        "host": urlsplit(page.url).hostname,
        "cms": {"id": facts.cms, "label": CMS_LABELS.get(facts.cms, facts.cms)},
        "page_type": {"id": facts.page_type, "label": PAGE_TYPE_LABELS.get(facts.page_type, facts.page_type)},
        "item_name": facts.item_name,
        "vendors": facts.vendors,
        "score": total,
        "grade": grade(total),
        "kpis": [k.public(full=True) for k in kpis],
        "blocks": [b.public() for b in facts.blocks],
        "notes": notes,
        "next_step": NEXT_STEPS.get(facts.cms, DEFAULT_NEXT_STEP),
    }


def teaser(result: dict[str, Any]) -> dict[str, Any]:
    """What the page shows before the email: the verdicts, not the explanations or the evidence."""
    return {
        **{k: result[k] for k in ("url", "final_url", "host", "cms", "page_type", "item_name", "vendors", "score", "grade")},
        "kpis": [{k: v for k, v in kpi.items() if k in ("id", "label", "status", "summary", "weight", "method")} for kpi in result["kpis"]],
        "block_count": len(result["blocks"]),
    }
