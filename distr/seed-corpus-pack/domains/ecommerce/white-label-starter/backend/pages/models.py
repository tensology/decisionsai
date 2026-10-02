from django.db import models

class LegalPage(models.Model):
    class Kind(models.TextChoices):
        TERMS = "terms", "Terms of Service"
        PRIVACY = "privacy", "Privacy Policy"
        ABOUT = "about", "About"
        CONTACT = "contact", "Contact"

    kind = models.CharField(max_length=20, choices=Kind.choices, unique=True)
    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    body_html = models.TextField()
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title
