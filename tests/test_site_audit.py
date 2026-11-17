"""The free recommendation audit (www.likyly.com/audit): what it finds on a page, that it only ever
reaches public addresses, and the API flow - verdicts first, the full report once an email is given,
then the report emailed. No real network: pages are served by a fake fetch / httpx MockTransport."""
import json
import uuid

import httpx
import pytest

import site_audit
import site_audits
from site_audit import AuditError, Page, SafeFetcher, run_audit


def fake_fetch(pages: dict[str, tuple[int, str]]):
    def fetch(url: str) -> Page:
        status, body = pages.get(url, (404, "<html><body>not found</body></html>"))
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
    assert block["title"] == "Produits apparentés" and block["kind"] == "rule"
    # image + title + "read more" of one card count once; the add-to-cart link is not an item
    assert [i["name"] for i in block["items"]] == ["Chaussure Trail", "Sac à dos", "Gourde", "Gourde"]
    assert kpi(result, "reco_on_page")["status"] == "pass"
    assert kpi(result, "self_reco")["status"] == "fail"
    assert kpi(result, "duplicates")["status"] == "fail"
    assert kpi(result, "unavailable")["status"] == "fail"
    assert kpi(result, "personalization")["status"] == "fail"
    assert "hasard" in kpi(result, "personalization")["detail"]
    assert kpi(result, "home")["status"] == "warn"
    assert "meilleures ventes" in kpi(result, "home")["summary"]
    assert kpi(result, "structured_data")["status"] == "pass"
    assert kpi(result, "agent_ready")["status"] == "info"
    assert result["next_step"]["href"] == "/wordpress"
    assert result["score"] < 50 and result["grade"] in ("D", "E")


SHOPIFY_PRODUCT = """<html><head><script src="https://cdn.shopify.com/s/files/theme.js"></script>
<script>var meta = {"product":{"id":4242,"vendor":"X"}};</script>
<meta property="og:type" content="product"><meta property="og:title" content="Bonnet"></head>
<body><h1>Bonnet</h1><product-recommendations class="product-recommendations" data-url="/recommendations/products?product_id=4242&section_id=x"></product-recommendations></body></html>"""

SHOPIFY_RECS = json.dumps({"products": [
    {"handle": "echarpe", "title": "Écharpe", "available": True},
    {"handle": "gants", "title": "Gants", "available": False},
    {"handle": "mitaines", "title": "Mitaines", "available": True},
    {"handle": "chaussettes", "title": "Chaussettes", "available": True},
]})


def test_shopify_reads_the_store_recommendations_endpoint():
    fetch = fake_fetch({
        "https://shop.example/products/bonnet": (200, SHOPIFY_PRODUCT),
        "https://shop.example/recommendations/products.json?product_id=4242&limit=10&intent=related": (200, SHOPIFY_RECS),
        "https://shop.example/": (200, "<html><body><h2>Recommandé pour vous</h2></body></html>"),
        "https://shop.example/.well-known/ucp": (200, '{"ucp": {"version": "2026-08-25"}}'),
    })
    result = run_audit("https://shop.example/products/bonnet", fetch)

    assert result["cms"]["id"] == "shopify" and result["page_type"]["id"] == "product"
    block = result["blocks"][0]
    assert block["source"] == "shopify_api" and block["kind"] == "auto"
    assert [i["name"] for i in block["items"] if i["unavailable"]] == ["Gants"]
    assert kpi(result, "unavailable")["status"] == "fail"
    assert kpi(result, "personalization")["status"] == "warn"
    assert kpi(result, "self_reco")["status"] == "pass"
    assert kpi(result, "agent_ready")["status"] == "pass"


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
    assert kpi(result, "home")["status"] == "fail"


def test_third_party_tool_filled_by_javascript():
    page = """<html><head><script src="https://connect.nosto.com/include/abc"></script>
    <script type="application/ld+json">{"@type":"Product","name":"Lampe"}</script></head>
    <body><div class="nosto_element" id="productpage-nosto-1"></div></body></html>"""
    result = run_audit("https://deco.example/lampe", fake_fetch({"https://deco.example/lampe": (200, page)}))

    assert [v["id"] for v in result["vendors"]] == ["nosto"]
    assert result["blocks"][0]["js_only"] is True
    assert kpi(result, "reco_on_page")["status"] == "warn"
    assert kpi(result, "personalization")["status"] == "pass"
    assert kpi(result, "self_reco")["status"] == "unknown"
    assert kpi(result, "structured_data")["status"] == "warn"
    assert result["notes"]


def test_no_recommendations_at_all():
    page = """<html><body><h1>Concert</h1><script type="application/ld+json">{"@type":"MusicEvent","name":"Concert",
    "startDate":"2026-12-01","image":"c.jpg","offers":{"price":"30","availability":"InStock"}}</script></body></html>"""
    result = run_audit("https://billets.example/concert", fake_fetch({"https://billets.example/concert": (200, page)}))
    assert result["page_type"]["id"] == "event"
    assert kpi(result, "reco_on_page")["status"] == "fail"
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
    monkeypatch.setattr(site_audits, "_run", lambda url: run_audit(url, fake_fetch(pages)))
    monkeypatch.setenv("SMTP_HOST", "smtp.test.invalid")
    monkeypatch.setenv("SMTP_USER", "noreply@likyly.test")
    monkeypatch.delenv("TURNSTILE_SECRET_KEY", raising=False)
    monkeypatch.delenv("UPGRADE_REQUEST_TO", raising=False)
    sent = []
    monkeypatch.setattr(site_audits, "deliver", lambda message, settings: sent.append(message))
    site_audits.reads.reset()
    site_audits.misses.reset()
    return {"sent": sent, "headers": {"X-Forwarded-For": f"203.0.113.{uuid.uuid4().int % 250}, 10.0.0.1"}}


def test_teaser_then_full_report_and_email(http, audits):
    headers = audits["headers"]
    created = http.post("/audits", json={"url": "boutique.example/produit/chaussure-trail/"}, headers=headers)
    assert created.status_code == 201, created.text
    teaser = created.json()
    token = teaser["token"]
    assert teaser["unlocked"] is False and teaser["score"] < 50
    assert teaser["cms"]["id"] == "woocommerce"
    assert all(set(k) == {"id", "label", "status", "summary", "weight"} for k in teaser["kpis"])
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
    def failing(url):
        raise AuditError("Le site refuse les visites automatiques (erreur 403).")
    monkeypatch.setattr(site_audits, "_run", failing)
    response = http.post("/audits", json={"url": "https://x.example/"}, headers=audits["headers"])
    assert response.status_code == 422
    assert "refuse" in response.json()["detail"]


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
    assert titles == [("Ceux qui ont aimé ce livre ont aussi aimé", "auto", 3), ("Autres livres de Frank Herbert (12)", "rule", 2)]
    # image + title of one card = one item; author links and "Voir plus" are not items
    assert kpi(result, "duplicates")["status"] == "pass"
    assert kpi(result, "personalization")["status"] == "warn"
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
