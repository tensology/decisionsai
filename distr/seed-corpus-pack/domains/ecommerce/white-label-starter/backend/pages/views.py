from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from .models import LegalPage

@api_view(["GET"])
@permission_classes([AllowAny])
def legal_list(request):
    pages = LegalPage.objects.all().values("kind", "title", "slug", "updated_at")
    return Response(list(pages))

@api_view(["GET"])
@permission_classes([AllowAny])
def legal_detail(request, slug):
    try:
        p = LegalPage.objects.get(slug=slug)
    except LegalPage.DoesNotExist:
        return Response({"detail": "Not found"}, status=404)
    return Response({
        "kind": p.kind, "title": p.title, "slug": p.slug,
        "body_html": p.body_html, "updated_at": p.updated_at,
    })

@api_view(["GET"])
@permission_classes([AllowAny])
def home_meta(request):
    return Response({
        "site_name": "White-Label Shop",
        "tagline": "Quality goods, branded for you.",
        "hero_cta": "Shop catalogue",
    })
