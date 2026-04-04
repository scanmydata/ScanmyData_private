"""Admin dashboard — unified tabbed view."""
import os
import httpx
import reflex as rx

from ...components.layout import card, layout
from ...state import GlobalState

FLASK_BASE = os.getenv("FLASK_API_URL", f"http://localhost:{os.getenv('PORT', '5001')}")


class AdminDashState(GlobalState):
    active_tab: str = "overview"
    # Overview — pre-populate keys so var access works before data loads
    stats: dict = {"users": "—", "groups": "—", "activity_24h": "—", "system_status": "OK"}
    recent_activity: list[dict] = []
    # Users
    users: list[dict] = []
    users_loading: bool = False
    # Groups
    admin_groups: list[dict] = []
    groups_loading: bool = False
    # Activity
    activity_logs: list[dict] = []
    activity_loading: bool = False
    # Backups
    backups: list[dict] = []
    backups_loading: bool = False
    # Email
    email_to: str = ""
    email_subject: str = ""
    email_body: str = ""
    email_loading: bool = False
    email_error: str = ""
    email_success: str = ""
    # Firebase
    firebase_usage: dict = {}
    # Settings
    admin_settings: dict = {}
    settings_loading: bool = False
    settings_success: str = ""
    settings_error: str = ""
    # General
    dash_loading: bool = False
    dash_error: str = ""

    async def on_load(self):
        await self.check_auth()
        if not self.is_authenticated:
            return rx.redirect("/firebase-auth/login")
        if not self.is_admin:
            return rx.redirect("/")
        await self.load_overview()

    async def load_overview(self):
        self.dash_loading = True
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{FLASK_BASE}/api/admin/stats")
                if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                    data = resp.json()
                    loaded = data.get("stats", {})
                    self.stats = {
                        "users": str(loaded.get("users", "—")),
                        "groups": str(loaded.get("groups", "—")),
                        "activity_24h": str(loaded.get("activity_24h", "—")),
                        "system_status": str(loaded.get("system_status", "OK")),
                    }
                    self.recent_activity = data.get("recent_activity", [])
        except Exception as e:
            self.dash_error = f"Σφάλμα: {e}"
        finally:
            self.dash_loading = False

    async def load_users(self):
        self.users_loading = True
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{FLASK_BASE}/api/admin/users")
                if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                    data = resp.json()
                    self.users = data.get("users", [])
        except Exception:
            pass
        finally:
            self.users_loading = False

    async def load_groups(self):
        self.groups_loading = True
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{FLASK_BASE}/api/admin/groups")
                if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                    data = resp.json()
                    self.admin_groups = data.get("groups", [])
        except Exception:
            pass
        finally:
            self.groups_loading = False

    async def load_activity(self):
        self.activity_loading = True
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{FLASK_BASE}/api/admin/activity-logs")
                if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                    data = resp.json()
                    self.activity_logs = data.get("logs", [])
        except Exception:
            pass
        finally:
            self.activity_loading = False

    async def load_firebase_usage(self):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{FLASK_BASE}/api/admin/firebase-usage")
                if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                    self.firebase_usage = resp.json()
        except Exception:
            pass

    async def load_settings(self):
        self.settings_loading = True
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{FLASK_BASE}/admin/settings")
                if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                    self.admin_settings = resp.json()
        except Exception:
            pass
        finally:
            self.settings_loading = False

    async def set_tab(self, tab: str):
        self.active_tab = tab
        if tab == "users" and not self.users:
            await self.load_users()
        elif tab == "groups" and not self.admin_groups:
            await self.load_groups()
        elif tab == "activity":
            await self.load_activity()
        elif tab == "firebase":
            await self.load_firebase_usage()
        elif tab == "settings" and not self.admin_settings:
            await self.load_settings()

    async def send_email(self):
        self.email_error = ""
        self.email_success = ""
        self.email_loading = True
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{FLASK_BASE}/admin/send-email",
                    data={"to": self.email_to, "subject": self.email_subject, "body": self.email_body},
                )
                if resp.status_code == 200:
                    self.email_success = "Το email στάλθηκε επιτυχώς."
                    self.email_to = ""
                    self.email_subject = ""
                    self.email_body = ""
                else:
                    self.email_error = "Σφάλμα αποστολής email."
        except Exception as e:
            self.email_error = f"Σφάλμα: {e}"
        finally:
            self.email_loading = False

    async def clear_activity_logs(self):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(f"{FLASK_BASE}/api/admin/activity-logs/clear")
            self.activity_logs = []
        except Exception:
            pass

    async def delete_user(self, uid: str):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(f"{FLASK_BASE}/admin/users/{uid}/delete")
            await self.load_users()
        except Exception as e:
            self.dash_error = f"Σφάλμα: {e}"


