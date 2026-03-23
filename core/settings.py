from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader without external dependencies."""
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(BASE_DIR / ".env")

SECRET_KEY = "dev-only-secret-key"
DEBUG = True
ALLOWED_HOSTS: list[str] = []

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sitemaps",
    "django_celery_results",
    "apps.accounts",
    "apps.workspaces",
    "apps.datasets",
    "apps.dashboards",
    "apps.billing.apps.BillingConfig",
    "apps.admin_portal.apps.AdminPortalConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.billing.context_processors.user_profile",
            ],
        },
    }
]

WSGI_APPLICATION = "core.wsgi.application"
ASGI_APPLICATION = "core.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_REDIRECT_URL = "app-home"
LOGOUT_REDIRECT_URL = "landing"

# ── LemonSqueezy settings (set via environment variables) ──────────────────────
LEMONSQUEEZY_API_KEY = os.environ.get("LEMONSQUEEZY_API_KEY", "")
LEMONSQUEEZY_WEBHOOK_SECRET = os.environ.get("LEMONSQUEEZY_WEBHOOK_SECRET", "")
LEMONSQUEEZY_PRO_VARIANT_ID = os.environ.get("LEMONSQUEEZY_PRO_VARIANT_ID", "")
LEMONSQUEEZY_STORE_SLUG = os.environ.get("LEMONSQUEEZY_STORE_SLUG", "")

# ── AI settings (DeepSeek) ─────────────────────────────────────────────────────
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
# Timeout (seconds) for the TCP/TLS handshake phase — fail fast when the API endpoint is unreachable
DEEPSEEK_CONNECT_TIMEOUT = int(os.environ.get("DEEPSEEK_CONNECT_TIMEOUT", 10))
# Timeout (seconds) for receiving the full dashboard-specs response (large prompt → longer budget)
DEEPSEEK_SPECS_TIMEOUT = int(os.environ.get("DEEPSEEK_SPECS_TIMEOUT", 60))
# Number of automatic retries on transient server errors (0 = no retries)
DEEPSEEK_MAX_RETRIES = int(os.environ.get("DEEPSEEK_MAX_RETRIES", 0))

# Site metadata for SEO
SITE_NAME = "DashAI"
SITE_DOMAIN = os.environ.get("SITE_DOMAIN", "https://dashai.io")

# ── Celery configuration ───────────────────────────────────────────────────────
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
CELERY_RESULT_BACKEND = "django-db"
CELERY_CACHE_BACKEND = "default"
CELERY_RESULT_EXTENDED = True
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_SOFT_TIME_LIMIT = 120  # 2 min soft limit per task
CELERY_TASK_DEFAULT_QUEUE = "default"
# Use solo pool on Windows (prefork/fork not supported); override via env for Linux prod
CELERY_WORKER_POOL = os.environ.get("CELERY_WORKER_POOL", "solo")