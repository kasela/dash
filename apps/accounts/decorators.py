from functools import wraps

from django.http import JsonResponse
from django.utils import timezone

from .models import ApiKey


def api_key_required(view_func):
    """Allow access via API key sent in Authorization: Bearer <key> or X-API-Key header."""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        key = None
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if auth_header.startswith("Bearer "):
            key = auth_header[7:].strip()
        if not key:
            key = request.META.get("HTTP_X_API_KEY", "").strip()
        if not key:
            return JsonResponse({"error": "API key required"}, status=401)

        try:
            api_key = ApiKey.objects.select_related("user").get(key=key)
        except ApiKey.DoesNotExist:
            return JsonResponse({"error": "Invalid API key"}, status=401)

        api_key.last_used_at = timezone.now()
        api_key.save(update_fields=["last_used_at"])
        request.user = api_key.user
        return view_func(request, *args, **kwargs)

    return wrapper
