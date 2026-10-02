from django.views.decorators.csrf import ensure_csrf_cookie
from django.middleware.csrf import get_token
from django.contrib.auth import login, logout
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from .models import User
from .serializers import (
    RegisterSerializer, LoginSerializer, ProfileSerializer,
    PasswordChangeSerializer, ForgotPasswordSerializer, ResetPasswordSerializer,
    EmailChangeSerializer, AddressSerializer,
)
from mailer.services import send_password_reset, send_email_change


@api_view(["POST"])
@permission_classes([AllowAny])
def register(request):
    ser = RegisterSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    user = ser.save()
    return Response({"id": user.id, "email": user.email, "detail": "Registered. Check email to activate."}, status=201)


@api_view(["POST"])
@permission_classes([AllowAny])
def activate(request):
    token = request.data.get("token") or request.query_params.get("token")
    if not token:
        return Response({"detail": "token required"}, status=400)
    try:
        user = User.objects.get(activation_token=token)
    except User.DoesNotExist:
        return Response({"detail": "Invalid token"}, status=400)
    user.email_verified = True
    user.activation_token = ""
    user.save(update_fields=["email_verified", "activation_token"])
    return Response({"detail": "Activated"})


@api_view(["POST"])
@permission_classes([AllowAny])
def login_view(request):
    ser = LoginSerializer(data=request.data, context={"request": request})
    ser.is_valid(raise_exception=True)
    user = ser.validated_data["user"]
    login(request, user)
    return Response(ProfileSerializer(user).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def logout_view(request):
    logout(request)
    return Response({"detail": "Logged out"})


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def profile(request):
    if request.method == "GET":
        return Response(ProfileSerializer(request.user).data)
    ser = ProfileSerializer(request.user, data=request.data, partial=True)
    ser.is_valid(raise_exception=True)
    ser.save()
    return Response(ser.data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def change_password(request):
    ser = PasswordChangeSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    if not request.user.check_password(ser.validated_data["current_password"]):
        return Response({"detail": "Current password incorrect"}, status=400)
    request.user.set_password(ser.validated_data["new_password"])
    request.user.save()
    login(request, request.user)
    return Response({"detail": "Password updated"})


@api_view(["POST"])
@permission_classes([AllowAny])
def forgot_password(request):
    ser = ForgotPasswordSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    try:
        user = User.objects.get(email__iexact=ser.validated_data["email"])
        token = user.issue_reset_token()
        send_password_reset(user, token)
    except User.DoesNotExist:
        pass
    return Response({"detail": "If that email exists, a reset link was sent."})


@api_view(["POST"])
@permission_classes([AllowAny])
def reset_password(request):
    ser = ResetPasswordSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    try:
        user = User.objects.get(reset_token=ser.validated_data["token"])
    except User.DoesNotExist:
        return Response({"detail": "Invalid token"}, status=400)
    user.set_password(ser.validated_data["new_password"])
    user.reset_token = ""
    user.save()
    return Response({"detail": "Password reset"})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def request_email_change(request):
    ser = EmailChangeSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    token = request.user.issue_email_change_token(ser.validated_data["new_email"])
    send_email_change(request.user, token, ser.validated_data["new_email"])
    return Response({"detail": "Confirmation sent to new email (stub token flow)."})


@api_view(["POST"])
@permission_classes([AllowAny])
def confirm_email_change(request):
    token = request.data.get("token")
    try:
        user = User.objects.get(email_change_token=token)
    except User.DoesNotExist:
        return Response({"detail": "Invalid token"}, status=400)
    user.email = user.pending_email
    user.pending_email = ""
    user.email_change_token = ""
    user.email_verified = True
    user.save()
    if hasattr(user, "customer"):
        user.customer.email = user.email
        user.customer.save(update_fields=["email"])
    return Response({"detail": "Email updated"})


@api_view(["GET"])
@permission_classes([AllowAny])
@ensure_csrf_cookie
def csrf(request):
    return Response({"csrfToken": get_token(request)})
