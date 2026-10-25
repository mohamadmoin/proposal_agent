"""Settings for the test suite: SQLite, the fake model and offline PubMed. No network, no keys."""

import os

os.environ.setdefault("DJANGO_DEBUG", "1")
os.environ["LLM_PROVIDER"] = "fake"
os.environ["PUBMED_OFFLINE"] = "1"

from config.settings import *  # noqa: E402,F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
API_TOKENS = ["test-token"]
WEB_ACCESS_CODE = ""
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
# WhiteNoise is not needed in tests (no collected static files).
MIDDLEWARE = [m for m in MIDDLEWARE if "whitenoise" not in m]  # noqa: F405
