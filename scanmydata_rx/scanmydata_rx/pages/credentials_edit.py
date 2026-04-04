"""Credentials edit page."""
import httpx
import reflex as rx

from ..components.layout import card, layout
from ..state import GlobalState

FLASK_BASE = "http://localhost:5000"


class CredentialsEditState(GlobalState):
    cred_name: str = ""
    cred_username: str = ""
    cred_password: str = ""
    cred_vat: str = ""
    cred_loaded: bool = False
    edit_loading: bool = False
    edit_error: str = ""
    edit_success: str = ""

    async def on_load(self):
        await self.check_auth()
        if not self.is_authenticated:
            return rx.redirect("/firebase-auth/login")

    async def load_credential(self, name: str):
        self.cred_name = name
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{FLASK_BASE}/credentials/edit/{name}")
                if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                    data = resp.json()
                    cred = data.get("credential", {})
                    self.cred_username = cred.get("username", "")
                    self.cred_vat = cred.get("vat", "")
                    self.cred_password = ""
                    self.cred_loaded = True
        except Exception as e:
            self.edit_error = f"Σφάλμα φόρτωσης: {e}"

    async def save_credential(self):
        self.edit_error = ""
        self.edit_success = ""
        self.edit_loading = True
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                data = {
                    "username": self.cred_username,
                    "vat": self.cred_vat,
                }
                if self.cred_password:
                    data["password"] = self.cred_password
                resp = await client.post(
                    f"{FLASK_BASE}/credentials/edit/{self.cred_name}",
                    data=data,
                )
                if resp.status_code == 200:
                    self.edit_success = "Το credential αποθηκεύτηκε."
                else:
                    self.edit_error = "Σφάλμα αποθήκευσης."
        except Exception as e:
            self.edit_error = f"Σφάλμα: {e}"
        finally:
            self.edit_loading = False


@rx.page(route="/credentials/edit/[name]", title="Επεξεργασία Credential", on_load=CredentialsEditState.on_load)
def credentials_edit_page() -> rx.Component:
    return layout(
        rx.center(
            card(
                rx.vstack(
                    rx.hstack(
                        rx.link("← Πίσω", href="/credentials", font_size="13px", color="#0284c7"),
                        rx.heading(f"✏️ Επεξεργασία: {CredentialsEditState.cred_name}", size="4", color=rx.color_mode_cond("#1f2937", "#f9fafb")),
                        spacing="4",
                        align="center",
                        flex_wrap="wrap",
                    ),
                    rx.cond(CredentialsEditState.edit_error != "", rx.box(CredentialsEditState.edit_error, background="#fee2e2", color="#7f1d1d", padding="10px", border_radius="8px", width="100%", font_size="14px"), rx.fragment()),
                    rx.cond(CredentialsEditState.edit_success != "", rx.box(CredentialsEditState.edit_success, background="#ecfdf5", color="#065f46", padding="10px", border_radius="8px", width="100%", font_size="14px"), rx.fragment()),
                    rx.vstack(rx.text("Username", font_size="14px", font_weight="500"), rx.input(value=CredentialsEditState.cred_username, on_change=CredentialsEditState.set_cred_username, width="100%", border_radius="8px"), width="100%", spacing="1"),
                    rx.vstack(rx.text("Νέος Κωδικός (αφήστε κενό για αμετάβλητο)", font_size="14px", font_weight="500"), rx.input(type="password", placeholder="Νέος κωδικός...", value=CredentialsEditState.cred_password, on_change=CredentialsEditState.set_cred_password, width="100%", border_radius="8px"), width="100%", spacing="1"),
                    rx.vstack(rx.text("ΑΦΜ", font_size="14px", font_weight="500"), rx.input(placeholder="ΑΦΜ πελάτη", value=CredentialsEditState.cred_vat, on_change=CredentialsEditState.set_cred_vat, width="100%", border_radius="8px"), width="100%", spacing="1"),
                    rx.hstack(
                        rx.link(rx.button("Ακύρωση", variant="soft"), href="/credentials"),
                        rx.button(
                            rx.cond(CredentialsEditState.edit_loading, rx.hstack(rx.spinner(size="1"), rx.text("Αποθήκευση..."), spacing="1"), rx.text("💾 Αποθήκευση")),
                            on_click=CredentialsEditState.save_credential,
                            background_color="#0284c7",
                            color="white",
                            border_radius="8px",
                            disabled=CredentialsEditState.edit_loading,
                        ),
                        spacing="3",
                        justify="end",
                        width="100%",
                    ),
                    spacing="4",
                    width="100%",
                ),
                width="460px",
                max_width="95vw",
            ),
            padding_top="40px",
        )
    )
