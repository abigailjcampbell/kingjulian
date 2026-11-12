import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "core",
    "properties",
    "contacts",
    "inventory",
    "maintenance",
    "planner",
    "attachments",
    "audit.apps.AuditConfig",
    "exports",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "attachments.middleware.UploadSizeMiddleware",     # must precede CsrfViewMiddleware
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Must come after authentication: resolves the active account and sets the tenant context.
    "core.middleware.ActiveAccountMiddleware",
    "audit.middleware.ActorMiddleware",                 # who is acting, for the activity log
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

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
            ],
        },
    },
]

DATABASES = {
    "default": dj_database_url.config(
        default="postgres://propmgr:propmgr@localhost:5432/propmgr",
        conn_max_age=60,
    )
}

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Email: console in dev; set EMAIL_BACKEND/EMAIL_HOST/... in production.
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "Property Manager <noreply@localhost>")
SITE_URL = os.environ.get("SITE_URL", "http://localhost:8000")   # used for links inside emails

# Uploaded files live outside any web-served path; they are only ever sent by the authenticated download views.
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", BASE_DIR / "media"))
ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024            # per file
ATTACHMENT_ACCOUNT_QUOTA_BYTES = 500 * 1024 * 1024  # per account
ATTACHMENT_MAX_PER_TARGET = 50                      # files per property/item/task/...
FILE_UPLOAD_PERMISSIONS = 0o640                    # stored files: owner read/write, group read, nobody else
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o750
