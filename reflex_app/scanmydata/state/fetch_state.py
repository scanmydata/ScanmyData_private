import reflex as rx
import httpx
from typing import Optional

FLASK_API_BASE = "http://localhost:5000"


class FetchState(rx.State):
    """Manages the MYDATA fetch page state."""
    date_from: str = ""
    date_to: str = ""
    is_fetching: bool = False
    fetch_progress: int = 0
    fetch_message: str = ""
    fetch_status: str = ""  # "idle" | "running" | "done" | "error"
    error: str = ""
    success: str = ""
    active_credential_name: str = ""
    last_fetch_date: str = ""

    def set_date_from(self, value: str):
        self.date_from = value

    def set_date_to(self, value: str):
        self.date_to = value

    async def load_initial_data(self):
        """Load last fetch date and active credential from Flask API."""
        self.error = ""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{FLASK_API_BASE}/api/last_fetch_date",
                    timeout=5.0,
                )
            if resp.status_code == 200:
                data = resp.json()
                self.last_fetch_date = data.get("last_fetch_date_display") or ""
                self.active_credential_name = data.get("active_credential") or ""
        except Exception:
            pass

    async def start_fetch(self):
        """Trigger invoice fetch via Flask API."""
        if not self.date_from or not self.date_to:
            self.error = "Παρακαλώ συμπλήρωσε ημερομηνίες"
            return
        self.is_fetching = True
        self.fetch_status = "running"
        self.fetch_progress = 5
        self.fetch_message = "Ξεκίνησε η λήψη..."
        self.error = ""
        self.success = ""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{FLASK_API_BASE}/fetch",
                    data={
                        "date_from": self.date_from,
                        "date_to": self.date_to,
                    },
                    headers={"Accept": "application/json"},
                    timeout=30.0,
                )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    self.fetch_status = "running"
                    self.fetch_message = "Η λήψη εκτελείται στο παρασκήνιο..."
                else:
                    self.error = data.get("error", "Άγνωστο σφάλμα")
                    self.fetch_status = "error"
                    self.is_fetching = False
            else:
                self.error = f"Σφάλμα HTTP {resp.status_code}"
                self.fetch_status = "error"
                self.is_fetching = False
        except Exception as e:
            self.error = f"Σφάλμα σύνδεσης: {e}"
            self.fetch_status = "error"
            self.is_fetching = False

    async def poll_progress(self):
        """Poll fetch progress from Flask API."""
        if self.fetch_status != "running":
            return
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{FLASK_API_BASE}/api/fetch_progress",
                    timeout=5.0,
                )
            if resp.status_code == 200:
                data = resp.json()
                self.fetch_progress = data.get("pct", self.fetch_progress)
                self.fetch_message = data.get("msg", self.fetch_message)
                status = data.get("status", "running")
                if status in ("done", "completed", "error"):
                    self.fetch_status = status
                    self.is_fetching = False
                    if status == "done":
                        self.success = "Η λήψη ολοκληρώθηκε επιτυχώς!"
                    else:
                        self.error = data.get("error", "Σφάλμα κατά τη λήψη")
        except Exception:
            pass
