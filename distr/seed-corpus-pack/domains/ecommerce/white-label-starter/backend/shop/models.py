from django.db import models
from django.conf import settings
import uuid


class Category(models.Model):
    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children")

    class Meta:
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class Product(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True, default="")
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="products")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Variant(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    sku = models.CharField(max_length=64, unique=True)
    label = models.CharField(max_length=120, blank=True, default="")
    # Money: integer minor units
    list_price_minor = models.PositiveIntegerField()
    sale_price_minor = models.PositiveIntegerField(null=True, blank=True)
    currency = models.CharField(max_length=3, default="ZAR")
    stock_on_hand = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    @property
    def unit_price_minor(self) -> int:
        return self.sale_price_minor if self.sale_price_minor is not None else self.list_price_minor

    def __str__(self):
        return f"{self.product.name} ({self.sku})"


class Cart(models.Model):
    customer = models.ForeignKey("accounts.Customer", null=True, blank=True, on_delete=models.CASCADE, related_name="carts")
    session_key = models.CharField(max_length=64, blank=True, default="")
    currency = models.CharField(max_length=3, default="ZAR")
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def total_minor(self) -> int:
        return sum(li.line_total_minor() for li in self.lines.all())

    def __str__(self):
        return f"Cart {self.pk}"


class LineItem(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="lines")
    variant = models.ForeignKey(Variant, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)
    unit_price_minor = models.PositiveIntegerField()  # snapshot at add

    def line_total_minor(self) -> int:
        return self.unit_price_minor * self.quantity


class Order(models.Model):
    class PaymentState(models.TextChoices):
        PENDING = "pending", "Pending"
        AUTHORIZED = "authorized", "Authorized"
        PAID = "paid", "Paid"
        PARTIALLY_PAID = "partially_paid", "Partially paid"
        PARTIALLY_REFUNDED = "partially_refunded", "Partially refunded"
        REFUNDED = "refunded", "Refunded"
        VOIDED = "voided", "Voided"

    class FulfillmentState(models.TextChoices):
        UNFULFILLED = "unfulfilled", "Unfulfilled"
        ALLOCATED = "allocated", "Allocated"
        READY_TO_SHIP = "ready_to_ship", "Ready to ship"
        PARTIALLY_FULFILLED = "partially_fulfilled", "Partially fulfilled"
        SHIPPED = "shipped", "Shipped"
        DELIVERED = "delivered", "Delivered"
        PARTIALLY_RETURNED = "partially_returned", "Partially returned"
        RETURNED = "returned", "Returned"

    class LifecycleState(models.TextChoices):
        DRAFT = "draft", "Draft"
        PLACED = "placed", "Placed"
        IN_PROGRESS = "in_progress", "In progress"
        SHIPPED = "shipped", "Shipped"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
        REFUNDED = "refunded", "Refunded"
        PARTIALLY_COMPLETED = "partially_completed", "Partially completed"

    number = models.CharField(max_length=32, unique=True, editable=False)
    customer = models.ForeignKey("accounts.Customer", on_delete=models.PROTECT, related_name="orders")
    payment_state = models.CharField(max_length=32, choices=PaymentState.choices, default=PaymentState.PENDING)
    fulfillment_state = models.CharField(max_length=32, choices=FulfillmentState.choices, default=FulfillmentState.UNFULFILLED)
    lifecycle_state = models.CharField(max_length=32, choices=LifecycleState.choices, default=LifecycleState.DRAFT)
    currency = models.CharField(max_length=3, default="ZAR")
    subtotal_minor = models.PositiveIntegerField(default=0)
    shipping_minor = models.PositiveIntegerField(default=0)
    tax_minor = models.PositiveIntegerField(default=0)
    total_minor = models.PositiveIntegerField(default=0)
    # Address snapshots
    shipping_name = models.CharField(max_length=200, blank=True, default="")
    shipping_line1 = models.CharField(max_length=200, blank=True, default="")
    shipping_line2 = models.CharField(max_length=200, blank=True, default="")
    shipping_city = models.CharField(max_length=100, blank=True, default="")
    shipping_region = models.CharField(max_length=100, blank=True, default="")
    shipping_postal_code = models.CharField(max_length=20, blank=True, default="")
    shipping_country = models.CharField(max_length=2, blank=True, default="ZA")
    idempotency_key = models.CharField(max_length=64, unique=True)
    placed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = f"ORD-{uuid.uuid4().hex[:10].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.number


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(Variant, on_delete=models.PROTECT)
    sku = models.CharField(max_length=64)
    product_name = models.CharField(max_length=200)
    quantity = models.PositiveIntegerField()
    unit_price_minor = models.PositiveIntegerField()
    line_total_minor = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.product_name} x{self.quantity}"


class Fulfillment(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="fulfillments")
    status = models.CharField(max_length=40, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)


class Shipment(models.Model):
    fulfillment = models.ForeignKey(Fulfillment, on_delete=models.CASCADE, related_name="shipments")
    carrier = models.CharField(max_length=80, blank=True, default="")
    tracking_number = models.CharField(max_length=120, blank=True, default="")
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
