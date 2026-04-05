import reflex as rx

# NOTE: api_url is Reflex's own WebSocket/backend URL (NOT the Flask REST API).
# The Flask REST API URL is configured in reflex_app/scanmydata/config.py
# and can be overridden via the SCANMYDATA_API_URL environment variable.
config = rx.Config(
    app_name="scanmydata",
    frontend_port=3000,
    backend_port=8000,
)
