"""ScanSync project package."""
from django.db.backends.base.base import BaseDatabaseWrapper

# Allow MySQL 8.0.x LTS (Django 6 defaults to 8.4+)
BaseDatabaseWrapper.check_database_version_supported = lambda self: None
