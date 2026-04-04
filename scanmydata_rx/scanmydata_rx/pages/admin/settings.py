"""Admin settings page."""
import os
import httpx
import reflex as rx

from ...components.layout import card, layout
from ...state import GlobalState

FLASK_BASE = os.getenv("FLASK_API_URL", f"http://localhost:{os.getenv('PORT', '5001')}")


class AdminSettingsState(GlobalState):
    settings: dict = {}
    s_loading: bool = False
    s_error: str = ""
    s_success: str = ""

    async def on_load(self):
        await self.check_auth()
        if not self.is_authenticated or not self.is_admin:
            return rx.redirect("/")
        await self.load_settings()

    async def load_settings(self):
        self.s_loading = True
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{FLASK_BASE}/admin/settings")
                if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                    self.settings = resp.json()
        except Exception as e:
            self.s_error = f"Σφάλμα: {e}"
        finally:
            self.s_loading = False

    async def save_settings(self):
        self.s_error = ""
        self.s_success = ""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{FLASK_BASE}/admin/settings/save", json=self.settings)
                if resp.status_code == 200:
                    self.s_success = "Οι ρυθμίσεις αποθηκεύτηκαν."
                else:
                    self.s_error = "Σφάλμα αποθήκευσης."
        except Exception as e:
            self.s_error = f"Σφάλμα: {e}"


@rx.page(route="/admin/settings", title="Admin Ρυθμίσεις - ScanmyData", on_load=AdminSettingsState.on_load)
def admin_settings_page() -> rx.Component:
    return layout(
        rx.vstack(
            rx.hstack(
                rx.link("← Dashboard", href="/admin/dashboard", font_size="13px", color="#0284c7"),
                rx.heading("⚙️ Ρυθμίσεις Συστήματος", size="5", color=rx.color_mode_cond("#1f2937", "#f9fafb")),
                spacing="4",
                align="center",
                flex_wrap="wrap",
                width="100%",
            ),
            rx.cond(AdminSettingsState.s_error != "", rx.box(AdminSettingsState.s_error, background="#fee2e2", color="#7f1d1d", padding="10px", border_radius="8px", width="100%", font_size="14px"), rx.fragment()),
            rx.cond(AdminSettingsState.s_success != "", rx.box(AdminSettingsState.s_success, background="#ecfdf5", color="#065f46", padding="10px", border_radius="8px", width="100%", font_size="14px"), rx.fragment()),
            rx.cond(
                AdminSettingsState.s_loading,
                rx.center(rx.spinner(size="3"), padding="40px"),
                card(
                    rx.vstack(
                        rx.text("Για προχωρημένες ρυθμίσεις, μεταβείτε στο Flask backend.", font_size="14px", color=rx.color_mode_cond("#6b7280", "#9ca3af")),
                        rx.hstack(
                            rx.link(rx.button("🔧 Flask Admin Settings", background_color="#0284c7", color="white", border_radius="8px"), href=f"{FLASK_BASE}/admin/settings", is_external=True),
                            rx.link(rx.button("📊 Dashboard", variant="soft", border_radius="8px"), href="/admin/dashboard"),
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
