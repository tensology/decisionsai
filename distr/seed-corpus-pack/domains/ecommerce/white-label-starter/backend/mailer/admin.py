from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import EmailLog

@admin.register(EmailLog)
class EmailLogAdmin(ModelAdmin):
    list_display = ("to_email", "subject", "template_key", "status", "created_at")
    readonly_fields = ("to_email", "subject", "template_key", "body_preview", "status", "created_at")