def _stat_card(label: str, value: str, icon: str, color: str) -> rx.Component:
    return card(
        rx.vstack(
            rx.hstack(
                rx.text(icon, font_size="28px"),
                rx.spacer(),
                rx.badge(value, color_scheme=color, variant="soft", font_size="18px", padding="4px 12px"),
                width="100%",
                align="center",
            ),
            rx.text(label, font_size="13px", font_weight="500", color=rx.color_mode_cond("#6b7280", "#9ca3af")),
            spacing="2",
            width="100%",
        ),
        flex="1",
        min_width="180px",
    )


def _overview_tab() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            _stat_card("Χρήστες", AdminDashState.stats["users"], "👤", "blue"),
            _stat_card("Ομάδες", AdminDashState.stats["groups"], "👥", "green"),
            _stat_card("Δραστηριότητα 24h", AdminDashState.stats["activity_24h"], "📊", "orange"),
            _stat_card("Κατάσταση Συστήματος", AdminDashState.stats["system_status"], "🟢", "green"),
            width="100%",
            spacing="4",
            flex_wrap="wrap",
        ),
        rx.cond(
            AdminDashState.recent_activity.length() > 0,
            card(
                rx.vstack(
                    rx.heading("Πρόσφατη Δραστηριότητα", size="3"),
                    rx.table.root(
                        rx.table.header(
                            rx.table.row(
                                rx.table.column_header_cell("Χρόνος", font_size="12px"),
                                rx.table.column_header_cell("Χρήστης", font_size="12px"),
                                rx.table.column_header_cell("Ενέργεια", font_size="12px"),
                            )
                        ),
                        rx.table.body(
                            rx.foreach(
                                AdminDashState.recent_activity,
                                lambda a: rx.table.row(
                                    rx.table.cell(rx.text(a["time"], font_size="12px")),
                                    rx.table.cell(rx.text(a["user"], font_size="12px")),
                                    rx.table.cell(rx.text(a["action"], font_size="12px")),
                                ),
                            )
                        ),
                        width="100%",
                        variant="surface",
                    ),
                    width="100%",
                    spacing="3",
                ),
                width="100%",
            ),
            rx.fragment(),
        ),
        width="100%",
        spacing="4",
    )


