"""Options / settings page."""
import httpx
import reflex as rx

from ..components.layout import card, layout
from ..state import GlobalState

FLASK_BASE = "http://localhost:5000"


class OptionsState(GlobalState):
    settings: dict = {}
    opts_loading: bool = False
    opts_error: str = ""
    opts_success: str = ""

    async def on_load(self):
        await self.check_auth()
        if not self.is_authenticated:
            return rx.redirect("/firebase-auth/login")
        await self.load_settings()

    async def load_settings(self):
        self.opts_loading = True
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{FLASK_BASE}/credentials/get_settings")
                if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                    self.settings = resp.json()
        except Exception as e:
            self.opts_error = f"Σφάλμα: {e}"
        finally:
            self.opts_loading = False

    async def save_settings(self):
        self.opts_error = ""
        self.opts_success = ""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{FLASK_BASE}/credentials/save_settings", json=self.settings)
                if resp.status_code == 200:
                    self.opts_success = "Οι ρυθμίσεις αποθηκεύτηκαν."
                else:
                    self.opts_error = "Σφάλμα αποθήκευσης."
        except Exception as e:
            self.opts_error = f"Σφάλμα: {e}"


@rx.page(route="/options", title="Ρυθμίσεις - ScanmyData", on_load=OptionsState.on_load)
def options_page() -> rx.Component:
    return layout(
        rx.vstack(
            rx.heading("⚙️ Ρυθμίσεις", size="5", color=rx.color_mode_cond("#1f2937", "#f9fafb")),
            rx.cond(OptionsState.opts_error != "", rx.box(OptionsState.opts_error, background="#fee2e2", color="#7f1d1d", padding="10px", border_radius="8px", width="100%", font_size="14px"), rx.fragment()),
            rx.cond(OptionsState.opts_success != "", rx.box(OptionsState.opts_success, background="#ecfdf5", color="#065f46", padding="10px", border_radius="8px", width="100%", font_size="14px"), rx.fragment()),
            rx.cond(
                OptionsState.opts_loading,
                rx.center(rx.spinner(size="3"), padding="40px"),
                card(
                    rx.vstack(
                        rx.text("Οι ρυθμίσεις φορτώθηκαν από τον Flask server.", font_size="14px", color=rx.color_mode_cond("#6b7280", "#9ca3af")),
                        rx.text("Για προχωρημένες ρυθμίσεις, επισκεφθείτε τη σελίδα Credentials.", font_size="14px", color=rx.color_mode_cond("#6b7280", "#9ca3af")),
                        rx.hstack(
                            rx.link(rx.button("🔐 Credentials", background_color="#0284c7", color="white", border_radius="8px"), href="/credentials"),
                            rx.link(rx.button("👤 Προφίλ", variant="soft", border_radius="8px"), href="/firebase-auth/profile"),
                            spacing="3",
                        ),
                        spacing="4",
                        width="100%",
                    ),
                    width="100%",
                ),
            ),
            width="100%",
            spacing="4",
        )
    )
