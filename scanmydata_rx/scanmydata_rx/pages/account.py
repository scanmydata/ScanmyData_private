"""Account management page (/auth/account)."""
import os
import httpx
import reflex as rx

from ..components.layout import card, layout
from ..state import GlobalState

FLASK_BASE = os.getenv("FLASK_API_URL", f"http://localhost:{os.getenv('PORT', '5001')}")


class AccountState(GlobalState):
    acc_error: str = ""
    acc_success: str = ""
    acc_loading: bool = False

    async def on_load(self):
        await self.check_auth()
        if not self.is_authenticated:
            return rx.redirect("/firebase-auth/login")

    async def delete_account(self):
        self.acc_loading = True
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{FLASK_BASE}/auth/account/delete")
                if resp.status_code == 200:
                    return rx.redirect("/firebase-auth/login")
                else:
                    self.acc_error = "Σφάλμα διαγραφής λογαριασμού."
        except Exception as e:
            self.acc_error = f"Σφάλμα: {e}"
        finally:
            self.acc_loading = False


@rx.page(route="/auth/account", title="Λογαριασμός - ScanmyData", on_load=AccountState.on_load)
def account_page() -> rx.Component:
    return layout(
        rx.vstack(
            rx.heading("👤 Λογαριασμός", size="5", color=rx.color_mode_cond("#1f2937", "#f9fafb")),
            rx.cond(AccountState.acc_error != "", rx.box(AccountState.acc_error, background="#fee2e2", color="#7f1d1d", padding="10px", border_radius="8px", width="100%", font_size="14px"), rx.fragment()),
            rx.cond(AccountState.acc_success != "", rx.box(AccountState.acc_success, background="#ecfdf5", color="#065f46", padding="10px", border_radius="8px", width="100%", font_size="14px"), rx.fragment()),
            card(
                rx.vstack(
                    rx.heading("Πληροφορίες Λογαριασμού", size="3"),
                    rx.hstack(
                        rx.text("Email:", font_weight="500", min_width="120px"),
                        rx.text(AccountState.user_email, color=rx.color_mode_cond("#374151", "#d1d5db")),
                        align="center",
                    ),
                    rx.hstack(
                        rx.text("Όνομα:", font_weight="500", min_width="120px"),
                        rx.text(AccountState.username, color=rx.color_mode_cond("#374151", "#d1d5db")),
                        align="center",
                    ),
                    rx.hstack(
                        rx.text("Ρόλος:", font_weight="500", min_width="120px"),
                        rx.cond(AccountState.is_admin, rx.badge("Admin", color_scheme="red", variant="soft"), rx.badge("Χρήστης", color_scheme="blue", variant="soft")),
                        align="center",
                    ),
                    rx.hstack(
                        rx.text("Ενεργή Ομάδα:", font_weight="500", min_width="120px"),
                        rx.text(rx.cond(AccountState.active_group != "", AccountState.active_group, "—"), color=rx.color_mode_cond("#374151", "#d1d5db")),
                        align="center",
                    ),
                    rx.divider(margin_top="8px"),
                    rx.hstack(
                        rx.link(rx.button("✏️ Επεξεργασία Προφίλ", background_color="#0284c7", color="white", border_radius="8px"), href="/firebase-auth/profile"),
                        rx.link(rx.button("🔑 Αλλαγή Κωδικού", variant="soft", border_radius="8px"), href="/firebase-auth/forgot-password"),
                        spacing="3",
                        flex_wrap="wrap",
                    ),
                    spacing="3",
                    width="100%",
                    align="start",
                ),
                width="100%",
            ),
            card(
                rx.vstack(
                    rx.heading("⚠️ Επικίνδυνη Ζώνη", size="3", color="#dc2626"),
                    rx.text("Η διαγραφή λογαριασμού είναι μη αναστρέψιμη.", font_size="13px", color=rx.color_mode_cond("#6b7280", "#9ca3af")),
                    rx.button(
                        rx.cond(AccountState.acc_loading, rx.hstack(rx.spinner(size="2"), rx.text("Διαγραφή..."), spacing="2"), rx.text("🗑️ Διαγραφή Λογαριασμού")),
                        on_click=AccountState.delete_account,
                        color_scheme="red",
                        variant="soft",
                        border_radius="8px",
                        disabled=AccountState.acc_loading,
                    ),
                    spacing="3",
                    width="100%",
                    align="start",
                ),
                border=rx.color_mode_cond("1px solid #fca5a5", "1px solid #7f1d1d"),
                width="100%",
            ),
            width="100%",
            spacing="4",
        )
    )
