"""Login page."""
import os
import httpx
import reflex as rx

from ..components.layout import card, layout
from ..state import GlobalState

FLASK_BASE = os.getenv("FLASK_API_URL", f"http://localhost:{os.getenv('PORT', '5001')}")


class LoginState(GlobalState):
    email: str = ""
    password: str = ""
    remember: bool = False
    login_loading: bool = False
    login_error: str = ""

    async def do_login(self):
        self.login_loading = True
        self.login_error = ""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{FLASK_BASE}/firebase-auth/login",
                    data={
                        "email": self.email,
                        "password": self.password,
                        "remember": "on" if self.remember else "",
                    },
                    follow_redirects=False,
                )
                if resp.status_code in (200, 302):
                    await self.check_auth()
                    if self.is_authenticated:
                        return rx.redirect("/")
                    self.login_error = "Σφάλμα σύνδεσης. Ελέγξτε τα στοιχεία σας."
                else:
                    body = resp.json() if "application/json" in resp.headers.get("content-type", "") else {}
                    self.login_error = body.get("error", "Σφάλμα σύνδεσης.")
        except Exception as e:
            self.login_error = f"Σφάλμα σύνδεσης: {e}"
        finally:
            self.login_loading = False


@rx.page(route="/firebase-auth/login", title="Σύνδεση - ScanmyData")
def login_page() -> rx.Component:
    return layout(
        rx.center(
            card(
                rx.vstack(
                    rx.heading("🔐 Σύνδεση", size="5", color=rx.color_mode_cond("#1f2937", "#f9fafb")),
                    rx.cond(
                        LoginState.login_error != "",
                        rx.box(
                            LoginState.login_error,
                            background="#fee2e2",
                            color="#7f1d1d",
                            padding="10px 14px",
                            border_radius="8px",
                            width="100%",
                            font_size="14px",
                        ),
                        rx.fragment(),
                    ),
                    rx.vstack(
                        rx.text("Email", font_size="14px", font_weight="500", color=rx.color_mode_cond("#374151", "#d1d5db")),
                        rx.input(
                            placeholder="your@email.com",
                            type="email",
                            value=LoginState.email,
                            on_change=LoginState.set_email,
                            width="100%",
                            border_radius="8px",
                        ),
                        width="100%",
                        spacing="1",
                    ),
                    rx.vstack(
                        rx.text("Κωδικός", font_size="14px", font_weight="500", color=rx.color_mode_cond("#374151", "#d1d5db")),
                        rx.input(
                            placeholder="Εισάγετε κωδικό",
                            type="password",
                            value=LoginState.password,
                            on_change=LoginState.set_password,
                            width="100%",
                            border_radius="8px",
                        ),
                        width="100%",
                        spacing="1",
                    ),
                    rx.hstack(
                        rx.checkbox(
                            "Να με θυμάσαι",
                            checked=LoginState.remember,
                            on_change=LoginState.set_remember,
                            font_size="14px",
                        ),
                        width="100%",
                    ),
                    rx.button(
                        rx.cond(
                            LoginState.login_loading,
                            rx.hstack(rx.spinner(size="2"), rx.text("Σύνδεση..."), spacing="2"),
                            rx.text("🔐 Σύνδεση"),
                        ),
                        on_click=LoginState.do_login,
                        background_color="#0284c7",
                        color="white",
                        width="100%",
                        border_radius="8px",
                        font_weight="600",
                        padding="10px",
                        disabled=LoginState.login_loading,
                    ),
                    rx.hstack(
                        rx.link("Ξέχασα τον κωδικό", href="/firebase-auth/forgot-password", font_size="13px", color="#0284c7"),
                        rx.spacer(),
                        rx.link("Εγγραφή", href="/firebase-auth/signup", font_size="13px", color="#0284c7"),
                        width="100%",
                    ),
                    rx.divider(),
                    rx.box(
                        rx.hstack(
                            rx.text("🛡️", font_size="16px"),
                            rx.vstack(
                                rx.text("Ασφαλής Σύνδεση", font_weight="600", font_size="13px"),
                                rx.text("Χρήση Firebase authentication.", font_size="12px", color=rx.color_mode_cond("#6b7280", "#9ca3af")),
                                spacing="0",
                                align="start",
                            ),
                            spacing="2",
                            align="start",
                        ),
                        background="#e0f2fe",
                        color="#075985",
                        padding="12px",
                        border_radius="8px",
                        width="100%",
                    ),
                    spacing="4",
                    width="100%",
                ),
                width="420px",
                max_width="95vw",
            ),
            padding_top="60px",
        )
    )
