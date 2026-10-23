"""
Django settings for the ScanSync project.

ScanSync is a standalone, classroom-scale facial-recognition attendance tracker.
Configuration can be overridden with environment variables so the same code base
runs on SQLite (default), MySQL/MariaDB, or PostgreSQL.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


# --------------------------------------------------------------------------- #
# Core
# --------------------------------------------------------------------------- #
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-scansync-dev-key-change-me-before-deploying",
)
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = [h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "attendance.apps.AttendanceConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "scansync.middleware.SeparateAdminSessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "scansync.urls"

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
                "attendance.context_processors.scansync",
            ],
        },
    },
]

WSGI_APPLICATION = "scansync.wsgi.application"

# --------------------------------------------------------------------------- #
# SQL database
#   DB_ENGINE = sqlite (default) | mysql | postgresql
DB_ENGINE = os.environ.get("DB_ENGINE", "mysql").lower()

if DB_ENGINE == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "scansync.sqlite3",
        }
    }
else:
    DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": "scansync",
        "USER": "root",           
        "PASSWORD": "1234",           
        "HOST": "127.0.0.1",
        "PORT": "3306",
        "OPTIONS": {
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
            "charset": "utf8mb4",
        },
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "attendance:dashboard"
LOGOUT_REDIRECT_URL = "login"

# --------------------------------------------------------------------------- #
# Internationalisation — Bago City, Negros Occidental
# --------------------------------------------------------------------------- #
LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "Asia/Manila")
USE_I18N = True
USE_TZ = True

# --------------------------------------------------------------------------- #
# Static & media (face images live in MEDIA_ROOT/faces/)
# --------------------------------------------------------------------------- #
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Registration posts several base64 JPEG frames in one JSON body.
DATA_UPLOAD_MAX_MEMORY_SIZE = 15 * 1024 * 1024

# --------------------------------------------------------------------------- #
# ScanSync recognition settings
# --------------------------------------------------------------------------- #
SCANSYNC = {
    # Max Euclidean distance between 128-d face descriptors to count as a match.
    # 0.6 is face-api's default; 0.5 is stricter and reduces false positives.
    "MATCH_THRESHOLD": float(os.environ.get("SCANSYNC_MATCH_THRESHOLD", 0.5)),
    # Number of face samples required when registering a student.
    "MIN_FACE_SAMPLES": 4,
    "MAX_FACE_SAMPLES": 4,
    # Where the browser loads face-api.js + model weights from. Point these at
    # /static/vendor/... to run fully offline (see README).
    "FACE_API_JS": os.environ.get(
        "SCANSYNC_FACE_API_JS",
        "https://cdn.jsdelivr.net/npm/@vladmandic/face-api@1.7.15/dist/face-api.js",
    ),
    "FACE_MODEL_URL": os.environ.get(
        "SCANSYNC_FACE_MODEL_URL",
        "https://cdn.jsdelivr.net/npm/@vladmandic/face-api@1.7.15/model/",
    ),
}

# Webcams need a secure context: http://localhost works; LAN IPs need HTTPS.
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_NAME = "sessionid"
CSRF_USE_SESSIONS = True
CSRF_FAILURE_VIEW = "scansync.middleware.csrf_failure"

