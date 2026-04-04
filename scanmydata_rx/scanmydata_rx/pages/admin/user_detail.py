"""Admin user detail page."""
import httpx
import reflex as rx

from ...components.layout import card, layout
from ...state import GlobalState

FLASK_BASE = "http://localhost:5000"


class AdminUserDetailState(GlobalState):
    user_uid: str = ""
    user_data: dict = {
        "display_name": "—", "email": "—", "uid": "—",
        "disabled": False, "creation_time": "—", "last_sign_in_time": "—",
    }
    ud_loading: bool = False
    ud_error: str = ""

    async def on_load(self):
        await self.check_auth()
        if not self.is_authenticated or not self.is_admin:
            return rx.redirect("/")

    async def load_user(self, uid: str):
        self.user_uid = uid
        self.ud_loading = True
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{FLASK_BASE}/admin/users/{uid}")
                if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                    raw = resp.json()
                    self.user_data = {
                        "display_name": str(raw.get("display_name") or "—"),
                        "email": str(raw.get("email") or "—"),
                        "uid": str(raw.get("uid") or "—"),
                        "disabled": bool(raw.get("disabled", False)),
                        "creation_time": str(raw.get("creation_time") or "—"),
                        "last_sign_in_time": str(raw.get("last_sign_in_time") or "—"),
                    }
        except Exception as e:
            self.ud_error = f"Σφάλμα: {e}"
        finally:
            self.ud_loading = False


@rx.page(route="/admin/user/[uid]", title="Admin Χρήστης - ScanmyData", on_load=AdminUserDetailState.on_load)
def admin_user_detail_page() -> rx.Component:
    return layout(
        rx.vstack(
            rx.hstack(
                rx.link("← Χρήστες", href="/admin/users", font_size="13px", color="#0284c7"),
                rx.heading("👤 Λεπτομέρειες Χρήστη", size="5", color=rx.color_mode_cond("#1f2937", "#f9fafb")),
                spacing="4",
                align="center",
                flex_wrap="wrap",
                width="100%",
            ),
            rx.cond(AdminUserDetailState.ud_error != "", rx.box(AdminUserDetailState.ud_error, background="#fee2e2", color="#7f1d1d", padding="10px", border_radius="8px", width="100%", font_size="14px"), rx.fragment()),
            rx.cond(
                AdminUserDetailState.ud_loading,
                rx.center(rx.spinner(size="3"), padding="40px"),
                card(
                    rx.vstack(
                        rx.heading(AdminUserDetailState.user_data["display_name"], size="4"),
                        rx.hstack(rx.text("Email:", font_weight="500", min_width="120px"), rx.text(AdminUserDetailState.user_data["email"]), align="center"),
                        rx.hstack(rx.text("UID:", font_weight="500", min_width="120px"), rx.text(AdminUserDetailState.user_data["uid"], font_size="12px", font_family="monospace")),
                        rx.hstack(rx.text("Κατάσταση:", font_weight="500", min_width="120px"),
                            rx.cond(AdminUserDetailState.user_data["disabled"], rx.badge("Ανενεργός", color_scheme="red"), rx.badge("Ενεργός", color_scheme="green")),
                            align="center",
                        ),
                        rx.hstack(rx.text("Δημιουργία:", font_weight="500", min_width="120px"), rx.text(AdminUserDetailState.user_data["creation_time"])),
                        rx.hstack(rx.text("Τελευταία Σύνδεση:", font_weight="500", min_width="120px"), rx.text(AdminUserDetailState.user_data["last_sign_in_time"])),
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
