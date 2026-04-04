"""Global application state for ScanmyData Reflex frontend."""
import httpx
import reflex as rx

FLASK_BASE = "http://localhost:5000"


class GlobalState(rx.State):
    """Shared state accessible across all pages."""

    is_authenticated: bool = False
    username: str = ""
    user_email: str = ""
    is_admin: bool = False
    active_credential: str = ""
    active_credential_vat: str = ""
    active_group: str = ""
    active_year: str = ""
    available_years: list[str] = []
    flash_messages: list[dict] = []
    side_menu_open: bool = False
    loading: bool = False

    # ------------------------------------------------------------------ auth

    async def check_auth(self):
        """Fetch auth status from Flask and update state."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(
                    f"{FLASK_BASE}/api/auth/status",
                    cookies=self.router.session.client_token and {} or {},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    self.is_authenticated = data.get("authenticated", False)
                    self.username = data.get("username", "")
                    self.user_email = data.get("email", "")
                    self.is_admin = data.get("is_admin", False)
                    self.active_credential = data.get("active_credential", "")
                    self.active_credential_vat = data.get("active_credential_vat", "")
                    self.active_group = data.get("active_group", "")
                    self.active_year = data.get("active_year", "")
                    self.available_years = data.get("available_years", [])
        except Exception:
            self.is_authenticated = False

    async def logout(self):
        """Log the user out via Flask session."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(f"{FLASK_BASE}/auth/logout")
        except Exception:
            pass
        self.is_authenticated = False
        self.username = ""
        self.user_email = ""
        self.is_admin = False
        self.active_credential = ""
        self.active_group = ""
        self.flash_messages = []
        return rx.redirect("/firebase-auth/login")

    async def set_fiscal_year(self, year: str):
        """Change the active fiscal year."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(
                    f"{FLASK_BASE}/set_fiscal_year",
                    data={"year": year},
                )
            self.active_year = year
        except Exception:
            pass

    # ------------------------------------------------------------------ UI helpers

    def toggle_side_menu(self):
        self.side_menu_open = not self.side_menu_open

    def close_side_menu(self):
        self.side_menu_open = False

    def add_flash(self, msg_type: str, message: str):
        self.flash_messages.append({"type": msg_type, "message": message})

    def clear_flash(self, idx: int):
        self.flash_messages = [
            m for i, m in enumerate(self.flash_messages) if i != idx
        ]

    def clear_flash_by_message(self, message: str):
        self.flash_messages = [m for m in self.flash_messages if m.get("message") != message]

    def clear_all_flash(self):
        self.flash_messages = []
