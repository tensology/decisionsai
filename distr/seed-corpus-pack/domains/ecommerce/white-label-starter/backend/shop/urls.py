from django.urls import path
from . import views

urlpatterns = [
    path("catalog/", views.catalog),
    path("catalog/<slug:slug>/", views.product_detail),
    path("categories/", views.categories),
    path("cart/", views.cart_view),
    path("checkout/", views.checkout),
    path("orders/", views.order_list),
    path("orders/<str:number>/", views.order_detail),
]
