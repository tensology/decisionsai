from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import LegalPage

@admin.register(LegalPage)
class LegalPageAdmin(ModelAdmin):
    list_display = ("title", "kind", "slug", "updated_at")
    prepopulated_fields = {"slug": ("title",)}
