"""Small, local-first Django configuration. Secrets come from the environment."""

import os
import secrets
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
(BASE_DIR / "var").mkdir(exist_ok=True)
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY is required when DEBUG is off")
    SECRET_KEY = secrets.token_urlsafe(48)

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get(
        "DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]"
    ).split(",")
    if host.strip()
]
INSTALLED_APPS = ["django.contrib.staticfiles", "app"]
MIDDLEWARE = [
    "app.api.middleware.ApiBoundaryMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
FUEL_API_RATE_REQUESTS = int(os.environ.get("FUEL_API_RATE_REQUESTS", "120"))
FUEL_API_RATE_WINDOW_SECONDS = int(os.environ.get("FUEL_API_RATE_WINDOW_SECONDS", "60"))
FUEL_API_RATE_MAX_CALLERS = int(os.environ.get("FUEL_API_RATE_MAX_CALLERS", "10000"))
if (
    min(FUEL_API_RATE_REQUESTS, FUEL_API_RATE_WINDOW_SECONDS, FUEL_API_RATE_MAX_CALLERS)
    < 1
):
    raise ImproperlyConfigured("API rate bounds must be positive")
DATA_UPLOAD_MAX_MEMORY_SIZE = 16 * 1024
ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {},
    }
]
WSGI_APPLICATION = "config.wsgi.application"
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        # Waitress reuses a bounded set of worker threads. Retain their SQLite
        # connections briefly instead of repeating WAL setup for every POST/GET.
        "CONN_MAX_AGE": 60,
        "OPTIONS": {
            "transaction_mode": "IMMEDIATE",
            "timeout": 0.25,
            # Local disk/named-volume SQLite: readers may overlap durable writes.
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;",
        },
        "NAME": BASE_DIR / "var" / "app.sqlite3",
    }
}
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "var" / "static_root"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"operations": {"()": "app.api.observability.OperationalFormatter"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
        "operations": {"class": "logging.StreamHandler", "formatter": "operations"},
    },
    "loggers": {
        "app.api.operations": {
            "handlers": ["operations"],
            "level": "INFO",
            "propagate": False,
        },
        "app.planning.trip": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "app.planning.matrix_cache": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
