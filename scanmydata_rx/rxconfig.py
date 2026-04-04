import reflex as rx
from reflex.plugins import SitemapPlugin

config = rx.Config(
    app_name="scanmydata_rx",
    frontend_port=3000,
    backend_port=8000,
    api_url="http://localhost:5000",
    db_url="sqlite:///reflex.db",
    tailwind={},
    plugins=[SitemapPlugin()],
)
