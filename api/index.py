"""Vercel serverless entry point.

Vercel's @vercel/python runtime looks for a WSGI `app` object in this file and
serves it. We reuse the Flask app defined in webapp.py (at the repo root).
"""
import os
import sys

# Make the repo root importable so `webapp`, `config`, and `lib` resolve.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webapp import app  # noqa: E402  (WSGI app Vercel will serve)
