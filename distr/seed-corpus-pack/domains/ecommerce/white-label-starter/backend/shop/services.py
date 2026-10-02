"""Checkout / cart domain services."""
from django.db import transaction
from django.utils import timezone
from accounts.models import Customer, Address
from .models import Cart, LineItem, Order, OrderItem, Variant, Fulfillment


def get_or_create_cart(*, customer=None, session_key=""):
    qs = Cart.objects.all()
    if customer:
        cart, _ = Cart.objects.get_or_create(customer=customer)
        return cart
    if session_key:
        cart, _ = Cart.objects.get_or_create(session_key=session_key, customer=None)
        return cart
    cart = Cart.objects.create(session_key="")
    return cart


def mutate_cart(cart: Cart, variant_id: int, quantity: int) -> Cart:
    variant = Variant.objects.select_related("product").get(pk=variant_id, is_active=True)
    if quantity <= 0:
        LineItem.objects.filter(cart=cart, variant=variant).delete()
        return cart
    line, created = LineItem.objects.get_or_create(
        cart=cart, variant=variant,
        defaults={"quantity": quantity, "unit_price_minor": variant.unit_price_minor},
    )
    if not created:
        line.quantity = quantity
        line.unit_price_minor = variant.unit_price_minor
        line.save()
    return cart


@transaction.atomic
def place_order(*, cart: Cart, customer: Customer, address: dict, idempotency_key: str) -> Order:
    existing = Order.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        return existing
    if not cart.lines.exists():
        raise ValueError("Cart is empty")
    subtotal = cart.total_minor()
    shipping = 0 if subtotal >= 50000 else 9900  # free over R500
    tax = 0
    total = subtotal + shipping + tax
    order = Order.objects.create(
        customer=customer,
        lifecycle_state=Order.LifecycleState.PLACED,
        payment_state=Order.PaymentState.PENDING,
        fulfillment_state=Order.FulfillmentState.UNFULFILLED,
        currency=cart.currency,
        subtotal_minor=subtotal,
        shipping_minor=shipping,
        tax_minor=tax,
        total_minor=total,
        shipping_name=address.get("name") or customer.name,
        shipping_line1=address["line1"],
        shipping_line2=address.get("line2", ""),
        shipping_city=address["city"],
        shipping_region=address.get("region", ""),
        shipping_postal_code=address["postal_code"],
        shipping_country=address.get("country", "ZA"),
        idempotency_key=idempotency_key,
        placed_at=timezone.now(),
    )
    for line in cart.lines.select_related("variant", "variant__product"):
        OrderItem.objects.create(
            order=order,
            variant=line.variant,
            sku=line.variant.sku,
            product_name=line.variant.product.name,
            quantity=line.quantity,
            unit_price_minor=line.unit_price_minor,
            line_total_minor=line.line_total_minor(),
        )
        # soft stock decrement
        v = line.variant
        if v.stock_on_hand >= line.quantity:
            v.stock_on_hand -= line.quantity
            v.save(update_fields=["stock_on_hand"])
    Fulfillment.objects.create(order=order, status="pending")
    cart.lines.all().delete()
    from mailer.services import send_order_confirmation
    send_order_confirmation(order)
    return order
