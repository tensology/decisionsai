"""Payment adapter seam — FakeGateway for tests; PayFast/Stripe-style stubs."""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class GatewayEvent:
    event_type: str  # payment.succeeded | payment.failed | …
    gateway_ref: str
    payload: dict


class PaymentGateway(ABC):
    name: str

    @abstractmethod
    def create_intent(self, amount_minor: int, currency: str, metadata: dict) -> dict:
        ...

    @abstractmethod
    def parse_webhook(self, headers: dict, body: bytes | str) -> GatewayEvent:
        ...


class FakeGateway(PaymentGateway):
    name = "fake"

    def create_intent(self, amount_minor: int, currency: str, metadata: dict) -> dict:
        return {
            "gateway_ref": metadata.get("gateway_ref") or f"pi_fake_{amount_minor}",
            "status": "requires_payment",
            "raw": {"amount_minor": amount_minor, "currency": currency},
        }

    def parse_webhook(self, headers: dict, body: bytes | str) -> GatewayEvent:
        import json
        data = json.loads(body) if isinstance(body, (bytes, str)) else body
        return GatewayEvent(
            event_type=data.get("type", "payment.succeeded"),
            gateway_ref=data["gateway_ref"],
            payload=data,
        )


class PayFastStubGateway(PaymentGateway):
    """Stub hooks for PayFast — no real credentials / network."""
    name = "payfast"

    def create_intent(self, amount_minor: int, currency: str, metadata: dict) -> dict:
        return {"gateway_ref": f"pf_stub_{amount_minor}", "status": "requires_payment", "raw": {"stub": True}}

    def parse_webhook(self, headers: dict, body: bytes | str) -> GatewayEvent:
        import json
        data = json.loads(body) if isinstance(body, (str, bytes)) else body
        return GatewayEvent(event_type="payment.succeeded", gateway_ref=data.get("m_payment_id", ""), payload=data)


class StripeStyleStubGateway(PaymentGateway):
    name = "stripe_stub"

    def create_intent(self, amount_minor: int, currency: str, metadata: dict) -> dict:
        return {"gateway_ref": f"pi_stripe_stub_{amount_minor}", "status": "requires_payment", "raw": {"stub": True}}

    def parse_webhook(self, headers: dict, body: bytes | str) -> GatewayEvent:
        import json
        data = json.loads(body) if isinstance(body, (str, bytes)) else body
        et = data.get("type", "payment_intent.succeeded")
        ref = data.get("data", {}).get("object", {}).get("id", data.get("gateway_ref", ""))
        mapped = "payment.succeeded" if "succeeded" in et else "payment.failed"
        return GatewayEvent(event_type=mapped, gateway_ref=ref, payload=data)


def get_gateway(name: str | None = None) -> PaymentGateway:
    from django.conf import settings
    key = (name or getattr(settings, "PAYMENT_GATEWAY", "fake")).lower()
    mapping = {
        "fake": FakeGateway,
        "payfast": PayFastStubGateway,
        "stripe": StripeStyleStubGateway,
        "stripe_stub": StripeStyleStubGateway,
    }
    return mapping.get(key, FakeGateway)()