def _users_tab() -> rx.Component:
    return rx.cond(
        AdminDashState.users_loading,
        rx.center(rx.spinner(size="3"), padding="40px"),
        card(
            rx.vstack(
                rx.hstack(
                    rx.heading("Χρήστες", size="3"),
                    rx.spacer(),
                    rx.button("🔄 Ανανέωση", on_click=AdminDashState.load_users, size="1", variant="soft"),
                    width="100%",
                    align="center",
                ),
                rx.cond(
                    AdminDashState.users.length() > 0,
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
                                    AdminDashState.users,
                                    lambda u: rx.table.row(
                                        rx.table.cell(rx.text(u["email"], font_size="12px")),
                                        rx.table.cell(rx.text(u["display_name"], font_size="12px")),
                                        rx.table.cell(
                                            rx.cond(
                                                u["disabled"],
                                                rx.badge("Ανενεργός", color_scheme="red", size="1"),
                                                rx.badge("Ενεργός", color_scheme="green", size="1"),
                                            )
                                        ),
                                        rx.table.cell(rx.text(u["creation_time"], font_size="11px")),
                                        rx.table.cell(
                                            rx.hstack(
                                                rx.link(rx.button("👁️", size="1", variant="soft"), href="/admin/user/" + u["uid"]),
                                                rx.button("🗑️", size="1", color_scheme="red", variant="soft", on_click=AdminDashState.delete_user(u["uid"]), cursor="pointer"),
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
                        max_height="500px",
                    ),
                    rx.center(rx.text("Δεν υπάρχουν χρήστες.", font_size="14px", color="#6b7280"), padding="30px"),
                ),
                spacing="3",
                width="100%",
            ),
            width="100%",
        ),
    )


def _groups_tab() -> rx.Component:
    return rx.cond(
        AdminDashState.groups_loading,
        rx.center(rx.spinner(size="3"), padding="40px"),
        card(
            rx.vstack(
                rx.hstack(
                    rx.heading("Ομάδες", size="3"),
                    rx.spacer(),
                    rx.button("🔄 Ανανέωση", on_click=AdminDashState.load_groups, size="1", variant="soft"),
                    width="100%",
                    align="center",
                ),
                rx.cond(
                    AdminDashState.admin_groups.length() > 0,
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
                                AdminDashState.admin_groups,
                                lambda g: rx.table.row(
                                    rx.table.cell(rx.text(g["name"], font_size="13px", font_weight="500")),
                                    rx.table.cell(rx.badge(g["member_count"], color_scheme="blue", size="1")),
                                    rx.table.cell(rx.text(g["created_at"], font_size="11px")),
                                        rx.table.cell(
                                        rx.link(rx.button("👁️ Λεπτομέρειες", size="1", variant="soft"), href="/admin/group/" + g["id"]),
                                        ),
                                    _hover={"background_color": rx.color_mode_cond("#f9fafb", "#374151")},
                                ),
                            )
                        ),
                        width="100%",
                        variant="surface",
                    ),
                    rx.center(rx.text("Δεν υπάρχουν ομάδες.", font_size="14px", color="#6b7280"), padding="30px"),
                ),
                spacing="3",
                width="100%",
            ),
            width="100%",
        ),
    )


def _activity_tab() -> rx.Component:
    return rx.cond(
        AdminDashState.activity_loading,
        rx.center(rx.spinner(size="3"), padding="40px"),
        card(
            rx.vstack(
                rx.hstack(
                    rx.heading("Δραστηριότητα", size="3"),
                    rx.spacer(),
                    rx.button("🔄 Ανανέωση", on_click=AdminDashState.load_activity, size="1", variant="soft"),
                    rx.button("🗑️ Καθαρισμός", on_click=AdminDashState.clear_activity_logs, size="1", color_scheme="red", variant="soft"),
                    width="100%",
                    align="center",
                    spacing="2",
                ),
                rx.cond(
                    AdminDashState.activity_logs.length() > 0,
                    rx.scroll_area(
                        rx.table.root(
                            rx.table.header(
                                rx.table.row(
                                    rx.table.column_header_cell("Χρόνος", font_size="12px"),
                                    rx.table.column_header_cell("Χρήστης", font_size="12px"),
                                    rx.table.column_header_cell("Ενέργεια", font_size="12px"),
                                    rx.table.column_header_cell("IP", font_size="12px"),
                                )
                            ),
                            rx.table.body(
                                rx.foreach(
                                    AdminDashState.activity_logs,
                                    lambda a: rx.table.row(
                                        rx.table.cell(rx.text(a["time"], font_size="11px")),
                                        rx.table.cell(rx.text(a["user"], font_size="12px")),
                                        rx.table.cell(rx.text(a["action"], font_size="12px")),
                                        rx.table.cell(rx.text(a["ip"], font_size="11px")),
                                        _hover={"background_color": rx.color_mode_cond("#f9fafb", "#374151")},
                                    ),
                                )
                            ),
                            width="100%",
                            variant="surface",
                        ),
                        type="always",
                        scrollbars="both",
                        max_height="500px",
                    ),
                    rx.center(rx.text("Δεν υπάρχουν logs.", font_size="14px", color="#6b7280"), padding="30px"),
                ),
                spacing="3",
                width="100%",
            ),
            width="100%",
        ),
    )


