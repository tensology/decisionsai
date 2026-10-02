from django.urls import path
from . import views

urlpatterns = [
    path("csrf/", views.csrf),
    path("register/", views.register),
    path("activate/", views.activate),
    path("login/", views.login_view),
    path("logout/", views.logout_view),
    path("profile/", views.profile),
    path("password/change/", views.change_password),
    path("password/forgot/", views.forgot_password),
    path("password/reset/", views.reset_password),
    path("email/change/", views.request_email_change),
    path("email/confirm/", views.confirm_email_change),
]
