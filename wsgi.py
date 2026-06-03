"""
PythonAnywhere WSGI entry point for Momentum Builder.

PythonAnywhere looks for a variable named `application` in this file.

After uploading all files to /home/YOUR_USERNAME/momentum_builder/,
paste the following into your PythonAnywhere WSGI config file
(Web tab → WSGI configuration file → Edit):

    import sys, os
    path = '/home/YOUR_USERNAME/momentum_builder'
    if path not in sys.path:
        sys.path.insert(0, path)
    from wsgi import application

"""

import sys
import os

# ── Add app directory to Python path ────────────────────────────────
APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

# ── Import Flask app ─────────────────────────────────────────────────
from app import app as application  # noqa: F401  (PythonAnywhere expects `application`)
