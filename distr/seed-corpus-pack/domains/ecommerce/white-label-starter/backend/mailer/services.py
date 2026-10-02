from django.core.mail import send_mail
from django.conf import settings
from django.template.loader import render_to_string
from .models import EmailLog

def _send(to, subject, template_key, context):
    html = render_to_string(f"mailer/{template_key}.html", context)
    send_mail(subject, html, settings.DEFAULT_FROM_EMAIL, [to], html_message=html)
    EmailLog.objects.create(
        to_email=to, subject=subject, template_key=template_key,
        body_preview=html[:500], status="sent",
    )

def send_welcome_activate(user, token):
    _send(user.email, "Welcome — activate your account", "welcome_activate", {
        "user": user, "token": token, "frontend_url": settings.FRONTEND_URL,
    })

def send_password_reset(user, token):
    _send(user.email, "Password reset", "password_reset", {
        "user": user, "token": token, "frontend_url": settings.FRONTEND_URL,
    })

def send_email_change(user, token, new_email):
    _send(new_email, "Confirm your new email", "email_change", {
        "user": user, "token": token, "new_email": new_email,
        "frontend_url": settings.FRONTEND_URL,
    })

def send_order_confirmation(order):
    _send(order.customer.email, f"Order {order.number} confirmed", "order_confirmation", {
        "order": order, "frontend_url": settings.FRONTEND_URL,
    })

def send_payment_failed(order):
    _send(order.customer.email, f"Payment failed for {order.number}", "payment_failed", {
        "order": order, "frontend_url": settings.FRONTEND_URL,
    })

def send_shipping_notice(order, shipment):
    _send(order.customer.email, f"Your order {order.number} has shipped", "shipping_notice", {
        "order": order, "shipment": shipment, "frontend_url": settings.FRONTEND_URL,
    })
