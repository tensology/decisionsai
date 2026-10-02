import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from .gateways import get_gateway
from .services import apply_gateway_event
from .models import PaymentIntent


@csrf_exempt
@require_POST
def webhook(request):
    """Gateway webhook — FakeGateway expects JSON {type, gateway_ref}. CSRF-exempt by design."""
    gw = get_gateway()
    body = request.body.decode("utf-8") or "{}"
    event = gw.parse_webhook(dict(request.headers), body)
    intent = apply_gateway_event(event)
    if intent is None:
        return JsonResponse({"detail": "Unknown intent"}, status=404)
    return JsonResponse({"status": intent.status, "order": intent.order.number})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def intent_status(request, pk):
    try:
        intent = PaymentIntent.objects.get(pk=pk, order__customer__user=request.user)
    except PaymentIntent.DoesNotExist:
        return Response({"detail": "Not found"}, status=404)
    return Response({
        "id": intent.id, "status": intent.status,
        "amount_minor": intent.amount_minor, "currency": intent.currency,
        "client_secret": intent.client_secret, "gateway_ref": intent.gateway_ref,
    })
