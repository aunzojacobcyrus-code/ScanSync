"""WSGI config for ScanSync."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scansync.settings")

application = get_wsgi_application()
