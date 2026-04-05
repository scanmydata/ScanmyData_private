import reflex as rx
import httpx

FLASK_API_BASE = "http://localhost:5000"


class AuthState(rx.State):
    """Manages authentication state."""
    firebase_token: str = ""
    user_email: str = ""
    is_authenticated: bool = False
    is_loading: bool = False
    error_message: str = ""

    def set_token(self, token: str):
        self.firebase_token = token
        self.is_authenticated = bool(token)

    async def verify_session(self):
        """Check if current session is valid by hitting Flask /health."""
        self.is_loading = True
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{FLASK_API_BASE}/health",
                    headers={"Cookie": f"session={self.firebase_token}"},
                    timeout=5.0,
                )
            self.is_authenticated = resp.status_code == 200
        except Exception:
            self.is_authenticated = False
        finally:
            self.is_loading = False

    def logout(self):
        self.firebase_token = ""
        self.user_email = ""
        self.is_authenticated = False
        return rx.redirect("/login")
