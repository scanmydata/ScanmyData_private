"""Admin group detail page."""
import httpx
import reflex as rx

from ...components.layout import card, layout
from ...state import GlobalState

FLASK_BASE = "http://localhost:5000"


class AdminGroupDetailState(GlobalState):
    group_id: str = ""
    group_data: dict = {
        "name": "—", "id": "—", "member_count": 0, "created_at": "—",
    }
    gd_loading: bool = False
    gd_error: str = ""
    gd_success: str = ""

    async def on_load(self):
        await self.check_auth()
        if not self.is_authenticated or not self.is_admin:
            return rx.redirect("/")

    async def load_group(self, gid: str):
        self.group_id = gid
        self.gd_loading = True
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{FLASK_BASE}/admin/groups/{gid}")
                if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                    raw = resp.json()
                    self.group_data = {
                        "name": str(raw.get("name") or "—"),
                        "id": str(raw.get("id") or "—"),
                        "member_count": int(raw.get("member_count") or 0),
                        "created_at": str(raw.get("created_at") or "—"),
                    }
        except Exception as e:
            self.gd_error = f"Σφάλμα: {e}"
        finally:
            self.gd_loading = False

    async def create_backup(self):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{FLASK_BASE}/admin/groups/{self.group_id}/backup")
                if resp.status_code == 200:
                    self.gd_success = "Το backup δημιουργήθηκε."
                else:
                    self.gd_error = "Σφάλμα δημιουργίας backup."
        except Exception as e:
            self.gd_error = f"Σφάλμα: {e}"


@rx.page(route="/admin/group/[gid]", title="Admin Ομάδα - ScanmyData", on_load=AdminGroupDetailState.on_load)
def admin_group_detail_page() -> rx.Component:
    return layout(
        rx.vstack(
            rx.hstack(
                rx.link("← Ομάδες", href="/admin/groups", font_size="13px", color="#0284c7"),
                rx.heading("👥 Λεπτομέρειες Ομάδας", size="5", color=rx.color_mode_cond("#1f2937", "#f9fafb")),
                spacing="4",
                align="center",
                flex_wrap="wrap",
                width="100%",
            ),
            rx.cond(AdminGroupDetailState.gd_error != "", rx.box(AdminGroupDetailState.gd_error, background="#fee2e2", color="#7f1d1d", padding="10px", border_radius="8px", width="100%", font_size="14px"), rx.fragment()),
            rx.cond(AdminGroupDetailState.gd_success != "", rx.box(AdminGroupDetailState.gd_success, background="#ecfdf5", color="#065f46", padding="10px", border_radius="8px", width="100%", font_size="14px"), rx.fragment()),
            rx.cond(
                AdminGroupDetailState.gd_loading,
                rx.center(rx.spinner(size="3"), padding="40px"),
                card(
                    rx.vstack(
                        rx.heading(AdminGroupDetailState.group_data["name"], size="4"),
                        rx.hstack(
                            rx.text("ID:", font_weight="500", min_width="120px"),
                            rx.text(AdminGroupDetailState.group_data["id"], font_size="12px", font_family="monospace"),
                        ),
                        rx.hstack(
                            rx.text("Μέλη:", font_weight="500", min_width="120px"),
                            rx.badge(AdminGroupDetailState.group_data["member_count"], color_scheme="blue"),
                        ),
                        rx.hstack(
                            rx.text("Δημιουργία:", font_weight="500", min_width="120px"),
                            rx.text(AdminGroupDetailState.group_data["created_at"]),
                        ),
                        rx.divider(),
                        rx.hstack(
                            rx.button("💾 Δημιουργία Backup", on_click=AdminGroupDetailState.create_backup, background_color="#0284c7", color="white", border_radius="8px"),
                            rx.link(rx.button("📁 Αρχεία", variant="soft", border_radius="8px"), href=f"{FLASK_BASE}/admin/groups/{AdminGroupDetailState.group_id}/files", is_external=True),
                            spacing="3",
                            flex_wrap="wrap",
                        ),
                        spacing="3",
                        width="100%",
                        align="start",
                    ),
                    width="100%",
                ),
            ),
            width="100%",
            spacing="4",
        )
    )
