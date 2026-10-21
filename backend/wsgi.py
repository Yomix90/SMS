"""Point d'entrée WSGI.

Production : gunicorn -k gevent -w 1 --bind 0.0.0.0:8000 wsgi:app
(un seul worker : Socket.IO et le scheduler vivent dans ce process ; le worker gevent
de gunicorn applique lui-même le monkey-patching avant de charger l'application)
Développement : python wsgi.py
"""
import os

from app import create_app
from app.extensions import socketio

app = create_app()

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=app.debug,
                 use_reloader=False, allow_unsafe_werkzeug=True)
