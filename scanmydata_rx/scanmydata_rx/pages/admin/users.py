"""Admin users list page."""
import httpx
import reflex as rx

from ...components.layout import card, layout
from ...state import GlobalState

FLASK_BASE = "http://localhost:5000"


class AdminUsersState(GlobalState):
    users: list[dict] = []
    users_loading: bool = False
    users_error: str = ""
    users_success: str = ""

    async def on_load(self):
        await self.check_auth()
        if not self.is_authenticated or not self.is_admin:
            return rx.redirect("/")
        await self.load_users()

    async def load_users(self):
        self.users_loading = True
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{FLASK_BASE}/api/admin/users")
                if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                    data = resp.json()
                    self.users = data.get("users", [])
        except Exception as e:
            self.users_error = f"Σφάλμα: {e}"
        finally:
            self.users_loading = False

    async def delete_user(self, uid: str):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{FLASK_BASE}/admin/users/{uid}/delete")
                if resp.status_code == 200:
                    self.users_success = "Ο χρήστης διαγράφηκε."
                    await self.load_users()
        except Exception as e:
            self.users_error = f"Σφάλμα: {e}"


@rx.page(route="/admin/users", title="Admin Χρήστες - ScanmyData", on_load=AdminUsersState.on_load)
def admin_users_page() -> rx.Component:
    return layout(
        rx.vstack(
            rx.hstack(
                rx.link("← Dashboard", href="/admin/dashboard", font_size="13px", color="#0284c7"),
                rx.heading("👤 Διαχείριση Χρηστών", size="5", color=rx.color_mode_cond("#1f2937", "#f9fafb")),
                spacing="4",
                align="center",
                flex_wrap="wrap",
                width="100%",
            ),
            rx.cond(AdminUsersState.users_error != "", rx.box(AdminUsersState.users_error, background="#fee2e2", color="#7f1d1d", padding="10px", border_radius="8px", width="100%", font_size="14px"), rx.fragment()),
            rx.cond(AdminUsersState.users_success != "", rx.box(AdminUsersState.users_success, background="#ecfdf5", color="#065f46", padding="10px", border_radius="8px", width="100%", font_size="14px"), rx.fragment()),
            rx.cond(
                AdminUsersState.users_loading,
                rx.center(rx.spinner(size="3"), padding="40px"),
                card(
                    rx.scroll_area(
                        rx.table.root(
                            rx.table.header(
                                rx.table.row(
                                    rx.table.column_header_cell("Email", font_size="12px"),
                                    rx.table.column_header_cell("Όνομα", font_size="12px"),
                                    rx.table.column_header_cell("Κατάσταση", font_size="12px"),
                                    rx.table.column_header_cell("Δημιουργία", font_size="12px"),
                                    rx.table.column_header_cell("Ενέργειες", font_size="12px"),
                                )
                            ),
                            rx.table.body(
                                rx.foreach(
                                    AdminUsersState.users,
                                    lambda u: rx.table.row(
                                        rx.table.cell(rx.text(u["email"], font_size="12px")),
                                        rx.table.cell(rx.text(u["display_name"], font_size="12px")),
                                        rx.table.cell(
                                            rx.cond(u["disabled"], rx.badge("Ανενεργός", color_scheme="red", size="1"), rx.badge("Ενεργός", color_scheme="green", size="1")),
                                        ),
                                        rx.table.cell(rx.text(u["creation_time"], font_size="11px")),
                                        rx.table.cell(
                                            rx.hstack(
                                                rx.link(rx.button("👁️", size="1", variant="soft"), href="/admin/user/" + u["uid"]),
                                                rx.button("🗑️", size="1", color_scheme="red", variant="soft", on_click=AdminUsersState.delete_user(u["uid"]), cursor="pointer"),
                                                spacing="1",
                                            )
                                        ),
                                        _hover={"background_color": rx.color_mode_cond("#f9fafb", "#374151")},
                                    ),
                                )
                            ),
                            width="100%",
                            variant="surface",
                        ),
                        type="always",
                        scrollbars="horizontal",
                        max_height="600px",
                    ),
                    width="100%",
                ),
            ),
            width="100%",
            spacing="4",
        )
    )
