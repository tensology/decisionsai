from django.db import transaction
from shop.models import Order
from mailer.services import send_payment_failed, send_order_confirmation
from .models import PaymentIntent
from .gateways import get_gateway, GatewayEvent


def create_payment_intent(order: Order) -> PaymentIntent:
    gw = get_gateway()
    intent = PaymentIntent(
        order=order,
        amount_minor=order.total_minor,
        currency=order.currency,
        gateway=gw.name,
    )
    intent.save()  # assigns gateway_ref / client_secret
    gw.create_intent(order.total_minor, order.currency, {"gateway_ref": intent.gateway_ref, "order": order.number})
    return intent


@transaction.atomic
def apply_gateway_event(event: GatewayEvent) -> PaymentIntent | None:
    try:
        intent = PaymentIntent.objects.select_related("order").select_for_update().get(gateway_ref=event.gateway_ref)
    except PaymentIntent.DoesNotExist:
        return None
    order = intent.order
    if event.event_type in ("payment.succeeded", "payment_intent.succeeded"):
        intent.status = PaymentIntent.Status.SUCCEEDED
        intent.save(update_fields=["status", "updated_at"])
        order.payment_state = Order.PaymentState.PAID
        order.lifecycle_state = Order.LifecycleState.IN_PROGRESS
        order.save(update_fields=["payment_state", "lifecycle_state", "updated_at"])
    elif event.event_type in ("payment.failed", "payment_intent.payment_failed"):
        intent.status = PaymentIntent.Status.FAILED
        intent.save(update_fields=["status", "updated_at"])
        send_payment_failed(order)
    return intent
