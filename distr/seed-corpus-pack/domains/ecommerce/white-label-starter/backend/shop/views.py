from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from accounts.models import Customer
from .models import Product, Category, Order, Cart
from .serializers import (
    ProductListSerializer, ProductDetailSerializer, CategorySerializer,
    CartSerializer, CartMutateSerializer, CheckoutSerializer, OrderSerializer,
)
from .services import get_or_create_cart, mutate_cart, place_order


def _customer_for(request):
    if request.user.is_authenticated and hasattr(request.user, "customer"):
        return request.user.customer
    return None


def _cart_for(request):
    customer = _customer_for(request)
    if not request.session.session_key:
        request.session.create()
    return get_or_create_cart(customer=customer, session_key=request.session.session_key)


@api_view(["GET"])
@permission_classes([AllowAny])
def catalog(request):
    qs = Product.objects.filter(is_active=True).prefetch_related("variants", "category")
    cat = request.query_params.get("category")
    if cat:
        qs = qs.filter(category__slug=cat)
    return Response(ProductListSerializer(qs, many=True).data)


@api_view(["GET"])
@permission_classes([AllowAny])
def product_detail(request, slug):
    try:
        p = Product.objects.prefetch_related("variants").get(slug=slug, is_active=True)
    except Product.DoesNotExist:
        return Response({"detail": "Not found"}, status=404)
    return Response(ProductDetailSerializer(p).data)


@api_view(["GET"])
@permission_classes([AllowAny])
def categories(request):
    return Response(CategorySerializer(Category.objects.all(), many=True).data)


@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def cart_view(request):
    cart = _cart_for(request)
    if request.method == "GET":
        return Response(CartSerializer(cart).data)
    ser = CartMutateSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    mutate_cart(cart, ser.validated_data["variant_id"], ser.validated_data["quantity"])
    cart.refresh_from_db()
    return Response(CartSerializer(cart).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def checkout(request):
    ser = CheckoutSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    customer = _customer_for(request)
    if not customer:
        customer = Customer.objects.create(
            user=request.user, name=request.user.get_full_name() or request.user.email,
            email=request.user.email,
        )
    cart = _cart_for(request)
    try:
        order = place_order(
            cart=cart, customer=customer,
            address=ser.validated_data,
            idempotency_key=ser.validated_data["idempotency_key"],
        )
    except ValueError as e:
        return Response({"detail": str(e)}, status=400)
    from payments.services import create_payment_intent
    intent = create_payment_intent(order)
    data = OrderSerializer(order).data
    data["payment_intent_id"] = intent.id
    data["client_secret"] = intent.client_secret
    return Response(data, status=201)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def order_list(request):
    customer = _customer_for(request)
    if not customer:
        return Response([])
    qs = Order.objects.filter(customer=customer).prefetch_related("items").order_by("-created_at")
    return Response(OrderSerializer(qs, many=True).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def order_detail(request, number):
    customer = _customer_for(request)
    try:
        order = Order.objects.prefetch_related("items").get(number=number, customer=customer)
    except Order.DoesNotExist:
        return Response({"detail": "Not found"}, status=404)
    return Response(OrderSerializer(order).data)
