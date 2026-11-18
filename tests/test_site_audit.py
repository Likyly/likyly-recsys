"""The free recommendation audit (www.likyly.com/audit): what it finds on a page, that it only ever
reaches public addresses, and the API flow - verdicts first, the full report once an email is given,
then the report emailed. No real network: pages are served by a fake fetch / httpx MockTransport."""
import uuid

import httpx
import pytest

import site_audit
import site_audits
from site_audit import AuditError, Page, SafeFetcher, is_challenge, run_audit


def fake_fetch(pages: dict):
    """url -> (status, body), or a list of them served one per call (a page that changes on reload)."""
    calls: dict[str, int] = {}

    def fetch(url: str) -> Page:
        answer = pages.get(url, (404, "<html><body>not found</body></html>"))
        if isinstance(answer, list):
            answer = answer[min(calls.get(url, 0), len(answer) - 1)]
            calls[url] = calls.get(url, 0) + 1
        status, body = answer
        content_type = "application/json" if body.lstrip().startswith("{") else "text/html; charset=utf-8"
        return Page(url=url, status=status, text=body, content_type=content_type)
    return fetch


def kpi(result, kpi_id):
    return next(k for k in result["kpis"] if k["id"] == kpi_id)


WOO_PRODUCT = """<!doctype html><html lang="fr"><head>
<meta name="generator" content="WooCommerce 9.1">
<link rel="canonical" href="https://boutique.example/produit/chaussure-trail/">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Product","name":"Chaussure Trail","image":"x.jpg","sku":"T1",
 "offers":{"@type":"Offer","price":"99","priceCurrency":"EUR","availability":"https://schema.org/InStock"}}</script>
</head><body class="product-template-default single-product woocommerce">
<h1>Chaussure Trail</h1>
<section class="related products"><h2>Produits apparentés</h2><ul class="products">
 <li class="product instock"><a href="/produit/chaussure-trail/"><img alt="Chaussure Trail"><h2 class="woocommerce-loop-product__title">Chaussure Trail</h2></a></li>
 <li class="product outofstock"><a href="/produit/sac-a-dos/"><h2 class="woocommerce-loop-product__title">Sac à dos</h2><span class="price">49 €</span></a></li>
 <li class="product instock"><a href="/produit/gourde/"><h2 class="woocommerce-loop-product__title">Gourde</h2></a><a href="/produit/gourde/">Lire la suite</a></li>
 <li class="product instock"><a href="/produit/gourde/"><h2 class="woocommerce-loop-product__title">Gourde</h2></a></li>
 <li class="product instock"><a href="/?add-to-cart=12">Ajouter au panier</a></li>
</ul></section></body></html>"""

WOO_HOME = """<html><head><meta name="generator" content="WooCommerce 9.1"></head><body class="home">
<h2>Nos meilleures ventes</h2><ul><li><a href="/produit/gourde/">Gourde</a></li></ul></body></html>"""


def test_woocommerce_product_page_errors_are_found():
    fetch = fake_fetch({
        "https://boutique.example/produit/chaussure-trail/": (200, WOO_PRODUCT),
        "https://boutique.example/": (200, WOO_HOME),
    })
    result = run_audit("boutique.example/produit/chaussure-trail/", fetch)

    assert result["cms"]["id"] == "woocommerce"
    assert result["page_type"]["id"] == "product"
    assert result["item_name"] == "Chaussure Trail"
    block = result["blocks"][0]
    assert block["title"] == "Produits apparentés" and block["kind"] == "similar"
    # image + title + "read more" of one card count once; the add-to-cart link is not an item
    assert [i["name"] for i in block["items"]] == ["Chaussure Trail", "Sac à dos", "Gourde", "Gourde"]
    assert kpi(result, "self_reco")["status"] == "fail"
    # nothing assumed from "it's WooCommerce": the other product page can't be opened -> not verified
    assert kpi(result, "similar_block")["status"] == "warn" and "non vérifié" in kpi(result, "similar_block")["summary"]
    assert result["blocks"][0]["verified"] is None and result["compared_with"] is None
    assert kpi(result, "behavior_block")["status"] == "fail"
    assert kpi(result, "duplicates")["status"] == "fail"
    assert kpi(result, "unavailable")["status"] == "fail" and kpi(result, "unavailable")["label"] == "Stock des produits recommandés"
    assert "Sac à dos" in kpi(result, "unavailable")["summary"]
    assert kpi(result, "personalization")["status"] == "fail"
    assert kpi(result, "structured_data")["status"] == "pass"
    assert kpi(result, "agent_ready")["status"] == "info"
    assert "home" not in {k["id"] for k in result["kpis"]}
    assert result["next_step"]["href"] == "/wordpress"
    # the item recommended on its own page caps the score, whatever else is right
    assert result["score"] <= site_audit.SELF_RECO_SCORE_CAP and result["grade"] == "E"


SHOPIFY_PRODUCT = """<html><head><script src="https://cdn.shopify.com/s/files/theme.js"></script>
<script>var meta = {"product":{"id":4242,"vendor":"X"}};</script>
<meta property="og:type" content="product"><meta property="og:title" content="Bonnet"></head>
<body><h1>Bonnet</h1><p>Un bonnet en laine mérinos, tricoté en France, doux et chaud. Un bonnet en laine mérinos, tricoté en France, doux et chaud. Un bonnet en laine mérinos, tricoté en France, doux et chaud. Un bonnet en laine mérinos, tricoté en France, doux et chaud. Un bonnet en laine mérinos, tricoté en France, doux et chaud. Un bonnet en laine mérinos, tricoté en France, doux et chaud. Un bonnet en laine mérinos, tricoté en France, doux et chaud. Un bonnet en laine mérinos, tricoté en France, doux et chaud. Un bonnet en laine mérinos, tricoté en France, doux et chaud. Un bonnet en laine mérinos, tricoté en France, doux et chaud. Un bonnet en laine mérinos, tricoté en France, doux et chaud. Un bonnet en laine mérinos, tricoté en France, doux et chaud. Un bonnet en laine mérinos, tricoté en France, doux et chaud. Un bonnet en laine mérinos, tricoté en France, doux et chaud. </p><product-recommendations class="product-recommendations" data-url="/recommendations/products?product_id=4242&section_id=x&limit=4"></product-recommendations></body></html>"""

