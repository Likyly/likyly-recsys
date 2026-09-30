"""The plan upgrade request (no billing yet: a form whose answers are stored and emailed to the
team) and the free plan's single-index cap that the form exists to lift."""
import uuid
from typing import get_args

import pytest

import db
import schemas
import upgrade_requests
from conftest import make_tenant


def valid_form(**overrides) -> dict:
    form = {
        "company_name": "Acme Store",
        "website_url": "https://acme-store.example",
        "activity_sector": "ecommerce",
        "activity_description": "Vente de chaussures de running en ligne.",
        "contact_first_name": "Camille",
        "contact_last_name": "Martin",
        "contact_role": "CTO",
        "catalog_size": "10k_100k",
        "indexes_needed": "2_5",
        "monthly_visitors": "100k_1m",
        "sync_frequency": "hourly",
        "maturity": "production_established",
        "go_live": "lt_1m",
        "current_solution": "in_house",
        "message": "On veut un index par pays.",
        "consent": True,
    }
    form.update(overrides)
    return form


@pytest.fixture
def account(api):
    """A workspace linked to a Supabase account, and the dashboard session acting as it."""
    uid = f"supabase-{uuid.uuid4().hex[:10]}"
    client_id, secret_key, _public_key = db.create_client_for_supabase_user("li_0123456789abcdef", uid, "camille@acme-store.example")
    api.app.dependency_overrides[api.get_current_supabase_user_id] = lambda: uid
    yield {"client_id": client_id, "secret": {"X-API-Key": secret_key}}
    api.app.dependency_overrides.pop(api.get_current_supabase_user_id, None)


@pytest.fixture
def mailbox(monkeypatch):
    """SMTP configured, with delivery captured instead of sent."""
    monkeypatch.setenv("SMTP_HOST", "smtp.test.invalid")
    monkeypatch.setenv("SMTP_USER", "noreply@likyly.test")
    monkeypatch.delenv("UPGRADE_REQUEST_TO", raising=False)
    sent = []
    monkeypatch.setattr(upgrade_requests, "deliver", lambda message, settings: sent.append(message))
    return sent


def stored_request(client_id: int):
    with db.SessionLocal() as session:
        return session.query(db.UpgradeRequestModel).filter_by(client_id=client_id).order_by(db.UpgradeRequestModel.id.desc()).first()


def test_request_is_stored_and_emailed_to_the_team(http, account, mailbox):
    response = http.post("/clients/me/upgrade-request", json=valid_form())
    assert response.status_code == 201

    assert len(mailbox) == 1
    message = mailbox[0]
    assert message["To"] == "contact@likyly.com"
    assert message["Reply-To"] == "camille@acme-store.example"
    assert "Acme Store" in message["Subject"] and "li_0123456789abcdef" in message["Subject"] and "\n" not in message["Subject"]
    body = message.get_content()
    for expected in [
        "https://acme-store.example", "E-commerce", "Vente de chaussures de running en ligne.", "Camille Martin (CTO)",
        "10 000 à 100 000", "2 à 5 catalogues", "Toutes les heures", "En production, trafic établi", "On veut un index par pays.",
        "Workspace : (nom non défini)", "Identifiant : li_0123456789abcdef (#", "Plan actuel : free", "Catalogues : 0 / 1", "Sources de données : 0 / 1", "Produits : 0 / 50",
    ]:
        assert expected in body, expected

    row = stored_request(account["client_id"])
    assert row.emailed_at is not None and row.email_error is None
    assert row.payload["company_name"] == "Acme Store" and "consent" not in row.payload


