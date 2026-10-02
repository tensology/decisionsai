from django.urls import path
from . import views
urlpatterns = [
    path("home/", views.home_meta),
    path("legal/", views.legal_list),
    path("legal/<slug:slug>/", views.legal_detail),
]
