"""Sign-up page."""
import os
import httpx
import reflex as rx

from ..components.layout import card, layout

FLASK_BASE = os.getenv("FLASK_API_URL", f"http://localhost:{os.getenv('PORT', '5001')}")


class SignupState(rx.State):
    email: str = ""
    password: str = ""
    confirm_password: str = ""
    display_name: str = ""
    loading: bool = False
    error: str = ""
    success: str = ""

    async def do_signup(self):
        self.error = ""
        self.success = ""
        if self.password != self.confirm_password:
            self.error = "Οι κωδικοί δεν ταιριάζουν."
            return
        if len(self.password) < 6:
            self.error = "Ο κωδικός πρέπει να έχει τουλάχιστον 6 χαρακτήρες."
            return
        self.loading = True
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{FLASK_BASE}/firebase-auth/signup",
                    data={
                        "email": self.email,
                        "password": self.password,
                        "display_name": self.display_name,
                    },
                    follow_redirects=False,
                )
                if resp.status_code in (200, 302):
                    self.success = "Ο λογαριασμός δημιουργήθηκε! Ελέγξτε το email σας για επαλήθευση."
                else:
                    data = resp.json() if "json" in resp.headers.get("content-type", "") else {}
                    self.error = data.get("error", "Σφάλμα δημιουργίας λογαριασμού.")
        except Exception as e:
            self.error = f"Σφάλμα: {e}"
        finally:
            self.loading = False


@rx.page(route="/firebase-auth/signup", title="Εγγραφή - ScanmyData")
def signup_page() -> rx.Component:
    return layout(
        rx.center(
            card(
                rx.vstack(
                    rx.heading("📝 Εγγραφή", size="5", color=rx.color_mode_cond("#1f2937", "#f9fafb")),
                    rx.cond(
                        SignupState.error != "",
                        rx.box(SignupState.error, background="#fee2e2", color="#7f1d1d", padding="10px", border_radius="8px", width="100%", font_size="14px"),
                        rx.fragment(),
                    ),
                    rx.cond(
                        SignupState.success != "",
                        rx.box(SignupState.success, background="#ecfdf5", color="#065f46", padding="10px", border_radius="8px", width="100%", font_size="14px"),
                        rx.fragment(),
                    ),
                    rx.vstack(
                        rx.text("Όνομα Εμφάνισης", font_size="14px", font_weight="500"),
                        rx.input(placeholder="Το όνομά σας", value=SignupState.display_name, on_change=SignupState.set_display_name, width="100%", border_radius="8px"),
                        width="100%", spacing="1",
                    ),
                    rx.vstack(
                        rx.text("Email", font_size="14px", font_weight="500"),
                        rx.input(placeholder="your@email.com", type="email", value=SignupState.email, on_change=SignupState.set_email, width="100%", border_radius="8px"),
                        width="100%", spacing="1",
                    ),
                    rx.vstack(
                        rx.text("Κωδικός", font_size="14px", font_weight="500"),
                        rx.input(placeholder="Τουλάχιστον 6 χαρακτήρες", type="password", value=SignupState.password, on_change=SignupState.set_password, width="100%", border_radius="8px"),
                        width="100%", spacing="1",
                    ),
                    rx.vstack(
                        rx.text("Επιβεβαίωση Κωδικού", font_size="14px", font_weight="500"),
                        rx.input(placeholder="Επαναλάβετε τον κωδικό", type="password", value=SignupState.confirm_password, on_change=SignupState.set_confirm_password, width="100%", border_radius="8px"),
                        width="100%", spacing="1",
                    ),
                    rx.button(
                        rx.cond(SignupState.loading, rx.hstack(rx.spinner(size="2"), rx.text("Δημιουργία..."), spacing="2"), rx.text("📝 Δημιουργία Λογαριασμού")),
                        on_click=SignupState.do_signup,
                        background_color="#0284c7",
                        color="white",
                        width="100%",
                        border_radius="8px",
                        font_weight="600",
                        padding="10px",
                        disabled=SignupState.loading,
                    ),
                    rx.hstack(
                        rx.text("Έχετε ήδη λογαριασμό;", font_size="13px"),
                        rx.link("Σύνδεση", href="/firebase-auth/login", font_size="13px", color="#0284c7"),
                        spacing="2",
                        justify="center",
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