def test_request_is_kept_when_smtp_is_not_configured(http, account, monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    assert http.post("/clients/me/upgrade-request", json=valid_form()).status_code == 201
    row = stored_request(account["client_id"])
    assert row.emailed_at is None and "SMTP is not configured" in row.email_error


def test_request_is_kept_when_the_mail_server_fails(http, account, mailbox, monkeypatch):
    def broken(message, settings):
        raise ConnectionRefusedError("smtp down")

    monkeypatch.setattr(upgrade_requests, "deliver", broken)
    assert http.post("/clients/me/upgrade-request", json=valid_form()).status_code == 201
    row = stored_request(account["client_id"])
    assert row.emailed_at is None and "ConnectionRefusedError" in row.email_error


def test_recipient_can_be_overridden(http, account, mailbox, monkeypatch):
    monkeypatch.setenv("UPGRADE_REQUEST_TO", "sales@likyly.test")
    http.post("/clients/me/upgrade-request", json=valid_form())
    assert mailbox[0]["To"] == "sales@likyly.test"


def test_form_validation(http, account, mailbox):
    def status(**overrides):
        return http.post("/clients/me/upgrade-request", json=valid_form(**overrides)).status_code

    assert status(consent=False) == 422
    assert status(website_url="not a url") == 422
    assert status(website_url="ftp://acme.example") == 422
    assert status(company_name="   ") == 422
    assert status(contact_first_name="  ") == 422
    assert status(contact_last_name="") == 422
    assert status(catalog_size="a-lot") == 422
    assert status(activity_sector="") == 422  # the sector is still required
    assert status(message="x" * 2001) == 422
    assert mailbox == []


def test_website_scheme_is_added_and_header_injection_is_neutralized(http, account, mailbox):
    response = http.post("/clients/me/upgrade-request", json=valid_form(
        website_url="acme-store.example", company_name="Acme\r\nBcc: attacker@evil.example",
    ))
    assert response.status_code == 201
    message = mailbox[0]
    assert "\n" not in message["Subject"] and message["Bcc"] is None
    assert "https://acme-store.example" in message.get_content()


def test_only_a_few_requests_per_day(http, account, mailbox):
    for _ in range(upgrade_requests.UPGRADE_REQUESTS_PER_DAY):
        assert http.post("/clients/me/upgrade-request", json=valid_form()).status_code == 201
    assert http.post("/clients/me/upgrade-request", json=valid_form()).status_code == 429
    assert len(mailbox) == upgrade_requests.UPGRADE_REQUESTS_PER_DAY


def test_requires_a_dashboard_session(http):
    assert http.post("/clients/me/upgrade-request", json=valid_form()).status_code == 401


def test_every_form_choice_has_a_label_for_the_email():
    for literal, labels in [
        (schemas.UpgradeSector, upgrade_requests.SECTOR_LABELS),
        (schemas.UpgradeCatalogSize, upgrade_requests.CATALOG_SIZE_LABELS),
        (schemas.UpgradeIndexesNeeded, upgrade_requests.INDEXES_LABELS),
        (schemas.UpgradeMonthlyVisitors, upgrade_requests.VISITORS_LABELS),
        (schemas.UpgradeSyncFrequency, upgrade_requests.SYNC_LABELS),
        (schemas.UpgradeMaturity, upgrade_requests.MATURITY_LABELS),
        (schemas.UpgradeGoLive, upgrade_requests.GO_LIVE_LABELS),
        (schemas.UpgradeCurrentSolution, upgrade_requests.SOLUTION_LABELS),
    ]:
        assert set(get_args(literal)) == set(labels)


def test_usage_reports_index_and_data_source_caps(http, account):
    usage = http.get("/clients/me/usage").json()
    assert usage["plan"] == "free"
    assert (usage["index_count"], usage["index_limit"]) == (0, 1)
    assert (usage["data_source_count"], usage["data_source_limit"]) == (0, 1)

    http.put("/items/SKU-1", params={"data_product_type": "shoes"}, headers=account["secret"], json={"title": "Nike"})
    assert http.get("/clients/me/usage").json()["index_count"] == 1


class TestIndexLimit:
    def test_free_plan_cannot_open_a_second_index(self, http):
        tenant = make_tenant(plan=db.PLAN_FREE)
        assert http.put("/items/a", params={"data_product_type": "shoes"}, headers=tenant.secret, json={"title": "t"}).status_code == 201

        blocked = http.put("/items/b", params={"data_product_type": "books"}, headers=tenant.secret, json={"title": "t"})
        assert blocked.status_code == 403
        detail = blocked.json()["detail"]
        assert "Free plan limit reached: 1 catalog max" in detail and db.UPGRADE_URL in detail

        # The index it has stays fully usable: new items and updates.
        assert http.put("/items/c", params={"data_product_type": "shoes"}, headers=tenant.secret, json={"title": "t"}).status_code == 201
        assert http.put("/items/a", params={"data_product_type": "shoes"}, headers=tenant.secret, json={"title": "renamed"}).status_code == 200

    def test_batch_import_into_a_second_index_is_refused_per_item(self, http):
        tenant = make_tenant(plan=db.PLAN_FREE)
        http.put("/items/a", params={"data_product_type": "shoes"}, headers=tenant.secret, json={"title": "t"})
        result = http.post("/items/import", params={"data_product_type": "books"}, headers=tenant.secret, json={"items": [{"item_id": "x", "title": "t"}, {"item_id": "y", "title": "t"}]}).json()
        assert (result["succeeded"], result["failed"]) == (0, 2)
        assert "1 catalog max" in result["errors"][0]["message"]

    def test_a_batch_may_open_the_first_index(self, http):
        tenant = make_tenant(plan=db.PLAN_FREE)
        result = http.post("/items/import", params={"data_product_type": "shoes"}, headers=tenant.secret, json={"items": [{"item_id": "x", "title": "t"}, {"item_id": "y", "title": "t"}]}).json()
        assert (result["succeeded"], result["failed"]) == (2, 0)

    def test_pro_plan_gets_more_indexes_and_says_pro_when_it_hits_its_cap(self, http, monkeypatch):
        monkeypatch.setitem(db.PLAN_LIMITS[db.PLAN_PRO], "index_limit", 2)
        tenant = make_tenant(plan=db.PLAN_PRO)
        for name in ("shoes", "books"):
            assert http.put("/items/a", params={"data_product_type": name}, headers=tenant.secret, json={"title": "t"}).status_code == 201
        blocked = http.put("/items/a", params={"data_product_type": "films"}, headers=tenant.secret, json={"title": "t"})
        assert blocked.status_code == 403 and "Pro plan limit reached: 2 catalogs max" in blocked.json()["detail"]

    def test_unlimited_plan_has_no_index_cap(self, http):
        tenant = make_tenant(plan=db.PLAN_UNLIMITED)
        for name in ("a", "b", "c"):
            assert http.put("/items/x", params={"data_product_type": name}, headers=tenant.secret, json={"title": "t"}).status_code == 201


def test_the_email_shows_the_display_name_once_the_owner_has_set_one(http, account, mailbox):
    db.set_client_display_name(account["client_id"], "Boutique Acme")
    http.post("/clients/me/upgrade-request", json=valid_form())
    body = mailbox[0].get_content()
    assert "Workspace : Boutique Acme" in body and "Identifiant : li_0123456789abcdef" in body


def test_company_name_is_independent_of_the_workspace_name(http, account, mailbox):
    db.set_client_display_name(account["client_id"], "Boutique Acme")
    http.post("/clients/me/upgrade-request", json=valid_form(company_name="Acme SAS"))
    body = mailbox[0].get_content()
    assert "Nom : Acme SAS" in body and "Workspace : Boutique Acme" in body


MINIMAL_FORM = {
    "company_name": "Acme SAS", "activity_sector": "ecommerce",
    "contact_first_name": "Camille", "contact_last_name": "Martin", "consent": True,
}


def test_only_company_sector_name_and_consent_are_required(http, account, mailbox):
    response = http.post("/clients/me/upgrade-request", json=MINIMAL_FORM)
    assert response.status_code == 201, response.text
    body = mailbox[0].get_content()
    assert "Site / application : non renseigné" in body and "Activité : non renseigné" in body
    assert "Taille du catalogue : non renseigné" in body and "Stade : non renseigné" in body
    assert "MESSAGE" not in body
    row = stored_request(account["client_id"])
    assert row.payload["website_url"] is None and row.payload["catalog_size"] is None


@pytest.mark.parametrize("missing", ["company_name", "activity_sector", "contact_first_name", "contact_last_name", "consent"])
def test_each_remaining_required_field_is_enforced(http, account, mailbox, missing):
    form = {k: v for k, v in MINIMAL_FORM.items() if k != missing}
    assert http.post("/clients/me/upgrade-request", json=form).status_code == 422
    assert mailbox == []


def test_blank_answers_are_treated_as_not_answered(http, account, mailbox):
    # What the form sends for a field left empty: "" for text and unselected choices.
    blank = {"website_url": "", "activity_description": "   ", "catalog_size": "", "indexes_needed": "", "monthly_visitors": "",
             "sync_frequency": "", "maturity": "", "go_live": "", "current_solution": "", "message": "", "contact_role": ""}
    assert http.post("/clients/me/upgrade-request", json={**MINIMAL_FORM, **blank}).status_code == 201
    row = stored_request(account["client_id"])
    assert all(row.payload[key] is None for key in blank)


def test_a_filled_website_is_still_validated(http, account, mailbox):
    assert http.post("/clients/me/upgrade-request", json={**MINIMAL_FORM, "website_url": "not a url"}).status_code == 422
