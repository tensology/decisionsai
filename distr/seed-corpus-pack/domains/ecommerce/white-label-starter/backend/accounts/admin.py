from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from unfold.admin import ModelAdmin
from .models import User, Customer, Address

@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    list_display = ("email", "username", "email_verified", "is_staff", "is_active")
    search_fields = ("email", "username")
    ordering = ("email",)
    fieldsets = BaseUserAdmin.fieldsets + (
        ("Verification", {"fields": ("email_verified", "pending_email")}),
    )

class AddressInline(admin.TabularInline):
    model = Address
    extra = 0

@admin.register(Customer)
class CustomerAdmin(ModelAdmin):
    list_display = ("name", "email", "phone", "created_at")
    search_fields = ("name", "email")
    inlines = [AddressInline]

@admin.register(Address)
class AddressAdmin(ModelAdmin):
    list_display = ("customer", "kind", "city", "country", "is_default")
