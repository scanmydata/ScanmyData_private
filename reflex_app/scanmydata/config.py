"""Shared configuration constants for the Reflex frontend."""
import os

# Flask backend base URL - can be overridden via environment variable
FLASK_API_BASE: str = os.getenv("SCANMYDATA_API_URL", "http://localhost:5000")
