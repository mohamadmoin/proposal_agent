"""Django settings. Every value comes from the environment (see `.env.example`).

Why everything is an environment variable: the same Docker image runs on a laptop, in CI and on a server; only the
environment changes. No secret is ever written in this file.
"""

import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent


def env_str(name: str, default: str = "") -> str:
    """Read a string variable; an empty value counts as missing."""
    value = os.environ.get(name, "").strip()
    return value or default


def env_bool(name: str, default: bool = False) -> bool:
    """Read a yes/no variable: 1, true, yes and on mean yes."""
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "", sep: str = ",") -> list[str]:
    """Read a list variable such as `a.com,b.com`."""
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(sep) if item.strip()]


def env_int(name: str, default: int) -> int:
    """Read a whole-number variable."""
    value = os.environ.get(name, "").strip()
    return int(value) if value else default


DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = env_str("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "insecure-development-key-do-not-use-in-production"
    else:
        raise RuntimeError("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is off.")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", "")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "proposals",
]

# No XFrameOptionsMiddleware: framing is controlled by the CSP `frame-ancestors` header instead
# (proposals.middleware.FrameAncestorsMiddleware), so an allowed platform can embed the app.
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "proposals.middleware.FrameAncestorsMiddleware",
    "proposals.middleware.AccessCodeMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": dj_database_url.parse(
        env_str("DATABASE_URL", f"sqlite:///{BASE_DIR / 'var' / 'db.sqlite3'}"),
        conn_max_age=60,
    )
}
if DATABASES["default"]["ENGINE"] == "django.db.backends.mysql":
    # utf8mb4 so Persian text and every emoji-like character are stored safely.
    DATABASES["default"].setdefault("OPTIONS", {})["charset"] = "utf8mb4"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

# Uploaded StatPearls pages may be up to 3 MB; leave room for the rest of the form.
DATA_UPLOAD_MAX_MEMORY_SIZE = 4 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 4 * 1024 * 1024

# --- App settings -------------------------------------------------------------------------------------------------
API_TOKENS = env_list("API_TOKENS", "")


def normalise_frame_ancestors(raw: str) -> str:
    """Put the quotes back on CSP keywords: env-file parsers often strip them ('self' → self)."""
    keywords = {"self", "none"}
    parts = [f"'{p}'" if p.strip("'") in keywords else p for p in raw.split()]
    return " ".join(parts) or "'self'"


FRAME_ANCESTORS = normalise_frame_ancestors(env_str("FRAME_ANCESTORS", "'self'"))
WEB_ACCESS_CODE = env_str("WEB_ACCESS_CODE", "")

# When another site embeds the app in an iframe, the browser only sends our cookies (session, CSRF) if they are
# marked SameSite=None; Secure. That needs HTTPS, so it is switched on only when other origins are allowed.
if any(origin not in {"'self'", "'none'"} for origin in FRAME_ANCESTORS.split()):
    SESSION_COOKIE_SAMESITE = "None"
    CSRF_COOKIE_SAMESITE = "None"
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

if env_bool("DJANGO_BEHIND_HTTPS_PROXY", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env_str("LOG_LEVEL", "INFO")},
    "loggers": {"httpx": {"level": "WARNING"}, "openai": {"level": "WARNING"}},
}
