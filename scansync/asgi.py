"""ASGI config for ScanSync."""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scansync.settings")

application = get_asgi_application()
