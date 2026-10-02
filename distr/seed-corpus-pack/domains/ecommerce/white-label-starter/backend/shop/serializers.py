from rest_framework import serializers
from .models import Category, Product, Variant, Cart, LineItem, Order, OrderItem


class VariantSerializer(serializers.ModelSerializer):
    unit_price_minor = serializers.IntegerField(read_only=True)

    class Meta:
        model = Variant
        fields = ("id", "sku", "label", "list_price_minor", "sale_price_minor",
                  "unit_price_minor", "currency", "stock_on_hand", "is_active")


class ProductListSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True, default=None)
    from_price_minor = serializers.SerializerMethodField()
    currency = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = ("id", "name", "slug", "description", "category_name", "from_price_minor", "currency")

    def get_from_price_minor(self, obj):
        v = obj.variants.filter(is_active=True).order_by("list_price_minor").first()
        return v.unit_price_minor if v else None

    def get_currency(self, obj):
        v = obj.variants.filter(is_active=True).first()
        return v.currency if v else "ZAR"


class ProductDetailSerializer(serializers.ModelSerializer):
    variants = VariantSerializer(many=True, read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True, default=None)

    class Meta:
        model = Product
        fields = ("id", "name", "slug", "description", "category_name", "variants", "is_active")


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ("id", "name", "slug")


class LineItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="variant.product.name", read_only=True)
    sku = serializers.CharField(source="variant.sku", read_only=True)
    line_total_minor = serializers.SerializerMethodField()

    class Meta:
        model = LineItem
        fields = ("id", "variant", "sku", "product_name", "quantity", "unit_price_minor", "line_total_minor")

    def get_line_total_minor(self, obj):
        return obj.line_total_minor()


class CartSerializer(serializers.ModelSerializer):
    lines = LineItemSerializer(many=True, read_only=True)
    total_minor = serializers.SerializerMethodField()

    class Meta:
        model = Cart
        fields = ("id", "currency", "lines", "total_minor", "updated_at")

    def get_total_minor(self, obj):
        return obj.total_minor()


class CartMutateSerializer(serializers.Serializer):
    variant_id = serializers.IntegerField()
    quantity = serializers.IntegerField()


class CheckoutSerializer(serializers.Serializer):
    idempotency_key = serializers.CharField(max_length=64)
    name = serializers.CharField(required=False, allow_blank=True)
    line1 = serializers.CharField()
    line2 = serializers.CharField(required=False, allow_blank=True)
    city = serializers.CharField()
    region = serializers.CharField(required=False, allow_blank=True)
    postal_code = serializers.CharField()
    country = serializers.CharField(default="ZA")


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = ("id", "sku", "product_name", "quantity", "unit_price_minor", "line_total_minor")


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = (
            "id", "number", "payment_state", "fulfillment_state", "lifecycle_state",
            "currency", "subtotal_minor", "shipping_minor", "tax_minor", "total_minor",
            "shipping_name", "shipping_line1", "shipping_line2", "shipping_city",
            "shipping_region", "shipping_postal_code", "shipping_country",
            "placed_at", "created_at", "items",
        )
