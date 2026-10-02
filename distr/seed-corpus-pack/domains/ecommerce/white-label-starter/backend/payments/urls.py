from django.urls import path
from . import views
urlpatterns = [
    path("webhook/", views.webhook),
    path("intents/<int:pk>/", views.intent_status),
]