def _backups_tab() -> rx.Component:
    return card(
        rx.vstack(
            rx.heading("💾 Backups", size="3"),
            rx.text("Διαχειριστείτε τα αντίγραφα ασφαλείας.", font_size="13px", color=rx.color_mode_cond("#6b7280", "#9ca3af")),
            rx.hstack(
                rx.link(
                    rx.button("📥 Λήψη Backup", background_color="#0284c7", color="white", border_radius="8px"),
                    href=f"{FLASK_BASE}/admin/backups",
                    is_external=True,
                ),
                spacing="3",
            ),
            spacing="4",
            width="100%",
        ),
        width="100%",
    )


def _email_tab() -> rx.Component:
    return card(
        rx.vstack(
            rx.heading("📧 Αποστολή Email", size="3"),
            rx.cond(AdminDashState.email_error != "", rx.box(AdminDashState.email_error, background="#fee2e2", color="#7f1d1d", padding="8px", border_radius="8px", width="100%", font_size="13px"), rx.fragment()),
            rx.cond(AdminDashState.email_success != "", rx.box(AdminDashState.email_success, background="#ecfdf5", color="#065f46", padding="8px", border_radius="8px", width="100%", font_size="13px"), rx.fragment()),
            rx.vstack(rx.text("Προς", font_size="13px", font_weight="500"), rx.input(placeholder="email@example.com", value=AdminDashState.email_to, on_change=AdminDashState.set_email_to, width="100%"), width="100%", spacing="1"),
            rx.vstack(rx.text("Θέμα", font_size="13px", font_weight="500"), rx.input(placeholder="Θέμα email", value=AdminDashState.email_subject, on_change=AdminDashState.set_email_subject, width="100%"), width="100%", spacing="1"),
            rx.vstack(rx.text("Μήνυμα", font_size="13px", font_weight="500"), rx.text_area(placeholder="Κείμενο email...", value=AdminDashState.email_body, on_change=AdminDashState.set_email_body, width="100%", min_height="120px"), width="100%", spacing="1"),
            rx.button(
                rx.cond(AdminDashState.email_loading, rx.hstack(rx.spinner(size="2"), rx.text("Αποστολή..."), spacing="2"), rx.text("📧 Αποστολή")),
                on_click=AdminDashState.send_email,
                background_color="#0284c7",
                color="white",
                border_radius="8px",
                font_weight="600",
                disabled=AdminDashState.email_loading,
                cursor="pointer",
            ),
            spacing="4",
            width="100%",
        ),
        width="100%",
    )


def _firebase_tab() -> rx.Component:
    return card(
        rx.vstack(
            rx.heading("🔥 Firebase Χρήση", size="3"),
            rx.cond(
                AdminDashState.firebase_usage != {},
                rx.vstack(
                    rx.text("Δεδομένα χρήσης Firebase.", font_size="13px", color=rx.color_mode_cond("#6b7280", "#9ca3af")),
                    rx.button("🔄 Ανανέωση", on_click=AdminDashState.load_firebase_usage, size="1", variant="soft"),
                    spacing="3",
                ),
                rx.vstack(
                    rx.text("Φόρτωση Firebase δεδομένων...", font_size="13px", color=rx.color_mode_cond("#6b7280", "#9ca3af")),
                    rx.button("🔄 Φόρτωση", on_click=AdminDashState.load_firebase_usage, background_color="#0284c7", color="white", border_radius="8px"),
                    spacing="3",
                ),
            ),
            spacing="4",
            width="100%",
        ),
        width="100%",
    )