# What the theme's section shows (4 cards), not what the JSON endpoint could list.
SHOPIFY_SECTION = """<div><h2>Vous aimerez aussi</h2><ul>""" + "".join(
    f'<li><a href="/products/{h}"><img alt=""></a><a class="quickbuy" href="/products/{h}">Coup d\'oeil</a><a href="/products/{h}"><div class="card__title">{t}</div></a></li>'
    for h, t in [("echarpe", "Écharpe"), ("gants", "Gants"), ("mitaines", "Mitaines"), ("chaussettes", "Chaussettes")]
) + "</ul></div>"


def test_shopify_reads_the_theme_section_and_each_product_stock():
    pages = {
        "https://shop.example/products/bonnet": (200, SHOPIFY_PRODUCT),
        "https://shop.example/recommendations/products?product_id=4242&section_id=x&limit=4": (200, SHOPIFY_SECTION),
        "https://shop.example/.well-known/ucp": (200, '{"ucp": {"version": "2026-08-25"}}'),
    }
    for handle in ("echarpe", "mitaines", "chaussettes"):
        pages[f"https://shop.example/products/{handle}.js"] = (200, '{"available": true}')
    pages["https://shop.example/products/gants.js"] = (200, '{"available": false}')
    # the first recommended product's own page, whose section shows other products
    pages["https://shop.example/products/echarpe"] = (200, SHOPIFY_PRODUCT.replace("4242", "5151").replace("Bonnet", "Écharpe"))
    pages["https://shop.example/recommendations/products?product_id=5151&section_id=x&limit=4"] = (200, SHOPIFY_SECTION.replace("mitaines", "bonnet").replace("Mitaines", "Bonnet"))
    result = run_audit("https://shop.example/products/bonnet", fake_fetch(pages))

    assert result["cms"]["id"] == "shopify" and result["page_type"]["id"] == "product"
    block = result["blocks"][0]
    assert block["source"] == "shopify_section" and block["kind"] == "similar"
    assert block["item_count"] == 4
    assert [i["name"] for i in block["items"]] == ["Écharpe", "Gants", "Mitaines", "Chaussettes"]  # never "Coup d'oeil"
    assert [i["name"] for i in block["items"] if i["unavailable"]] == ["Gants"]
    assert kpi(result, "unavailable")["status"] == "fail"
    assert block["verified"] == "varies" and "Écharpe" in block["evidence"]
    assert kpi(result, "similar_block")["status"] == "pass" and "vérifié" in kpi(result, "similar_block")["summary"]
    assert result["compared_with"]["name"] == "Écharpe"
    assert kpi(result, "behavior_block")["status"] == "fail"
    assert kpi(result, "personalization")["status"] == "fail"
    assert kpi(result, "block_size")["summary"] == "4 produits recommandés par bloc"
    assert kpi(result, "agent_ready")["status"] == "pass"


def test_shopify_store_without_recommendations_section_gets_no_invented_block():
    page = SHOPIFY_PRODUCT.replace('<product-recommendations class="product-recommendations" data-url="/recommendations/products?product_id=4242&section_id=x&limit=4"></product-recommendations>', "")
    result = run_audit("https://shop.example/products/bonnet", fake_fetch({"https://shop.example/products/bonnet": (200, page)}))
    assert result["blocks"] == []
    assert kpi(result, "similar_block")["status"] == "fail"


# A Shopify theme "product list" section titled "POUR TOI" - a collection picked once, holding the
# product being viewed, and two colours of the same bag.
FIXED_SECTION_PAGE = """<html><head><script src="https://cdn.shopify.com/x.js"></script>
<link rel="canonical" href="https://sacs.example/fr/products/castellana-burgundy">
<script type="application/ld+json">{"@type":"Product","name":"CASTELLANA","image":"c.jpg","offers":{"price":"95","availability":"InStock"}}</script></head>
<body><h1>CASTELLANA</h1>
<div id="shopify-section-template--1__main_x" class="shopify-section">
 <carousel-slider><h2><button>Précédent</button><span class="has-paging__title">POUR TOI:</span><button>Suivant</button></h2>
 <div class="grid">""" + "".join(
    f'<product-block><a class="product-link" href="/fr/products/{h}"><img alt=""></a><a class="btn quickbuy-toggle" href="/fr/products/{h}">Coup d\'oeil</a>'
    f'<a class="product-link" href="/fr/products/{h}"><div class="product-block__title">{t}</div></a></product-block>'
    for h, t in [("castellana-burgundy", "CASTELLANA"), ("filles-du-calvaire-dore", "FILLES DU CALVAIRE"), ("ranelagh-rose", "RANELAGH"), ("filles-du-calvaire-noir", "FILLES DU CALVAIRE"),
                 ("goya-chocolate", "GOYA"), ("chamberi-noir", "CHAMBERI")]
) + """</div></carousel-slider></div></body></html>"""


def test_a_pour_toi_block_is_proven_fixed_on_another_product_page():
    url = "https://sacs.example/fr/products/castellana-burgundy"
    other = (FIXED_SECTION_PAGE
             .replace('href="https://sacs.example/fr/products/castellana-burgundy"', 'href="https://sacs.example/fr/products/filles-du-calvaire-dore"')
             .replace("<h1>CASTELLANA</h1>", "<h1>FILLES DU CALVAIRE</h1>").replace('"name":"CASTELLANA"', '"name":"FILLES DU CALVAIRE"'))
    result = run_audit(url, fake_fetch({url: (200, FIXED_SECTION_PAGE), "https://sacs.example/fr/products/filles-du-calvaire-dore": (200, other)}))
    block = result["blocks"][0]
    # nothing in the markup says "fixed": the second product page shows the same four bags
    assert block["title"] == "POUR TOI" and block["kind"] == "fixed" and block["claims_personal"] is True
    assert block["verified"] == "fixed" and "FILLES DU CALVAIRE" in block["evidence"]
    assert kpi(result, "self_reco")["status"] == "fail"
    assert kpi(result, "similar_block")["status"] == "fail" and "constaté" in kpi(result, "similar_block")["summary"]
    assert kpi(result, "behavior_block")["status"] == "fail"
    personalization = kpi(result, "personalization")
    assert personalization["status"] == "fail" and "« POUR TOI » s'intitule « pour vous », mais affiche les mêmes produits" in personalization["detail"]
    assert kpi(result, "duplicates")["status"] == "warn" and "même modèle" in kpi(result, "duplicates")["summary"]
    assert result["grade"] == "E" and result["score"] <= site_audit.SELF_RECO_SCORE_CAP


