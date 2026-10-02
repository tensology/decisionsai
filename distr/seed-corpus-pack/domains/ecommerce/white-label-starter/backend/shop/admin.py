from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from .models import Category, Product, Variant, Cart, LineItem, Order, OrderItem, Fulfillment, Shipment

class VariantInline(TabularInline):
    model = Variant
    extra = 0

@admin.register(Category)
class CategoryAdmin(ModelAdmin):
    list_display = ("name", "slug", "parent")
    prepopulated_fields = {"slug": ("name",)}

@admin.register(Product)
class ProductAdmin(ModelAdmin):
    list_display = ("name", "slug", "category", "is_active")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [VariantInline]

@admin.register(Variant)
class VariantAdmin(ModelAdmin):
    list_display = ("sku", "product", "list_price_minor", "currency", "stock_on_hand", "is_active")
    search_fields = ("sku", "product__name")

class LineItemInline(TabularInline):
    model = LineItem
    extra = 0

@admin.register(Cart)
class CartAdmin(ModelAdmin):
    list_display = ("id", "customer", "currency", "updated_at")
    inlines = [LineItemInline]

class OrderItemInline(TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ("sku", "product_name", "quantity", "unit_price_minor", "line_total_minor", "variant")

class FulfillmentInline(TabularInline):
    model = Fulfillment
    extra = 0

@admin.register(Order)
class OrderAdmin(ModelAdmin):
    list_display = ("number", "customer", "payment_state", "fulfillment_state", "lifecycle_state", "total_minor", "currency", "placed_at")
    list_filter = ("payment_state", "fulfillment_state", "lifecycle_state")
    search_fields = ("number", "customer__email")
    inlines = [OrderItemInline, FulfillmentInline]
    readonly_fields = ("number", "idempotency_key", "subtotal_minor", "total_minor")

@admin.register(Fulfillment)
class FulfillmentAdmin(ModelAdmin):
    list_display = ("id", "order", "status", "created_at")

@admin.register(Shipment)
class ShipmentAdmin(ModelAdmin):
    list_display = ("id", "fulfillment", "carrier", "tracking_number", "shipped_at")

@admin.register(OrderItem)
class OrderItemAdmin(ModelAdmin):
    list_display = ("order", "sku", "product_name", "quantity", "line_total_minor")

@admin.register(LineItem)
class LineItemAdmin(ModelAdmin):
    list_display = ("cart", "variant", "quantity", "unit_price_minor")
