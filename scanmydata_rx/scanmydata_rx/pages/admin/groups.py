"""Admin groups list page."""
import os
import httpx
import reflex as rx

from ...components.layout import card, layout
from ...state import GlobalState

FLASK_BASE = os.getenv("FLASK_API_URL", f"http://localhost:{os.getenv('PORT', '5001')}")


class AdminGroupsState(GlobalState):
    groups: list[dict] = []
    groups_loading: bool = False
    groups_error: str = ""
    groups_success: str = ""

    async def on_load(self):
        await self.check_auth()
        if not self.is_authenticated or not self.is_admin:
            return rx.redirect("/")
        await self.load_groups()

    async def load_groups(self):
        self.groups_loading = True
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{FLASK_BASE}/api/admin/groups")
                if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                    data = resp.json()
                    self.groups = data.get("groups", [])
        except Exception as e:
            self.groups_error = f"Σφάλμα: {e}"
        finally:
            self.groups_loading = False

    async def delete_group(self, gid: str):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{FLASK_BASE}/admin/groups/{gid}/delete")
                if resp.status_code == 200:
                    self.groups_success = "Η ομάδα διαγράφηκε."
                    await self.load_groups()
        except Exception as e:
            self.groups_error = f"Σφάλμα: {e}"


@rx.page(route="/admin/groups", title="Admin Ομάδες - ScanmyData", on_load=AdminGroupsState.on_load)
def admin_groups_page() -> rx.Component:
    return layout(
        rx.vstack(
            rx.hstack(
                rx.link("← Dashboard", href="/admin/dashboard", font_size="13px", color="#0284c7"),
                rx.heading("👥 Διαχείριση Ομάδων", size="5", color=rx.color_mode_cond("#1f2937", "#f9fafb")),
                spacing="4",
                align="center",
                flex_wrap="wrap",
                width="100%",
            ),
            rx.cond(AdminGroupsState.groups_error != "", rx.box(AdminGroupsState.groups_error, background="#fee2e2", color="#7f1d1d", padding="10px", border_radius="8px", width="100%", font_size="14px"), rx.fragment()),
            rx.cond(AdminGroupsState.groups_success != "", rx.box(AdminGroupsState.groups_success, background="#ecfdf5", color="#065f46", padding="10px", border_radius="8px", width="100%", font_size="14px"), rx.fragment()),
            rx.cond(
                AdminGroupsState.groups_loading,
                rx.center(rx.spinner(size="3"), padding="40px"),
                card(
                    rx.table.root(
                        rx.table.header(
                            rx.table.row(
                                rx.table.column_header_cell("Όνομα", font_size="12px"),
                                rx.table.column_header_cell("Μέλη", font_size="12px"),
                                rx.table.column_header_cell("Δημιουργία", font_size="12px"),
                                rx.table.column_header_cell("Ενέργειες", font_size="12px"),
                            )
                        ),
                        rx.table.body(
                            rx.foreach(
                                AdminGroupsState.groups,
                                lambda g: rx.table.row(
                                    rx.table.cell(rx.text(g["name"], font_size="13px", font_weight="500")),
                                    rx.table.cell(rx.badge(g["member_count"], color_scheme="blue", size="1")),
                                    rx.table.cell(rx.text(g["created_at"], font_size="11px")),
                                    rx.table.cell(
                                        rx.hstack(
                                            rx.link(rx.button("👁️", size="1", variant="soft"), href="/admin/group/" + g["id"]),
                                            rx.button("🗑️", size="1", color_scheme="red", variant="soft", on_click=AdminGroupsState.delete_group(g["id"]), cursor="pointer"),
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
                    width="100%",
                ),
            ),
            width="100%",
            spacing="4",
        )
    )
