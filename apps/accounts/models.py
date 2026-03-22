import secrets

from django.conf import settings
from django.db import models


class ApiKey(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_keys"
    )
    key = models.CharField(max_length=64, unique=True, editable=False)
    name = models.CharField(max_length=100, default="Default")
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.name} ({self.user.username})"

    @classmethod
    def generate_key(cls) -> str:
        return secrets.token_hex(32)
