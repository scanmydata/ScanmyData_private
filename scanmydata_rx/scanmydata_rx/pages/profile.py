"""User profile page."""
import httpx
import reflex as rx

from ..components.layout import card, layout
from ..state import GlobalState

FLASK_BASE = "http://localhost:5000"


class ProfileState(GlobalState):
    display_name: str = ""
    profile_loading: bool = False
    profile_error: str = ""
    profile_success: str = ""

    async def load_profile(self):
        await self.check_auth()
        if not self.is_authenticated:
            return rx.redirect("/firebase-auth/login")
        self.display_name = self.username

    async def save_profile(self):
        self.profile_error = ""
        self.profile_success = ""
        self.profile_loading = True
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{FLASK_BASE}/firebase-auth/profile",
                    data={"display_name": self.display_name},
                )
                if resp.status_code == 200:
                    self.profile_success = "Το προφίλ ενημερώθηκε."
                    self.username = self.display_name
                else:
                    self.profile_error = "Σφάλμα αποθήκευσης προφίλ."
        except Exception as e:
            self.profile_error = f"Σφάλμα: {e}"
        finally:
            self.profile_loading = False


@rx.page(route="/firebase-auth/profile", title="Προφίλ - ScanmyData", on_load=ProfileState.load_profile)
def profile_page() -> rx.Component:
    return layout(
        rx.center(
            card(
                rx.vstack(
                    rx.heading("👤 Προφίλ Χρήστη", size="5", color=rx.color_mode_cond("#1f2937", "#f9fafb")),
                    rx.cond(
                        ProfileState.profile_error != "",
                        rx.box(ProfileState.profile_error, background="#fee2e2", color="#7f1d1d", padding="10px", border_radius="8px", width="100%", font_size="14px"),
                        rx.fragment(),
                    ),
                    rx.cond(
                        ProfileState.profile_success != "",
                        rx.box(ProfileState.profile_success, background="#ecfdf5", color="#065f46", padding="10px", border_radius="8px", width="100%", font_size="14px"),
                        rx.fragment(),
                    ),
                    rx.vstack(
                        rx.text("Email", font_size="14px", font_weight="500", color=rx.color_mode_cond("#374151", "#d1d5db")),
                        rx.input(
                            value=ProfileState.user_email,
                            disabled=True,
                            width="100%",
                            border_radius="8px",
                            background_color=rx.color_mode_cond("#f9fafb", "#374151"),
                        ),
                        width="100%",
                        spacing="1",
                    ),
                    rx.vstack(
                        rx.text("Όνομα Εμφάνισης", font_size="14px", font_weight="500", color=rx.color_mode_cond("#374151", "#d1d5db")),
                        rx.input(
                            value=ProfileState.display_name,
                            on_change=ProfileState.set_display_name,
                            width="100%",
                            border_radius="8px",
                        ),
                        width="100%",
                        spacing="1",
                    ),
                    rx.button(
                        rx.cond(ProfileState.profile_loading, rx.hstack(rx.spinner(size="2"), rx.text("Αποθήκευση..."), spacing="2"), rx.text("💾 Αποθήκευση")),
                        on_click=ProfileState.save_profile,
                        background_color="#0284c7",
                        color="white",
                        width="100%",
                        border_radius="8px",
                        font_weight="600",
                        padding="10px",
                        disabled=ProfileState.profile_loading,
                    ),
                    rx.divider(),
                    rx.link("🔑 Αλλαγή Κωδικού", href="/firebase-auth/forgot-password", font_size="14px", color="#0284c7"),
                    spacing="4",
                    width="100%",
                ),
                width="440px",
                max_width="95vw",
            ),
            padding_top="40px",
        )
    )