ARTICLE = """<html lang="fr"><head><meta name="generator" content="WordPress 6.8">
<script type="application/ld+json">{"@graph":[{"@type":"WebSite"},{"@type":"BlogPosting","headline":"Bien choisir son vélo","image":"a.jpg","datePublished":"2026-01-02"}]}</script>
</head><body class="single-post"><article><h1>Bien choisir son vélo</h1><p>Texte…</p></article>
<nav><h3>Articles similaires</h3><a href="/a/">A</a><a href="/b/">B</a></nav>
<aside><div class="widget"><h3>À lire aussi</h3><ul><li><a href="/blog/entretien-velo/">Entretenir son vélo</a></li>
<li><a href="/blog/velo-electrique/">Le vélo électrique</a></li><li><a href="/blog/bien-choisir-son-velo/">Bien choisir son vélo</a></li></ul></div></aside>
</body></html>"""


def test_article_page_and_heading_detected_block():
    fetch = fake_fetch({"https://media.example/blog/bien-choisir-son-velo/": (200, ARTICLE), "https://media.example/": (200, "<html></html>")})
    result = run_audit("https://media.example/blog/bien-choisir-son-velo/", fetch)

    assert result["cms"]["id"] == "wordpress" and result["page_type"]["id"] == "article"
    assert [b["title"] for b in result["blocks"]] == ["À lire aussi"]  # the one in <nav> is navigation
    assert kpi(result, "self_reco")["status"] == "fail"
    assert "L'article affiché" in kpi(result, "self_reco")["summary"]
    assert kpi(result, "unavailable")["status"] == "unknown"
    assert result["blocks"][0]["kind"] == "similar"
    assert kpi(result, "similar_block")["status"] == "warn"  # the other article could not be opened: not verified


def test_third_party_tool_filled_by_javascript():
    page = """<html><head><script src="https://connect.nosto.com/include/abc"></script>
    <script type="application/ld+json">{"@type":"Product","name":"Lampe"}</script></head>
    <body><div class="nosto_element" id="productpage-nosto-1"></div></body></html>"""
    result = run_audit("https://deco.example/lampe", fake_fetch({"https://deco.example/lampe": (200, page)}))

    assert [v["id"] for v in result["vendors"]] == ["nosto"]
    assert result["blocks"][0]["js_only"] is True
    # drawn by the browser: not readable, so not judged (not "absent")
    assert kpi(result, "similar_block")["status"] == "unknown"
    assert kpi(result, "behavior_block")["status"] == "unknown"
    assert kpi(result, "personalization")["status"] == "pass"
    assert kpi(result, "self_reco")["status"] == "unknown"
    assert kpi(result, "structured_data")["status"] == "warn"
    assert result["notes"]


def test_no_recommendations_at_all():
    page = """<html><body><h1>Concert</h1><p>Un concert exceptionnel, une soirée inoubliable avec un orchestre réputé. Un concert exceptionnel, une soirée inoubliable avec un orchestre réputé. Un concert exceptionnel, une soirée inoubliable avec un orchestre réputé. Un concert exceptionnel, une soirée inoubliable avec un orchestre réputé. Un concert exceptionnel, une soirée inoubliable avec un orchestre réputé. Un concert exceptionnel, une soirée inoubliable avec un orchestre réputé. Un concert exceptionnel, une soirée inoubliable avec un orchestre réputé. Un concert exceptionnel, une soirée inoubliable avec un orchestre réputé. Un concert exceptionnel, une soirée inoubliable avec un orchestre réputé. Un concert exceptionnel, une soirée inoubliable avec un orchestre réputé. </p><script type="application/ld+json">{"@type":"MusicEvent","name":"Concert",
    "startDate":"2026-12-01","image":"c.jpg","offers":{"price":"30","availability":"InStock"}}</script></body></html>"""
    result = run_audit("https://billets.example/concert", fake_fetch({"https://billets.example/concert": (200, page)}))
    assert result["page_type"]["id"] == "event"
    assert kpi(result, "similar_block")["status"] == "fail"
    assert kpi(result, "behavior_block")["status"] == "fail"
    assert kpi(result, "personalization")["status"] == "fail"
    assert result["grade"] == "E"


@pytest.mark.parametrize("raw, message", [
    ("", "Indiquez"),
    ("pas une url", "pas valide"),
    ("ftp://site.example/x", "pas valide"),
    ("https://user:pw@site.example/", "pas valide"),
])
def test_bad_input_is_refused(raw, message):
    with pytest.raises(AuditError, match=message):
        run_audit(raw, fake_fetch({}))


def test_blocked_and_missing_pages_say_why():
    with pytest.raises(AuditError, match="anti-robots"):
        run_audit("https://shop.example/p", fake_fetch({"https://shop.example/p": (403, "no")}))
    with pytest.raises(AuditError, match="erreur 404"):
        run_audit("https://shop.example/p", fake_fetch({}))


# ---------------------------------------------------------------------------- network safety

@pytest.fixture
def dns(monkeypatch):
    table = {"shop.example": "93.184.216.34", "internal.example": "10.0.0.5", "meta.example": "169.254.169.254", "loop.example": "127.0.0.1"}

    def getaddrinfo(host, port, *args, **kwargs):
        return [(2, 1, 6, "", (table.get(host, host), port))]
    monkeypatch.setattr(site_audit.socket, "getaddrinfo", getaddrinfo)
    return table


def mock_fetcher(handler) -> SafeFetcher:
    return SafeFetcher(client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False))


@pytest.mark.parametrize("url", ["http://internal.example/", "http://meta.example/latest/meta-data", "http://loop.example/", "http://127.0.0.1/", "http://[::1]/"])
def test_private_addresses_are_never_fetched(dns, url):
    seen = []
    fetcher = mock_fetcher(lambda request: seen.append(request) or httpx.Response(200, text="x"))
    with pytest.raises(AuditError, match="site public"):
        fetcher.get(url)
    assert seen == []


def test_a_redirect_to_a_private_address_is_refused(dns):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": "http://internal.example/admin"})
    with pytest.raises(AuditError, match="site public"):
        mock_fetcher(handler).get("https://shop.example/p")
    assert len(seen) == 1


