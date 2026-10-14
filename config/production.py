"""Explicit production settings; TLS terminates at the configured reverse proxy."""

import os

from django.core.exceptions import ImproperlyConfigured

# Do not silently inherit local development settings.
if os.environ.get("DJANGO_DEBUG") != "0":
    raise ImproperlyConfigured("Production requires DJANGO_DEBUG=0")
if not os.environ.get("DJANGO_ALLOWED_HOSTS", "").strip():
    raise ImproperlyConfigured("Production requires explicit DJANGO_ALLOWED_HOSTS")

from config.settings import *  # noqa: E402,F403

if "*" in ALLOWED_HOSTS:  # noqa: F405
    raise ImproperlyConfigured("Production does not allow wildcard hosts")
if len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 5:  # noqa: F405
    raise ImproperlyConfigured("Production requires a strong persistent secret")

SECURE_SSL_REDIRECT = True
SECURE_REDIRECT_EXEMPT = [r"^health/(live|ready)$"]
SECURE_HSTS_SECONDS = 3600
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
# Fail storage contention before Django's default five-second wait consumes
# the finalization margin after a long plan.
DATABASES["default"]["OPTIONS"]["timeout"] = 0.25  # noqa: F405
# Domain-wide HTTPS/preload enrollment require knowledge of the actual domain.
# Keep those deployment choices explicit rather than enabling them blindly.
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]
# Waitress trusts only the configured proxy and normalizes wsgi.url_scheme.
# Do not let Django independently trust arbitrary X-Forwarded-* headers.
SECURE_PROXY_SSL_HEADER = None
USE_X_FORWARDED_HOST = False
