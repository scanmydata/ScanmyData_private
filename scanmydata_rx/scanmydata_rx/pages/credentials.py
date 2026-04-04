"""Credentials management page."""
import httpx
import reflex as rx

from ..components.layout import card, layout
from ..state import GlobalState

FLASK_BASE = "http://localhost:5000"


class CredentialsState(GlobalState):
    credentials: list[dict] = []
    creds_loading: bool = False
    creds_error: str = ""
    creds_success: str = ""
    # Add credential modal
    add_modal_open: bool = False
    new_cred_name: str = ""
    new_cred_username: str = ""
    new_cred_password: str = ""
    new_cred_vat: str = ""
    add_loading: bool = False
    # Delete confirm
    delete_confirm_name: str = ""

    async def on_load(self):
        await self.check_auth()
        if not self.is_authenticated:
            return rx.redirect("/firebase-auth/login")
        await self.load_credentials()

    async def load_credentials(self):
        self.creds_loading = True
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{FLASK_BASE}/credentials")
                if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                    data = resp.json()
                    self.credentials = data.get("credentials", [])
                else:
                    self.credentials = []
        except Exception as e:
            self.creds_error = f"Σφάλμα φόρτωσης: {e}"
        finally:
            self.creds_loading = False

    async def set_active(self, name: str):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(f"{FLASK_BASE}/credentials/set_active", data={"name": name})
            self.active_credential = name
            self.creds_success = f"Ενεργό credential: {name}"
            await self.load_credentials()
        except Exception as e:
            self.creds_error = f"Σφάλμα: {e}"

    async def delete_credential(self, name: str):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{FLASK_BASE}/credentials/delete/{name}")
                if resp.status_code == 200:
                    self.creds_success = f"Το credential '{name}' διαγράφηκε."
                    await self.load_credentials()
                else:
                    self.creds_error = "Σφάλμα διαγραφής."
        except Exception as e:
            self.creds_error = f"Σφάλμα: {e}"
        self.delete_confirm_name = ""

    def open_add_modal(self):
        self.add_modal_open = True
        self.new_cred_name = ""
        self.new_cred_username = ""
        self.new_cred_password = ""
        self.new_cred_vat = ""
        self.creds_error = ""

    def close_add_modal(self):
        self.add_modal_open = False

    async def add_credential(self):
        if not self.new_cred_name or not self.new_cred_username:
            self.creds_error = "Συμπληρώστε όνομα και username."
            return
        self.add_loading = True
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{FLASK_BASE}/credentials/add",
                    data={
                        "name": self.new_cred_name,
                        "username": self.new_cred_username,
                        "password": self.new_cred_password,
                        "vat": self.new_cred_vat,
                    },
                )
                if resp.status_code == 200:
                    self.creds_success = "Το credential προστέθηκε."
                    self.add_modal_open = False
                    await self.load_credentials()
                else:
                    data = resp.json() if "json" in resp.headers.get("content-type", "") else {}
                    self.creds_error = data.get("error", "Σφάλμα προσθήκης.")
        except Exception as e:
            self.creds_error = f"Σφάλμα: {e}"
        finally:
            self.add_loading = False


def _credential_row(cred: dict) -> rx.Component:
    return rx.table.row(
        rx.table.cell(
            rx.hstack(
                rx.cond(
                    cred["active"],
                    rx.badge("✓ Ενεργό", color_scheme="green", variant="soft", font_size="11px"),
                    rx.fragment(),
                ),
                rx.text(cred["name"], font_weight=rx.cond(cred["active"], "600", "400"), font_size="14px"),
                spacing="2",
                align="center",
            )
        ),
        rx.table.cell(rx.text(cred.get("vat", "—"), font_size="13px", color=rx.color_mode_cond("#6b7280", "#9ca3af"))),
        rx.table.cell(rx.text(cred.get("username", "—"), font_size="13px")),
        rx.table.cell(
            rx.hstack(
                rx.button(
                    "✓ Ενεργό",
                    on_click=CredentialsState.set_active(cred["name"]),
                    size="1",
                    color_scheme="blue",
                    variant=rx.cond(cred["active"], "solid", "soft"),
                    cursor="pointer",
                ),
                rx.link(
                    rx.button("✏️ Επεξεργασία", size="1", variant="soft", cursor="pointer"),
                    href=f"/credentials/edit/{cred['name']}",
                ),
                rx.button(
                    "🗑️ Διαγραφή",
                    on_click=CredentialsState.delete_credential(cred["name"]),
                    size="1",
                    color_scheme="red",
                    variant="soft",
                    cursor="pointer",
                ),
                spacing="2",
            )
        ),
        _hover={"background_color": rx.color_mode_cond("#f9fafb", "#374151")},
    )


