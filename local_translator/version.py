"""
version.py — single source of truth for APP_VERSION

Bump this by hand when you build a release worth telling apart from the
last one — nothing else in the app derives or auto-increments it.

No third-party imports — imported by app.py, safe for every build target.
"""

APP_VERSION = "0.1.0"
