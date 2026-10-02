import json
import pytest
from shop.models import Order
from payments.models import PaymentIntent

@pytest.mark.django_db
def test_place_order_idempotent(api_client, user, catalog):
    api_client.force_authenticate(user=user)
    api_client.post("/api/shop/cart/", {"variant_id": catalog["variant"].id, "quantity": 2}, format="json")
    payload = {
        "idempotency_key": "idem-abc",
        "line1": "1 Main St", "city": "Cape Town", "postal_code": "8001", "country": "ZA",
    }
    r1 = api_client.post("/api/shop/checkout/", payload, format="json")
    assert r1.status_code == 201, r1.content
    r2 = api_client.post("/api/shop/checkout/", payload, format="json")
    assert r2.status_code == 201
    assert r1.data["number"] == r2.data["number"]
    assert Order.objects.filter(idempotency_key="idem-abc").count() == 1

@pytest.mark.django_db
def test_fake_payment_webhook(api_client, user, catalog):
    api_client.force_authenticate(user=user)
    api_client.post("/api/shop/cart/", {"variant_id": catalog["variant"].id, "quantity": 1}, format="json")
    r = api_client.post("/api/shop/checkout/", {
        "idempotency_key": "idem-pay",
        "line1": "1 Main St", "city": "Cape Town", "postal_code": "8001",
    }, format="json")
    assert r.status_code == 201
    intent = PaymentIntent.objects.get(id=r.data["payment_intent_id"])
    wr = api_client.post(
        "/api/payments/webhook/",
        data=json.dumps({"type": "payment.succeeded", "gateway_ref": intent.gateway_ref}),
        content_type="application/json",
    )
    assert wr.status_code == 200, wr.content
    intent.refresh_from_db()
    assert intent.status == PaymentIntent.Status.SUCCEEDED
    order = intent.order
    order.refresh_from_db()
    assert order.payment_state == Order.PaymentState.PAID
