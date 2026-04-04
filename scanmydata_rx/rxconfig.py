import os
import reflex as rx

_port     = os.getenv("PORT", "5001")
_api_url  = os.getenv("REFLEX_API_URL", f"http://localhost:{_port}")

config = rx.Config(
    app_name="scanmydata_rx",
    frontend_port=int(os.getenv("REFLEX_FRONTEND_PORT", "3000")),
    backend_port=int(os.getenv("REFLEX_BACKEND_PORT", "8001")),
    api_url=_api_url,
    deploy_url=os.getenv("REFLEX_DEPLOY_URL", _api_url),
    db_url="sqlite:///reflex.db",
    tailwind={},
)