def test_the_connection_is_pinned_to_the_checked_address(dns):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})
    page = mock_fetcher(handler).get("https://shop.example/p?x=1")
    assert page.url == "https://shop.example/p?x=1"
    assert seen[0].url.host == "93.184.216.34"
    assert seen[0].headers["host"] == "shop.example"
    assert seen[0].extensions["sni_hostname"] == "shop.example"
    assert "LikylyAudit" in seen[0].headers["user-agent"]


def test_other_ports_are_refused(dns):
    with pytest.raises(AuditError, match="ports 80 et 443"):
        mock_fetcher(lambda r: httpx.Response(200)).get("http://shop.example:8080/")


def test_large_bodies_are_capped(dns, monkeypatch):
    monkeypatch.setattr(site_audit, "MAX_BODY_BYTES", 1000)
    page = mock_fetcher(lambda r: httpx.Response(200, content=b"a" * 50_000)).get("https://shop.example/")
    assert len(page.text) < 50_000


# ---------------------------------------------------------------------------- API flow

@pytest.fixture
def audits(monkeypatch):
    """The API with a fake site behind it, SMTP captured, and a fresh visitor address per test."""
    pages = {
        "https://boutique.example/produit/chaussure-trail/": (200, WOO_PRODUCT.replace("Sac à dos", "Sac &lt;img src=x onerror=alert(1)&gt;")),
        "https://boutique.example/": (200, WOO_HOME),
    }
    monkeypatch.setattr(site_audits, "_run", lambda url, provided=None, source="robot": run_audit(url, fake_fetch(pages), provided=provided, source=source))
    monkeypatch.setenv("SMTP_HOST", "smtp.test.invalid")
    monkeypatch.setenv("SMTP_USER", "noreply@likyly.test")
    monkeypatch.delenv("TURNSTILE_SECRET_KEY", raising=False)
    monkeypatch.delenv("UPGRADE_REQUEST_TO", raising=False)
    sent = []
    monkeypatch.setattr(site_audits, "deliver", lambda message, settings: sent.append(message))
    site_audits.reads.reset()
    site_audits.misses.reset()
    site_audits.page_posts.reset()
    site_audits.sent_pages.reset()
    return {"sent": sent, "headers": {"X-Forwarded-For": f"2001:db8::{uuid.uuid4().hex[:4]}:{uuid.uuid4().hex[:4]}, 10.0.0.1"}}


def test_teaser_then_full_report_and_email(http, audits):
    headers = audits["headers"]
    created = http.post("/audits", json={"url": "boutique.example/produit/chaussure-trail/"}, headers=headers)
    assert created.status_code == 201, created.text
    teaser = created.json()
    token = teaser["token"]
    assert teaser["unlocked"] is False and teaser["score"] < 50
    assert teaser["cms"]["id"] == "woocommerce"
    assert all(set(k) == {"id", "label", "about", "status", "summary", "weight", "method"} for k in teaser["kpis"])
    assert all(k["method"] and k["about"] for k in teaser["kpis"])  # every check says what it is and how it is measured, before the email too
    assert kpi(teaser, "self_reco")["about"] == "Le produit affiché ne doit jamais apparaître dans ses propres recommandations."
    assert "UCP (Universal Commerce Protocol)" in kpi(teaser, "agent_ready")["about"]
    assert kpi(teaser, "unavailable")["method"].startswith("Stock lu dans le code WooCommerce")
    assert "blocks" not in teaser and "next_step" not in teaser and teaser["block_count"] == 1
    assert teaser["report_url"].endswith(f"/audit/r/{token}")
    assert http.get(f"/audits/{token}").json()["unlocked"] is False

    assert http.post(f"/audits/{token}/email", json={"email": "pas-un-email"}, headers=headers).status_code == 422
    full = http.post(f"/audits/{token}/email", json={"email": " camille@boutique.example "}, headers=headers)
    assert full.status_code == 200, full.text
    body = full.json()
    assert body["unlocked"] is True and body["blocks"] and body["next_step"]["href"] == "/wordpress"
    assert all(k.get("detail") is not None for k in body["kpis"])
    assert "email" not in body

    report, lead = audits["sent"]
    assert report["To"] == "camille@boutique.example"
    assert "/100 pour boutique.example" in report["Subject"]
    html_part = report.get_body(("html",)).get_content()
    assert f"/audit/r/{token}" in html_part and "Auto-recommandation" in html_part
    assert "<img src=x" not in html_part and "Sac &lt;img src=x onerror=alert(1)&gt;" in html_part
    assert "{{" not in html_part
    assert lead["To"] == "contact@likyly.com" and lead["Reply-To"] == "camille@boutique.example"

    again = http.get(f"/audits/{token}").json()
    assert again["unlocked"] is True and again["blocks"]
    import db
    row = db.get_site_audit(token)
    assert row["email"] == "camille@boutique.example" and row["emailed_at"] is not None


def test_unknown_report(http, audits):
    assert http.get("/audits/nope").status_code == 404
    assert http.post("/audits/nope/email", json={"email": "a@b.example"}).status_code == 404


def test_audit_errors_are_shown_to_the_visitor(http, audits, monkeypatch):
    def failing(url, provided=None, source="robot"):
        raise AuditError("Le site refuse les visites automatiques (erreur 403).", blocked=True)
    monkeypatch.setattr(site_audits, "_run", failing)
    response = http.post("/audits", json={"url": "https://x.example/"}, headers=audits["headers"])
    assert response.status_code == 422
    assert "refuse" in response.json()["detail"]
    assert response.json()["blocked"] is True  # the page then offers the browser way


def test_rate_limits_per_visitor(http, audits):
    headers = audits["headers"]
    for _ in range(site_audits.AUDITS_PER_HOUR):
        assert http.post("/audits", json={"url": "boutique.example/produit/chaussure-trail/"}, headers=headers).status_code == 201
    assert http.post("/audits", json={"url": "boutique.example/produit/chaussure-trail/"}, headers=headers).status_code == 429
    other = {"X-Forwarded-For": "198.51.100.77"}
    assert http.post("/audits", json={"url": "boutique.example/produit/chaussure-trail/"}, headers=other).status_code == 201


def test_smtp_down_still_shows_the_report(http, audits, monkeypatch):
    def broken(message, settings):
        raise OSError("connection refused")
    monkeypatch.setattr(site_audits, "deliver", broken)
    token = http.post("/audits", json={"url": "boutique.example/produit/chaussure-trail/"}, headers=audits["headers"]).json()["token"]
    response = http.post(f"/audits/{token}/email", json={"email": "camille@boutique.example"}, headers=audits["headers"])
    assert response.status_code == 200 and response.json()["unlocked"] is True
    import db
    assert "connection refused" in db.get_site_audit(token)["email_error"]