def _settings_tab() -> rx.Component:
    return rx.cond(
        AdminDashState.settings_loading,
        rx.center(rx.spinner(size="3"), padding="40px"),
        card(
            rx.vstack(
                rx.heading("⚙️ Ρυθμίσεις Συστήματος", size="3"),
                rx.cond(AdminDashState.settings_error != "", rx.box(AdminDashState.settings_error, background="#fee2e2", color="#7f1d1d", padding="8px", border_radius="8px", width="100%", font_size="13px"), rx.fragment()),
                rx.cond(AdminDashState.settings_success != "", rx.box(AdminDashState.settings_success, background="#ecfdf5", color="#065f46", padding="8px", border_radius="8px", width="100%", font_size="13px"), rx.fragment()),
                rx.text("Για προχωρημένες ρυθμίσεις συστήματος, αλληλεπιδράστε απευθείας με τον Flask διακομιστή.", font_size="13px", color=rx.color_mode_cond("#6b7280", "#9ca3af")),
                rx.link(
                    rx.button("🔧 Ρυθμίσεις στο Flask", background_color="#0284c7", color="white", border_radius="8px"),
                    href=f"{FLASK_BASE}/admin/settings",
                    is_external=True,
                ),
                spacing="4",
                width="100%",
            ),
            width="100%",
        ),
    )


def _tab_button(label: str, tab_id: str) -> rx.Component:
    return rx.button(
        label,
        on_click=AdminDashState.set_tab(tab_id),
        variant=rx.cond(AdminDashState.active_tab == tab_id, "solid", "ghost"),
        background_color=rx.cond(AdminDashState.active_tab == tab_id, "#0284c7", "transparent"),
        color=rx.cond(
            AdminDashState.active_tab == tab_id,
            "white",
            rx.color_mode_cond("#374151", "#d1d5db"),
        ),
        border_radius="8px",
        font_size="13px",
        padding="7px 14px",
        cursor="pointer",
        white_space="nowrap",
    )


@rx.page(route="/admin/dashboard", title="Admin Dashboard - ScanmyData", on_load=AdminDashState.on_load)
def admin_dashboard_page() -> rx.Component:
    return layout(
        rx.vstack(
            rx.hstack(
                rx.heading("⚙️ Admin Dashboard", size="5", color="#dc2626"),
                rx.spacer(),
                rx.badge("ADMIN", color_scheme="red", variant="solid", padding="4px 10px"),
                width="100%",
                align="center",
            ),
            rx.cond(AdminDashState.dash_error != "", rx.box(AdminDashState.dash_error, background="#fee2e2", color="#7f1d1d", padding="10px", border_radius="8px", width="100%", font_size="14px"), rx.fragment()),
            # Tab buttons
            rx.box(
                rx.hstack(
                    _tab_button("📊 Επισκόπηση", "overview"),
                    _tab_button("👤 Χρήστες", "users"),
                    _tab_button("👥 Ομάδες", "groups"),
                    _tab_button("📋 Δραστηριότητα", "activity"),
                    _tab_button("💾 Backups", "backups"),
                    _tab_button("📧 Email", "email"),
                    _tab_button("🔥 Firebase", "firebase"),
                    _tab_button("⚙️ Ρυθμίσεις", "settings"),
                    spacing="1",
                    flex_wrap="wrap",
                ),
                width="100%",
                padding_bottom="8px",
                border_bottom=rx.color_mode_cond("1px solid #e5e7eb", "1px solid #374151"),
                margin_bottom="16px",
            ),
            # Tab content
            rx.cond(AdminDashState.dash_loading, rx.center(rx.spinner(size="3"), padding="40px"),
                rx.match(
                    AdminDashState.active_tab,
                    ("overview", _overview_tab()),
                    ("users", _users_tab()),
                    ("groups", _groups_tab()),
                    ("activity", _activity_tab()),
                    ("backups", _backups_tab()),
                    ("email", _email_tab()),
                    ("firebase", _firebase_tab()),
                    ("settings", _settings_tab()),
                    _overview_tab(),
                ),
            ),
            width="100%",
            spacing="4",
        )
    )
