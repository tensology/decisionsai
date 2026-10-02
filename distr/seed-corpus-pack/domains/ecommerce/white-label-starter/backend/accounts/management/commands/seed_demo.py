from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from accounts.models import Customer
from shop.models import Category, Product, Variant
from pages.models import LegalPage

User = get_user_model()

TERMS = """
<h1>Terms of Service</h1>
<p>Welcome to White-Label Shop. By using this storefront you agree to these placeholder terms suitable for local demos.</p>
<ul><li>Orders are binding once placed.</li><li>Prices are shown in minor currency units on the API and formatted on the storefront.</li><li>No real payment credentials are processed in this starter.</li></ul>
"""
PRIVACY = """
<h1>Privacy Policy</h1>
<p>We store account email, order history, and shipping addresses to fulfil purchases. Demo data stays on your local SQLite database.</p>
"""
ABOUT = """
<h1>About us</h1>
<p>White-Label Shop is a forkable ecommerce starter for Decisions / Tensology white-label builds.</p>
"""
CONTACT = """
<h1>Contact</h1>
<p>Email <a href="mailto:hello@example.com">hello@example.com</a> — replace with your BrandPack contact details.</p>
"""

class Command(BaseCommand):
    help = "Seed demo admin, catalog, and legal pages"

    def add_arguments(self, parser):
        parser.add_argument("--quiet", action="store_true")

    def handle(self, *args, **opts):
        quiet = opts.get("quiet")
        user, created = User.objects.get_or_create(
            email="admin@example.com",
            defaults={"username": "admin", "is_staff": True, "is_superuser": True, "email_verified": True},
        )
        if created:
            user.set_password("adminpass123")
            user.save()
        Customer.objects.get_or_create(user=user, defaults={"name": "Admin", "email": user.email})

        cat, _ = Category.objects.get_or_create(slug="essentials", defaults={"name": "Essentials"})
        products = [
            ("Classic Tee", "classic-tee", "Soft cotton tee.", "TEE-S", 29900, 10),
            ("Canvas Tote", "canvas-tote", "Everyday tote bag.", "TOTE-1", 44900, 25),
            ("Water Bottle", "water-bottle", "Insulated 750ml.", "BTL-750", 35900, 40),
        ]
        for name, slug, desc, sku, price, stock in products:
            p, _ = Product.objects.get_or_create(slug=slug, defaults={"name": name, "description": desc, "category": cat})
            Variant.objects.get_or_create(
                sku=sku,
                defaults={"product": p, "label": "Default", "list_price_minor": price, "currency": "ZAR", "stock_on_hand": stock},
            )

        for kind, title, slug, body in [
            (LegalPage.Kind.TERMS, "Terms of Service", "terms", TERMS),
            (LegalPage.Kind.PRIVACY, "Privacy Policy", "privacy", PRIVACY),
            (LegalPage.Kind.ABOUT, "About", "about", ABOUT),
            (LegalPage.Kind.CONTACT, "Contact", "contact", CONTACT),
        ]:
            LegalPage.objects.update_or_create(kind=kind, defaults={"title": title, "slug": slug, "body_html": body})

        if not quiet:
            self.stdout.write(self.style.SUCCESS("Demo data ready (admin@example.com / adminpass123)"))