def test_captcha_is_required_when_configured(http, audits, monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret")
    response = http.post("/audits", json={"url": "boutique.example/produit/chaussure-trail/"}, headers=audits["headers"])
    assert response.status_code == 400


def test_audit_routes_stay_out_of_the_public_api_document(http):
    assert not any(path.startswith("/audits") for path in http.get("/openapi.json").json()["paths"])


BOOK_PAGE = """<html><head><script type="application/ld+json">{"@type":"Book","name":"Dune","image":"d.jpg","author":"Herbert"}</script></head>
<body><header class="site"><div class="wrapper">
<nav><a href="/">Accueil</a><a href="/livres/">Livres</a></nav>
<h1>Dune</h1>
<div class="stats"><a href="#avis">12 critiques</a> · <a href="#apres">Que lire après</a></div>
<section><h2>Que lire après ?</h2>
 <div class="sub"><div class="head"><h3>Ceux qui ont aimé ce livre ont aussi aimé<a aria-label="Voir plus" href="/livres/dune/recommandations"></a></h3></div>
  <div class="carousel">
   <div class="card"><a href="/livres/fondation/1"><img alt="Fondation par Asimov"></a><p><a href="/livres/fondation/1">Fondation</a></p><p><a href="/auteur/asimov/9">Asimov</a></p></div>
   <div class="card"><a href="/livres/hyperion/2"><img alt="Hypérion"></a><p><a href="/livres/hyperion/2">Hypérion</a></p><p><a href="/auteur/simmons/8">Simmons</a></p></div>
   <div class="card"><a href="/livres/neuromancien/3"><img alt="Neuromancien"></a><p><a href="/livres/neuromancien/3">Neuromancien</a></p></div>
  </div></div>
 <div class="sub"><div class="head"><h3>Autres livres de Frank Herbert (12)</h3></div>
  <div class="carousel"><div class="card"><a href="/livres/messie/4">Le Messie de Dune</a></div><div class="card"><a href="/livres/enfants/5">Les Enfants de Dune</a></div></div></div>
</section></div></header></body></html>"""


def test_book_page_sub_blocks_cards_and_broken_markup():
    result = run_audit("https://livres.example/livres/dune/7", fake_fetch({"https://livres.example/livres/dune/7": (200, BOOK_PAGE)}))
    assert result["page_type"]["id"] == "book"
    titles = [(b["title"], b["kind"], b["item_count"]) for b in result["blocks"]]
    # the sub-blocks, not their section; not the menu line; the page wrapped in <header> still read
    assert titles == [("Ceux qui ont aimé ce livre ont aussi aimé", "behavior", 3), ("Autres livres de Frank Herbert (12)", "similar", 2)]
    # image + title of one card = one item; author links and "Voir plus" are not items
    assert kpi(result, "duplicates")["status"] == "pass"
    assert kpi(result, "similar_block")["status"] == "warn" and kpi(result, "behavior_block")["status"] == "pass"
    # the site computes "readers also liked" itself: whether it adapts to the visitor can't be seen
    assert kpi(result, "personalization")["status"] == "unknown"
    assert kpi(result, "structured_data")["status"] == "pass"  # a book page that sells nothing needs no offer


def test_ipv4_is_preferred_when_a_site_has_both(monkeypatch):
    monkeypatch.setattr(site_audit.socket, "getaddrinfo", lambda host, port, *a, **k: [
        (10, 1, 6, "", ("2606:4700::1", port, 0, 0)), (2, 1, 6, "", ("93.184.216.34", port)),
    ])
    seen = []
    mock_fetcher(lambda r: seen.append(r) or httpx.Response(200, text="<html></html>")).get("https://dual.example/")
    assert seen[0].url.host == "93.184.216.34"



def _new_report(http, headers) -> str:
    return http.post("/audits", json={"url": "boutique.example/produit/chaussure-trail/"}, headers=headers).json()["token"]


def test_report_links_expire_after_ten_days(http, audits):
    import db
    headers = audits["headers"]
    token = _new_report(http, headers)
    report = http.get(f"/audits/{token}", headers=headers).json()
    assert report["expires_at"].startswith(str((db.get_site_audit(token)["created_at"] + site_audits.timedelta(days=10)).date()))
    assert http.post(f"/audits/{token}/email", json={"email": "camille@boutique.example"}, headers=headers).status_code == 200
    assert "jusqu'au" in audits["sent"][0].get_body(("plain",)).get_content()

    with db.SessionLocal() as session:
        session.query(db.SiteAuditModel).filter_by(token=token).update({"created_at": db.utcnow() - site_audits.timedelta(days=10, minutes=1)})
        session.commit()
    expired = http.get(f"/audits/{token}", headers=headers)
    assert expired.status_code == 410 and "10 jours" in expired.json()["detail"]
    assert http.post(f"/audits/{token}/email", json={"email": "x@y.example"}, headers=headers).status_code == 410


def test_report_reads_are_capped_and_guessing_is_cut_short(http, audits):
    headers = audits["headers"]
    token = _new_report(http, headers)
    for _ in range(site_audits.MISSES_PER_HOUR):
        assert http.get("/audits/devine-moi", headers=headers).status_code == 404
    assert http.get("/audits/encore-une", headers=headers).status_code == 429
    assert http.get(f"/audits/{token}", headers=headers).status_code == 200  # real links still open

    for _ in range(site_audits.READS_PER_HOUR - site_audits.MISSES_PER_HOUR - 2):
        http.get(f"/audits/{token}", headers=headers)
    assert http.get(f"/audits/{token}", headers=headers).status_code == 429
    assert http.get(f"/audits/{token}", headers={"X-Forwarded-For": "198.51.100.200"}).status_code == 200


RANDOM_PAGE = """<html><head><meta name="generator" content="WooCommerce 9"><script type="application/ld+json">{"@type":"Product","name":"Tente","image":"t.jpg","offers":{"price":"1","availability":"InStock"}}</script></head>
<body><h1>Tente</h1><section class="related products"><h2>Produits apparentés</h2><ul>ITEMS</ul></section></body></html>"""


def _related(*slugs):
    return RANDOM_PAGE.replace("ITEMS", "".join(f'<li class="product instock"><a href="/produit/{s}/"><h2 class="woocommerce-loop-product__title">{s}</h2></a></li>' for s in slugs))


def test_a_block_that_changes_on_reload_is_seen_as_random():
    url = "https://camping.example/produit/tente/"
    pages = {
        url: [(200, _related("duvet", "lampe", "gourde", "sac")), (200, _related("matelas", "rechaud", "gourde", "sac"))],
        "https://camping.example/produit/duvet/": (200, _related("tente", "lampe", "pompe", "sac")),
    }
    result = run_audit(url, fake_fetch(pages))
    block = result["blocks"][0]
    assert block["verified"] == "random" and block["kind"] == "random"
    assert kpi(result, "similar_block")["status"] == "warn" and "hasard" in kpi(result, "similar_block")["summary"]


def test_a_block_that_follows_the_product_is_verified():
    url = "https://camping.example/produit/tente/"
    pages = {
        url: (200, _related("duvet", "lampe", "gourde", "sac")),
        "https://camping.example/produit/duvet/": (200, _related("oreiller", "matelas", "tente", "drap")),
    }
    result = run_audit(url, fake_fetch(pages))
    assert result["blocks"][0]["verified"] == "varies"
    assert kpi(result, "similar_block")["status"] == "pass"
    assert result["compared_with"]["url"] == "https://camping.example/produit/duvet/"


SHOW_PAGE = """<html><head><meta property="og:title" content="Turandot - Opéra - Programmation Saison 26/27">
<meta property="og:url" content="https://opera.example/saison/opera/turandot">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"WebPage","name":"Turandot"}</script></head>
<body><a href="/">retour</a><h1>retour</h1><p>Billetterie · 12 représentations · Billetterie en ligne · Réserver vos places · Billetterie</p>
<section><h2>Vous aimerez aussi</h2><ul>
<li><a href="/saison/opera/le-trouvere">Le Trouvère</a></li><li><a href="/saison/opera/turandot">Turandot</a></li><li><a href="/saison/opera/don-giovanni">Don Giovanni</a></li>
</ul></section></body></html>"""


def test_a_show_page_that_declares_nothing_is_still_fully_audited():
    url = "https://opera.example/saison/opera/turandot"

    def fetch(target):
        if target == "https://opera.example/.well-known/ucp":
            raise AuditError("Le site ne répond pas.")  # never answers: still a clear "no"
        return fake_fetch({url: (200, SHOW_PAGE)})(target)

    result = run_audit(url, fetch)
    assert result["page_type"]["id"] == "event" and result["item_name"] == "Turandot"
    # self-recommendation is checked whatever the page declares
    assert kpi(result, "self_reco")["status"] == "fail" and "L'événement affiché" in kpi(result, "self_reco")["summary"]
    data = kpi(result, "structured_data")
    assert data["status"] == "fail" and "« WebPage »" in data["summary"] and "spectacle" in data["summary"]
    assert kpi(result, "agent_ready")["status"] == "info"
    assert kpi(result, "unavailable")["label"] == "Disponibilité des événements recommandés"


def test_a_page_with_no_structured_data_at_all_fails_that_check():
    page = SHOW_PAGE.replace('<script type="application/ld+json">{"@context":"https://schema.org","@type":"WebPage","name":"Turandot"}</script>', "")
    url = "https://opera.example/saison/opera/turandot"
    result = run_audit(url, fake_fetch({url: (200, page)}))
    assert kpi(result, "structured_data")["status"] == "fail" and kpi(result, "structured_data")["summary"] == "Aucune donnée structurée schema.org"


# Calibration (Amazon, BBC, The Independent…): what a marketplace page and a news page look like.
MARKET_PAGE = """<html><head><title>Manette</title></head><body>
<div id="wayfinding-breadcrumbs_feature_div"><ul><li><a href="/jeux-video/b/1">Jeux vidéo</a></li><li><a href="/pc/b/2">PC</a></li>
<li><h3>Accessoires</h3><a href="/accessoires/b/3">Accessoires</a></li><li><a href="/manettes/b/4">Contrôleurs</a></li></ul></div>
<h1>Manette Pro 2</h1><span id="add-to-cart-button">Ajouter au panier</span>
<p>""" + "Une manette sans fil précise, avec joysticks à effet Hall et une autonomie de vingt heures. " * 8 + """</p>
<div class="p13n-sc-carousel"><h2>Quels sont les autres articles que les clients achètent après avoir consulté cet article ?</h2><ol>""" + "".join(
    f'<li><div class="p13n-card"><div><div><div><a href="/Manette-{n}/dp/B0000000{n}{n}/ref=pd_vtp_{n}"><img alt="Manette {n}"></a></div></div></div>'
    f'<div><div><div><a href="/Manette-{n}/dp/B0000000{n}{n}/ref=pd_vtp_t{n}"><span>Manette {n}</span></a></div></div></div>'
    f'<a href="/product-reviews/B0000000{n}{n}">4,5 étoiles</a></div></li>' for n in range(1, 6)
) + """</ol></div></body></html>"""


def test_a_marketplace_page_is_read_like_a_marketplace():
    url = "https://shop.example/Manette-Pro/dp/B0CSPGMGBT?th=1"
    result = run_audit(url, fake_fetch({url: (200, MARKET_PAGE)}))
    assert result["page_type"]["id"] == "product"  # no schema.org, but an add-to-cart button
    titles = [b["title"] for b in result["blocks"]]
    assert "Accessoires" not in titles  # the breadcrumb is not a block
    block = next(b for b in result["blocks"] if b["title"].startswith("Quels sont"))
    assert block["kind"] == "behavior" and block["item_count"] == 5  # image + title + reviews link = one product
    assert kpi(result, "behavior_block")["status"] == "pass"
    assert kpi(result, "duplicates")["status"] == "pass"
    assert kpi(result, "personalization")["status"] == "pass"  # the site's own "p13n" engine
    assert site_audit.item_key("https://x.example/Some-Slug/dp/b0cspgmgbt/ref=abc?th=1") == "/dp/B0CSPGMGBT"


NEWS_PAGE = """<html><head><script type="application/ld+json">{"@type":"NewsArticle","headline":"Un sujet","image":"a.jpg","datePublished":"2026-11-17"}</script></head>
<body><article><h1>Un sujet</h1><p>""" + "Le texte de l'article, assez long pour être un vrai article de presse. " * 12 + """</p></article>
<aside><h2>Read next</h2></aside>
<section><h2>Most read</h2><ol><li><a href="/news/un-sujet">Un sujet</a></li><li><a href="/news/autre">Autre</a></li><li><a href="/news/encore">Encore</a></li></ol></section>
<span class="badge">Best seller</span></body></html>"""


def test_a_news_page_with_a_block_drawn_in_javascript_and_a_best_of_list():
    url = "https://news.example/news/un-sujet"
    result = run_audit(url, fake_fetch({url: (200, NEWS_PAGE)}))
    kinds = {b["title"]: (b["kind"], b["js_only"]) for b in result["blocks"]}
    assert kinds["Read next"] == ("similar", True)  # a title with nothing under it: filled in by the browser
    assert kinds["Most read"] == ("popular", False)
    assert "Best seller" not in kinds  # a badge, not a block
    assert kpi(result, "similar_block")["status"] == "warn" and "JavaScript" in kpi(result, "similar_block")["summary"]
    assert kpi(result, "behavior_block")["status"] == "warn" and "popularité" in kpi(result, "behavior_block")["summary"]
    assert kpi(result, "self_reco")["status"] == "unknown"  # the article in "Most read" is not a self-recommendation


def test_no_score_when_no_recommendation_can_be_read():
    page = """<html><body><h1>Produit</h1><p>""" + "Description du produit, assez longue pour une vraie page. " * 15 + """</p>
    <div class="product-recommendations-carousel" data-testid="recommendations"></div></body></html>"""
    url = "https://spa.example/p/produit-123456"
    result = run_audit(url, fake_fetch({url: (200, page)}))
    assert result["score"] is None and result["grade"] is None and result["partial"] is True


# ---------------------------------------------------------------------------- the audit's browser

SHELL_PAGE = """<html><head><script type="application/ld+json">{"@type":"Product","name":"Veste","image":"v.jpg","offers":{"price":"90","availability":"InStock"}}</script>
<script>window.reco = {placement: "you may also like"};</script></head><body><h1>Veste</h1><p>""" + "Une veste chaude et légère. " * 40 + """</p>
<div id="reco-similar"></div><div id="reco-bought"></div>BLOCKS</body></html>"""


def _shop_block(title, slugs):
    return f'<section><h2>{title}</h2><ul>' + "".join(f'<li><a href="/p/{s}">{s.title()}</a></li>' for s in slugs) + "</ul></section>"


def _rendered(similar, bought):
    return SHELL_PAGE.replace("BLOCKS", _shop_block("Vous aimerez aussi", similar) + _shop_block("Les clients ont aussi acheté", bought))


def fake_render(pages: dict, calls: list):
    def render(url: str) -> Page:
        calls.append(url)
        answer = pages.get(url)
        if answer is None:
            raise AuditError("la page met trop de temps à s'afficher")
        status, body = answer[:2]
        return Page(url=url, status=status, text=body, content_type="text/html", challenge=len(answer) > 2 and answer[2])
    return render


def test_blocks_drawn_by_javascript_are_read_in_a_browser():
    url, other = "https://mode.example/p/veste", "https://mode.example/p/pull"
    calls: list = []
    result = run_audit(url, fake_fetch({url: (200, SHELL_PAGE.replace("BLOCKS", "")), other: (200, SHELL_PAGE.replace("BLOCKS", ""))}),
                       render=fake_render({url: (200, _rendered(["pull", "gilet", "manteau", "echarpe"], ["bonnet", "gants", "pull", "botte"])),
                                           other: (200, _rendered(["veste", "chemise", "short", "jean"], ["sac", "ceinture", "veste", "polo"]))}, calls))
    assert result["rendered"] is True and result["source"] == "robot"
    assert [b["browser_only"] for b in result["blocks"]] == [True, True]
    assert kpi(result, "similar_block")["status"] == "pass"  # read in the browser, and it follows the product
    assert kpi(result, "behavior_block")["status"] == "pass"
    assert kpi(result, "self_reco")["status"] == "pass"
    assert result["score"] is not None
    # the comparisons were made in the browser too: the other product, then a second load
    assert calls == [url, other, url]
    assert any("assistants IA" in n for n in result["notes"])


def test_a_page_whose_html_has_both_blocks_is_not_rendered():
    url = "https://mode.example/p/veste"
    page = _rendered(["pull", "gilet", "manteau", "echarpe"], ["bonnet", "gants", "pull", "botte"])
    calls: list = []
    result = run_audit(url, fake_fetch({url: (200, page)}), render=fake_render({}, calls))
    assert calls == [] and result["rendered"] is False
    assert [b["browser_only"] for b in result["blocks"]] == [False, False]


def test_a_browser_that_cannot_help_leaves_the_html_audit():
    url = "https://mode.example/p/veste"
    static = SHELL_PAGE.replace("BLOCKS", "")
    challenged = run_audit(url, fake_fetch({url: (200, static)}), render=fake_render({url: (200, "<html><title>Just a moment...</title></html>", True)}, []))
    assert challenged["rendered"] is False and any("vérification anti-robots" in n for n in challenged["notes"])
    assert kpi(challenged, "similar_block")["summary"].startswith("Non lisible sans navigateur")
    down = run_audit(url, fake_fetch({url: (200, static)}), render=fake_render({}, []))
    assert down["rendered"] is False and any("navigateur impossible" in n for n in down["notes"])


def test_anti_robot_pages_are_never_audited():
    url = "https://mode.example/p/veste"
    for page in ('<html><head><title>Just a moment...</title></head><body><div id="challenge-platform"></div></body></html>',
                 '<html><head><title>Client Challenge</title><script src="/_fs-ch-1T1/script.js"></script></head></html>'):
        with pytest.raises(AuditError) as error:
            run_audit(url, fake_fetch({url: (200, page)}))
        assert error.value.blocked is True and "navigateur" in str(error.value)
    with pytest.raises(AuditError) as error:
        run_audit(url, fake_fetch({url: (403, "no")}))
    assert error.value.blocked is True
    with pytest.raises(AuditError, match="vérification anti-robots du site, pas celui de la fiche"):
        run_audit(url, fake_fetch({}), provided=Page(url=url, status=200, text="<title>Just a moment...</title>"), source="paste")


def test_a_real_page_loading_the_protection_script_is_not_a_challenge():
    # Le Monde's home page loads /_fs-ch-…/init.js; Cloudflare adds challenge-platform to normal pages
    text = " ".join(["Un article de presse avec beaucoup de texte."] * 60)
    for marker in ('<script src="/_fs-ch-1T1wmsGaOgGaSxcX/assets/init.js"></script>',
                   '<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>'):
        assert is_challenge(f"<html><head><title>Le Monde</title>{marker}</head><body><p>{text}</p></body></html>") is False
        assert is_challenge(f"<html><head><title>Le Monde</title>{marker}</head><body>Please enable JavaScript</body></html>") is True

def test_the_renderer_client(monkeypatch):
    def handler(request):
        if b"busy" in request.content:
            return httpx.Response(503, json={"detail": "busy"})
        assert request.headers["X-Renderer-Token"] == "t0k"
        return httpx.Response(200, json={"status": 200, "url": "https://a.example/x", "html": "<html></html>", "challenge": True, "ms": 900})
    client = site_audit.RendererClient("http://renderer:7000/", "t0k", httpx.Client(transport=httpx.MockTransport(handler)))
    page = client.render("https://a.example/x")
    assert page.status == 200 and page.challenge is True and page.text == "<html></html>"
    with pytest.raises(AuditError, match="occupé"):
        client.render("https://a.example/busy")
    with pytest.raises(AuditError):
        client.render("file:///etc/passwd")


# ---------------------------------------------------------------------------- from the visitor's browser

def test_the_bookmarklet_sends_the_page_then_the_audit_page_runs_it(http, audits, monkeypatch):
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://www.likyly.test")
    page = _rendered(["pull", "gilet", "manteau", "echarpe"], ["bonnet", "gants", "pull", "botte"])
    sent = http.post("/audits/page", data={"url": "https://mode.example/p/veste", "html": page}, headers=audits["headers"], follow_redirects=False)
    assert sent.status_code == 303
    location = sent.headers["location"]
    assert location.startswith("https://www.likyly.test/audit?page=")
    page_id = location.split("page=")[1]

    created = http.post("/audits", json={"page_id": page_id}, headers=audits["headers"])
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["source"] == "browser" and body["url"] == "https://mode.example/p/veste"
    assert kpi(body, "behavior_block")["status"] == "pass"
    # once only: the page is not kept
    again = http.post("/audits", json={"page_id": page_id}, headers=audits["headers"])
    assert again.status_code == 410 and "favori" in again.json()["detail"]


@pytest.mark.parametrize("form, error", [
    ({"url": "https://mode.example/p/veste", "html": "  "}, "vide"),
    ({"url": "pas une adresse", "html": "<html></html>"}, "adresse"),
    ({"url": "https://mode.example/p/veste", "html": "x" * (site_audits.MAX_PAGE_CHARS + 1)}, "trop-lourde"),
])
def test_a_page_the_bookmarklet_cannot_send_says_why(http, audits, monkeypatch, form, error):
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://www.likyly.test")
    sent = http.post("/audits/page", data=form, headers=audits["headers"], follow_redirects=False)
    assert sent.status_code == 303 and sent.headers["location"] == f"https://www.likyly.test/audit/navigateur?erreur={error}"


def test_pasted_source_code_is_audited(http, audits):
    page = _rendered(["pull", "gilet", "manteau", "echarpe"], ["bonnet", "gants", "pull", "botte"])
    created = http.post("/audits", json={"url": "mode.example/p/veste", "html": page}, headers=audits["headers"])
    assert created.status_code == 201, created.text
    assert created.json()["source"] == "paste" and created.json()["host"] == "mode.example"
    assert http.post("/audits", json={}, headers=audits["headers"]).status_code == 422


def test_pages_sent_are_kept_briefly_and_within_a_memory_budget(monkeypatch):
    store = site_audits.PageStore()
    monkeypatch.setattr(store, "MAX_TOTAL_CHARS", 100)
    first = store.put("https://a.example/", "x" * 60)
    assert first and store.put("https://a.example/", "x" * 60) is None  # over budget
    assert store.take(first) == ("https://a.example/", "x" * 60) and store.take(first) is None
    kept = store.put("https://a.example/", "y")
    monkeypatch.setattr(site_audits.time, "monotonic", lambda: 10 ** 9)
    assert store.take(kept) is None  # expired


def test_cards_with_author_links_in_a_section_header_block():
    """Fnac's shape: the block title in the section's own <header>, and each card linking the book,
    its author and its e-book format - one recommendation per card."""
    cards = "".join(
        f'<li><article class="miniFA"><a class="miniFA__link" href="/a{n}/livre-{n}"><span class="miniFA__title">Livre {n}</span></a>'
        f'<div><a href="/Auteur-{n}/ia{n}">Auteur {n}</a></div><a href="/a{n}/livre-{n}?NUMERICAL=Y">Format numérique</a></article></li>'
        for n in range(101, 107))
    page = ('<html><head><script type="application/ld+json">{"@type":"Book","name":"Morsures","image":"m.jpg"}</script></head><body>'
            '<header role="banner"><nav><a href="/">Accueil</a></nav></header><h1>Morsures</h1>'
            f'<div class="f-productLayer"><header class="f-productLayer__header"><h2>Les internautes ont aussi acheté</h2></header><ul>{cards}</ul></div></body></html>')
    result = run_audit("https://livres.example/a100/morsures", fake_fetch({"https://livres.example/a100/morsures": (200, page)}))
    block = result["blocks"][0]
    assert block["kind"] == "behavior" and block["item_count"] == 6
    assert [i["name"] for i in block["items"][:2]] == ["Livre 101", "Livre 102"]
    assert kpi(result, "duplicates")["status"] == "pass"


def test_a_block_still_empty_in_the_browser_says_so():
    url = "https://mode.example/p/veste"
    static = SHELL_PAGE.replace("BLOCKS", "")
    shown = SHELL_PAGE.replace("BLOCKS", '<div class="nosto_element" id="nosto-similar"></div>' + _shop_block("Les clients ont aussi acheté", ["bonnet", "gants", "pull", "botte"]))
    result = run_audit(url, fake_fetch({url: (200, static)}), render=fake_render({url: (200, shown)}, []))
    assert result["rendered"] is True
    similar = kpi(result, "similar_block")
    assert similar["status"] == "unknown" and similar["summary"] == "Non lisible : le bloc reste vide, même affiché dans un navigateur"
