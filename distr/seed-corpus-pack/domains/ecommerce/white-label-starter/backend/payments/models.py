from django.db import models
import secrets, uuid

class PaymentMethod(models.Model):
    """Token placeholder — never store raw PANs."""
    customer = models.ForeignKey("accounts.Customer", on_delete=models.CASCADE, related_name="payment_methods")
    brand = models.CharField(max_length=40, blank=True, default="card")
    last4 = models.CharField(max_length=4, blank=True, default="4242")
    token = models.CharField(max_length=128, default="")  # opaque gateway token
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = f"tok_{secrets.token_hex(16)}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.brand} •••• {self.last4}"


class PaymentIntent(models.Model):
    class Status(models.TextChoices):
        REQUIRES_PAYMENT = "requires_payment", "Requires payment"
        PROCESSING = "processing", "Processing"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    order = models.ForeignKey("shop.Order", on_delete=models.CASCADE, related_name="payment_intents")
    amount_minor = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="ZAR")
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.REQUIRES_PAYMENT)
    gateway = models.CharField(max_length=40, default="fake")
    gateway_ref = models.CharField(max_length=128, blank=True, default="")
    client_secret = models.CharField(max_length=128, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.client_secret:
            self.client_secret = f"cs_{uuid.uuid4().hex}"
        if not self.gateway_ref:
            self.gateway_ref = f"pi_{uuid.uuid4().hex[:16]}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.gateway_ref} ({self.status})"
