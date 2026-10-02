from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import PaymentIntent, PaymentMethod

@admin.register(PaymentIntent)
class PaymentIntentAdmin(ModelAdmin):
    list_display = ("gateway_ref", "order", "amount_minor", "currency", "status", "gateway", "created_at")
    list_filter = ("status", "gateway")

@admin.register(PaymentMethod)
class PaymentMethodAdmin(ModelAdmin):
    list_display = ("customer", "brand", "last4", "is_default", "created_at")
