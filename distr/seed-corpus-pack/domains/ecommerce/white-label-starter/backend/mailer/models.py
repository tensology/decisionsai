from django.db import models

class EmailLog(models.Model):
    to_email = models.EmailField()
    subject = models.CharField(max_length=255)
    template_key = models.CharField(max_length=64)
    body_preview = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, default="sent")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.template_key} → {self.to_email}"
