import pytest
from shop.models import Order, Variant

@pytest.mark.django_db
def test_variant_unit_price_uses_sale(catalog):
    v = catalog["variant"]
    assert v.unit_price_minor == 29900
    v.sale_price_minor = 24900
    v.save()
    assert Variant.objects.get(pk=v.pk).unit_price_minor == 24900

@pytest.mark.django_db
def test_order_axes_defaults(user, catalog):
    from accounts.models import Customer
    c = user.customer
    o = Order.objects.create(
        customer=c, idempotency_key="k1",
        subtotal_minor=100, total_minor=100,
    )
    assert o.payment_state == Order.PaymentState.PENDING
    assert o.fulfillment_state == Order.FulfillmentState.UNFULFILLED
    assert o.lifecycle_state == Order.LifecycleState.DRAFT
    assert o.number.startswith("ORD-")
