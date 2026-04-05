import reflex as rx

config = rx.Config(
    app_name="scanmydata",
    api_url="http://localhost:5000",  # Flask backend URL
    frontend_port=3000,
    backend_port=8000,
    loglevel="debug",
)
