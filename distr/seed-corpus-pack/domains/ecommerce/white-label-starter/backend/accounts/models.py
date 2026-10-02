from django.contrib.auth.models import AbstractUser
from django.db import models
import secrets

class User(AbstractUser):
    email = models.EmailField(unique=True)
    email_verified = models.BooleanField(default=False)
    activation_token = models.CharField(max_length=64, blank=True, default="")
    reset_token = models.CharField(max_length=64, blank=True, default="")
    pending_email = models.EmailField(blank=True, default="")
    email_change_token = models.CharField(max_length=64, blank=True, default="")

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    def issue_activation_token(self):
        self.activation_token = secrets.token_urlsafe(32)
        self.save(update_fields=["activation_token"])
        return self.activation_token

    def issue_reset_token(self):
        self.reset_token = secrets.token_urlsafe(32)
        self.save(update_fields=["reset_token"])
        return self.reset_token

    def issue_email_change_token(self, new_email: str):
        self.pending_email = new_email
        self.email_change_token = secrets.token_urlsafe(32)
        self.save(update_fields=["pending_email", "email_change_token"])
        return self.email_change_token


class Customer(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="customer", null=True, blank=True)
    name = models.CharField(max_length=200)
    email = models.EmailField()
    phone = models.CharField(max_length=40, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} <{self.email}>"


class Address(models.Model):
    class Kind(models.TextChoices):
        SHIPPING = "shipping", "Shipping"
        BILLING = "billing", "Billing"

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="addresses")
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.SHIPPING)
    line1 = models.CharField(max_length=200)
    line2 = models.CharField(max_length=200, blank=True, default="")
    city = models.CharField(max_length=100)
    region = models.CharField(max_length=100, blank=True, default="")
    postal_code = models.CharField(max_length=20)
    country = models.CharField(max_length=2, default="ZA")
    is_default = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.line1}, {self.city}"
