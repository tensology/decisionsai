import pytest
from pages.models import LegalPage

@pytest.mark.django_db
def test_legal_pages_200(api_client):
    LegalPage.objects.create(
        kind=LegalPage.Kind.TERMS, title="Terms", slug="terms",
        body_html="<p>Terms body</p>",
    )
    LegalPage.objects.create(
        kind=LegalPage.Kind.PRIVACY, title="Privacy", slug="privacy",
        body_html="<p>Privacy body</p>",
    )
    for slug in ("terms", "privacy"):
        r = api_client.get(f"/api/pages/legal/{slug}/")
        assert r.status_code == 200
        assert r.data["title"]
        assert "body" in r.data["body_html"].lower() or "<p>" in r.data["body_html"]

@pytest.mark.django_db
def test_home_meta_200(api_client):
    r = api_client.get("/api/pages/home/")
    assert r.status_code == 200
    assert "site_name" in r.data

@pytest.mark.django_db
def test_unfold_admin_login_200(client):
    r = client.get("/admin/login/")
    assert r.status_code == 200
    content = r.content.decode()
    assert "login" in content.lower() or "username" in content.lower() or "email" in content.lower()

@pytest.mark.django_db
def test_catalog_200(api_client, catalog):
    r = api_client.get("/api/shop/catalog/")
    assert r.status_code == 200
    assert len(r.data) >= 1
