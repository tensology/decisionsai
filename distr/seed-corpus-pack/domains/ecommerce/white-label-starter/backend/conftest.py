import pytest
from django.core import mail

@pytest.fixture
def api_client():
    from rest_framework.test import APIClient
    return APIClient()

@pytest.fixture
def user(db):
    from django.contrib.auth import get_user_model
    from accounts.models import Customer
    User = get_user_model()
    u = User.objects.create_user(username="alice", email="alice@example.com", password="Secret123!")
    u.email_verified = True
    u.save()
    Customer.objects.create(user=u, name="Alice", email=u.email)
    return u

@pytest.fixture
def catalog(db):
    from shop.models import Category, Product, Variant
    cat = Category.objects.create(name="Essentials", slug="essentials")
    p = Product.objects.create(name="Classic Tee", slug="classic-tee", description="A tee", category=cat)
    v = Variant.objects.create(product=p, sku="TEE-S", list_price_minor=29900, currency="ZAR", stock_on_hand=10)
    return {"category": cat, "product": p, "variant": v}
