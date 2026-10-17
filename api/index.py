import sys
import os

# Add root directory to sys.path so server and ai_service can be imported
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from server import app

# On Vercel, api/index.py becomes a serverless function at /api/index.
# The FastAPI app defines routes under /api/* (e.g. /api/ask).
# Vercel passes the original request path to the WSGI/ASGI handler,
# so FastAPI's /api/ask routes will match the forwarded path.
app = app
