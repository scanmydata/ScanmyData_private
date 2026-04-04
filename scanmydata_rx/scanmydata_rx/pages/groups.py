"""Groups page (/auth/groups)."""
import os
import httpx
import reflex as rx

from ..components.layout import card, layout
from ..state import GlobalState

FLASK_BASE = os.getenv("FLASK_API_URL", f"http://localhost:{os.getenv('PORT', '5001')}")


class GroupsState(GlobalState):
    groups: list[dict] = []
    groups_loading: bool = False
    groups_error: str = ""
    groups_success: str = ""

    async def on_load(self):
        await self.check_auth()
        if not self.is_authenticated:
            return rx.redirect("/firebase-auth/login")
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

    async def set_active_group(self, gname: str):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{FLASK_BASE}/set_active", data={"group": gname})
                if resp.status_code == 200:
                    self.active_group = gname
                    self.groups_success = f"Ενεργή ομάδα: {gname}"
        except Exception as e:
            self.groups_error = f"Σφάλμα: {e}"


@rx.page(route="/auth/groups", title="Ομάδες - ScanmyData", on_load=GroupsState.on_load)
def groups_page() -> rx.Component:
    return layout(
        rx.vstack(
            rx.heading("👥 Ομάδες", size="5", color=rx.color_mode_cond("#1f2937", "#f9fafb")),
            rx.text("Επιλέξτε ενεργή ομάδα εργασίας.", font_size="13px", color=rx.color_mode_cond("#6b7280", "#9ca3af")),
            rx.cond(GroupsState.groups_error != "", rx.box(GroupsState.groups_error, background="#fee2e2", color="#7f1d1d", padding="10px", border_radius="8px", width="100%", font_size="14px"), rx.fragment()),
            rx.cond(GroupsState.groups_success != "", rx.box(GroupsState.groups_success, background="#ecfdf5", color="#065f46", padding="10px", border_radius="8px", width="100%", font_size="14px"), rx.fragment()),
            rx.cond(
                GroupsState.groups_loading,
                rx.center(rx.spinner(size="3"), padding="40px"),
                rx.cond(
                    GroupsState.groups.length() > 0,
                    rx.vstack(
                        rx.foreach(
                            GroupsState.groups,
                            lambda g: card(
                                rx.hstack(
                                    rx.vstack(
                                        rx.text(g["name"], font_weight="600", font_size="15px"),
                                        rx.text("Μέλη: ", g["member_count"], font_size="12px", color=rx.color_mode_cond("#6b7280", "#9ca3af")),
                                        spacing="1",
                                        align="start",
                                    ),
                                    rx.spacer(),
                                    rx.cond(
                                        GroupsState.active_group == g["name"],
                                        rx.badge("✓ Ενεργή", color_scheme="green", variant="soft"),
                                        rx.button(
                                            "Επιλογή",
                                            on_click=GroupsState.set_active_group(g["name"]),
                                            size="1",
                                            background_color="#0284c7",
                                            color="white",
                                            border_radius="6px",
                                            cursor="pointer",
                                        ),
                                    ),
                                    align="center",
                                    width="100%",
                                ),
                                width="100%",
                                padding="14px 18px",
                            ),
                        ),
                        width="100%",
                        spacing="2",
                    ),
                    rx.center(
                        rx.vstack(rx.text("👥", font_size="36px"), rx.text("Δεν υπάρχουν ομάδες.", font_size="14px", color="#6b7280"), spacing="2", align="center"),
                        padding="40px",
                    ),
                ),
            ),
            width="100%",
            spacing="4",
        )
    )
