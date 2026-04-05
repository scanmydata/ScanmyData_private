import reflex as rx
import httpx
from typing import List, Dict, Any

from ..config import FLASK_API_BASE


class CredentialsState(rx.State):
    """Manages credentials list state."""
    credentials: List[Dict[str, Any]] = []
    is_loading: bool = False
    error: str = ""
    success: str = ""
    active_credential_name: str = ""

    # Form fields for adding a credential
    form_name: str = ""
    form_user: str = ""
    form_key: str = ""
    form_vat: str = ""

    async def load_credentials(self):
        """Load credentials from Flask API."""
        self.is_loading = True
        self.error = ""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{FLASK_API_BASE}/api/credentials",
                    timeout=10.0,
                )
            if resp.status_code == 200:
                data = resp.json()
                self.credentials = data.get("credentials", [])
                self.active_credential_name = data.get("active_name", "")
            else:
                self.error = f"Σφάλμα φόρτωσης credentials (HTTP {resp.status_code})"
        except Exception as e:
            self.error = f"Σφάλμα σύνδεσης: {e}"
        finally:
            self.is_loading = False

    def set_form_name(self, value: str):
        self.form_name = value

    def set_form_user(self, value: str):
        self.form_user = value

    def set_form_key(self, value: str):
        self.form_key = value

    def set_form_vat(self, value: str):
        self.form_vat = value

    async def add_credential(self):
        """Add a new credential via Flask API."""
        if not self.form_name.strip():
            self.error = "Απαιτείται όνομα credential"
            return
        self.is_loading = True
        self.error = ""
        self.success = ""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{FLASK_API_BASE}/credentials/add",
                    data={
                        "name": self.form_name,
                        "user": self.form_user,
                        "key": self.form_key,
                        "vat": self.form_vat,
                    },
                    timeout=10.0,
                )
            if resp.status_code in (200, 201, 302):
                self.success = "Αποθηκεύτηκε επιτυχώς"
                self.form_name = ""
                self.form_user = ""
                self.form_key = ""
                self.form_vat = ""
                await self.load_credentials()
            else:
                self.error = f"Σφάλμα αποθήκευσης (HTTP {resp.status_code})"
        except Exception as e:
            self.error = f"Σφάλμα: {e}"
        finally:
            self.is_loading = False

    async def delete_credential(self, name: str):
        """Delete a credential via Flask API."""
        self.is_loading = True
        self.error = ""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{FLASK_API_BASE}/credentials/delete/{name}",
                    timeout=10.0,
                )
            if resp.status_code in (200, 302):
                self.success = f"Το credential '{name}' διαγράφηκε"
                await self.load_credentials()
            else:
                self.error = f"Σφάλμα διαγραφής (HTTP {resp.status_code})"
        except Exception as e:
            self.error = f"Σφάλμα: {e}"
        finally:
            self.is_loading = False

    async def set_active(self, name: str):
        """Set active credential via Flask API."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{FLASK_API_BASE}/credentials/set_active",
                    data={"name": name},
                    timeout=10.0,
                )
            if resp.status_code in (200, 302):
                self.active_credential_name = name
                self.success = f"Ενεργό credential: {name}"
            else:
                self.error = f"Σφάλμα (HTTP {resp.status_code})"
        except Exception as e:
            self.error = f"Σφάλμα: {e}"
