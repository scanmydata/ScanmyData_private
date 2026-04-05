import reflex as rx
from . import pages  # noqa: F401 – registers all pages

app = rx.App(
    stylesheets=[
        "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap",
    ],
)
