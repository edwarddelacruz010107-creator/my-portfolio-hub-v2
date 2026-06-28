"""
wsgi.py — Gunicorn / uWSGI entry point for production.

Usage:
  gunicorn wsgi:app
  gunicorn --bind 0.0.0.0:${PORT:-8000} --workers 2 --threads 2 wsgi:app

NOTE: ProxyFix is applied in app/__init__.py create_app() and does not need
to be applied again here. Applying it twice would cause incorrect header
parsing and security logging issues.
"""
import os
from app import create_app

app = create_app(os.environ.get('FLASK_ENV', 'production'))
