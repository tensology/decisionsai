import pytest
from django.contrib.auth import get_user_model
from django.core import mail

User = get_user_model()

@pytest.mark.django_db
def test_register_and_activate(api_client):
    r = api_client.post("/api/auth/register/", {
        "email": "bob@example.com", "password": "Secret123!", "name": "Bob",
    }, format="json")
    assert r.status_code == 201
    user = User.objects.get(email="bob@example.com")
    assert user.activation_token
    assert len(mail.outbox) >= 1
    r2 = api_client.post("/api/auth/activate/", {"token": user.activation_token}, format="json")
    assert r2.status_code == 200
    user.refresh_from_db()
    assert user.email_verified

@pytest.mark.django_db
def test_password_reset_token_path(api_client, user):
    r = api_client.post("/api/auth/password/forgot/", {"email": user.email}, format="json")
    assert r.status_code == 200
    user.refresh_from_db()
    assert user.reset_token
    r2 = api_client.post("/api/auth/password/reset/", {
        "token": user.reset_token, "new_password": "NewSecret123!",
    }, format="json")
    assert r2.status_code == 200
    user.refresh_from_db()
    assert user.check_password("NewSecret123!")
    assert user.reset_token == ""
