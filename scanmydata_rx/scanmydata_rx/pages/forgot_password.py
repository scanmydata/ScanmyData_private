"""Forgot password page."""
import os
import httpx
import reflex as rx

from ..components.layout import card, layout

FLASK_BASE = os.getenv("FLASK_API_URL", f"http://localhost:{os.getenv('PORT', '5001')}")


class ForgotPasswordState(rx.State):
    email: str = ""
    loading: bool = False
    error: str = ""
    success: str = ""

    async def send_reset(self):
        self.error = ""
        self.success = ""
        if not self.email:
            self.error = "Εισάγετε email."
            return
        self.loading = True
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{FLASK_BASE}/firebase-auth/forgot-password",
                    data={"email": self.email},
                )
                if resp.status_code == 200:
                    self.success = "Εάν το email υπάρχει, θα λάβετε οδηγίες επαναφοράς κωδικού."
                else:
                    data = resp.json() if "json" in resp.headers.get("content-type", "") else {}
                    self.error = data.get("error", "Σφάλμα αποστολής email.")
        except Exception as e:
            self.error = f"Σφάλμα: {e}"
        finally:
            self.loading = False


@rx.page(route="/firebase-auth/forgot-password", title="Επαναφορά Κωδικού - ScanmyData")
def forgot_password_page() -> rx.Component:
    return layout(
        rx.center(
            card(
                rx.vstack(
                    rx.heading("🔑 Επαναφορά Κωδικού", size="5", color=rx.color_mode_cond("#1f2937", "#f9fafb")),
                    rx.text(
                        "Εισάγετε το email σας και θα σας στείλουμε οδηγίες επαναφοράς κωδικού.",
                        font_size="14px",
                        color=rx.color_mode_cond("#6b7280", "#9ca3af"),
                    ),
                    rx.cond(
                        ForgotPasswordState.error != "",
                        rx.box(ForgotPasswordState.error, background="#fee2e2", color="#7f1d1d", padding="10px", border_radius="8px", width="100%", font_size="14px"),
                        rx.fragment(),
                    ),
                    rx.cond(
                        ForgotPasswordState.success != "",
                        rx.box(ForgotPasswordState.success, background="#ecfdf5", color="#065f46", padding="10px", border_radius="8px", width="100%", font_size="14px"),
                        rx.fragment(),
                    ),
                    rx.vstack(
                        rx.text("Email", font_size="14px", font_weight="500"),
                        rx.input(
                            placeholder="your@email.com",
                            type="email",
                            value=ForgotPasswordState.email,
                            on_change=ForgotPasswordState.set_email,
                            width="100%",
                            border_radius="8px",
                        ),
                        width="100%",
                        spacing="1",
                    ),
                    rx.button(
                        rx.cond(
                            ForgotPasswordState.loading,
                            rx.hstack(rx.spinner(size="2"), rx.text("Αποστολή..."), spacing="2"),
                            rx.text("📧 Αποστολή Οδηγιών"),
                        ),
                        on_click=ForgotPasswordState.send_reset,
                        background_color="#0284c7",
                        color="white",
                        width="100%",
                        border_radius="8px",
                        font_weight="600",
                        padding="10px",
                        disabled=ForgotPasswordState.loading,
                    ),
                    rx.link("← Πίσω στη Σύνδεση", href="/firebase-auth/login", font_size="13px", color="#0284c7"),
                    spacing="4",
                    width="100%",
                ),
                width="400px",
                max_width="95vw",
            ),
            padding_top="60px",
        )
    )
