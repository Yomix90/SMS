"""Raccourci CLI sans scheduler : python manage.py create-admin, db upgrade, seed-demo…"""
import os
import sys

os.environ.setdefault("SMS_NO_SCHEDULER", "1")
os.environ.setdefault("FLASK_APP", "wsgi:app")

from flask.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