@rx.page(route="/credentials", title="Credentials - ScanmyData", on_load=CredentialsState.on_load)
def credentials_page() -> rx.Component:
    return layout(
        rx.vstack(
            rx.hstack(
                rx.heading("🔐 Credentials", size="5", color=rx.color_mode_cond("#1f2937", "#f9fafb")),
                rx.spacer(),
                rx.button(
                    "➕ Προσθήκη",
                    on_click=CredentialsState.open_add_modal,
                    background_color="#0284c7",
                    color="white",
                    border_radius="8px",
                    font_weight="600",
                    cursor="pointer",
                ),
                width="100%",
                align="center",
                margin_bottom="16px",
            ),
            rx.cond(CredentialsState.creds_error != "", rx.box(CredentialsState.creds_error, background="#fee2e2", color="#7f1d1d", padding="10px", border_radius="8px", width="100%", font_size="14px"), rx.fragment()),
            rx.cond(CredentialsState.creds_success != "", rx.box(CredentialsState.creds_success, background="#ecfdf5", color="#065f46", padding="10px", border_radius="8px", width="100%", font_size="14px"), rx.fragment()),
            rx.cond(
                CredentialsState.creds_loading,
                rx.center(rx.spinner(size="3"), padding="40px"),
                card(
                    rx.cond(
                        CredentialsState.credentials.length() > 0,
                        rx.table.root(
                            rx.table.header(
                                rx.table.row(
                                    rx.table.column_header_cell("Όνομα", font_size="13px", color="#6b7280"),
                                    rx.table.column_header_cell("ΑΦΜ", font_size="13px", color="#6b7280"),
                                    rx.table.column_header_cell("Username", font_size="13px", color="#6b7280"),
                                    rx.table.column_header_cell("Ενέργειες", font_size="13px", color="#6b7280"),
                                )
                            ),
                            rx.table.body(
                                rx.foreach(CredentialsState.credentials, _credential_row)
                            ),
                            width="100%",
                            variant="surface",
                        ),
                        rx.center(
                            rx.vstack(
                                rx.text("🔐", font_size="40px"),
                                rx.text("Δεν υπάρχουν credentials.", font_size="14px", color="#6b7280"),
                                rx.button("➕ Προσθήκη", on_click=CredentialsState.open_add_modal, background_color="#0284c7", color="white", border_radius="8px"),
                                spacing="3",
                                align="center",
                            ),
                            padding="40px",
                        ),
                    ),
                    width="100%",
                ),
            ),
            # Add modal
            rx.dialog.root(
                rx.dialog.content(
                    rx.dialog.title("➕ Προσθήκη Credential"),
                    rx.vstack(
                        rx.vstack(rx.text("Όνομα *", font_size="13px", font_weight="500"), rx.input(placeholder="π.χ. Εταιρεία Α", value=CredentialsState.new_cred_name, on_change=CredentialsState.set_new_cred_name, width="100%"), width="100%", spacing="1"),
                        rx.vstack(rx.text("Username *", font_size="13px", font_weight="500"), rx.input(placeholder="MYDATA username", value=CredentialsState.new_cred_username, on_change=CredentialsState.set_new_cred_username, width="100%"), width="100%", spacing="1"),
                        rx.vstack(rx.text("Password", font_size="13px", font_weight="500"), rx.input(placeholder="MYDATA password", type="password", value=CredentialsState.new_cred_password, on_change=CredentialsState.set_new_cred_password, width="100%"), width="100%", spacing="1"),
                        rx.vstack(rx.text("ΑΦΜ", font_size="13px", font_weight="500"), rx.input(placeholder="ΑΦΜ πελάτη", value=CredentialsState.new_cred_vat, on_change=CredentialsState.set_new_cred_vat, width="100%"), width="100%", spacing="1"),
                        rx.cond(CredentialsState.creds_error != "", rx.box(CredentialsState.creds_error, background="#fee2e2", color="#7f1d1d", padding="8px", border_radius="6px", width="100%", font_size="13px"), rx.fragment()),
                        rx.hstack(
                            rx.dialog.close(rx.button("Ακύρωση", variant="soft", on_click=CredentialsState.close_add_modal)),
                            rx.button(
                                rx.cond(CredentialsState.add_loading, rx.hstack(rx.spinner(size="1"), rx.text("Αποθήκευση..."), spacing="1"), rx.text("💾 Αποθήκευση")),
                                on_click=CredentialsState.add_credential,
                                background_color="#0284c7",
                                color="white",
                                disabled=CredentialsState.add_loading,
                            ),
                            spacing="3",
                            justify="end",
                            width="100%",
                        ),
                        spacing="3",
                        width="100%",
                    ),
                    max_width="480px",
                ),
                open=CredentialsState.add_modal_open,
                on_open_change=CredentialsState.set_add_modal_open,
            ),
            width="100%",
            spacing="4",
        )
    )
