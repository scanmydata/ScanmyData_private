"""Main Reflex app entry point for ScanmyData."""
import reflex as rx

# Import all pages — this registers their routes via @rx.page decorators
from . import pages  # noqa: F401

app = rx.App(
    theme=rx.theme(
        appearance="light",
        has_background=True,
        radius="medium",
        accent_color="sky",
    ),
    stylesheets=[
        "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap",
    ],
    style={
        "font_family": "Inter, system-ui, -apple-system, sans-serif",
    },
)
