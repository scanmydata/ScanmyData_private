import reflex as rx
import httpx
from typing import List, Dict, Any

FLASK_API_BASE = "http://localhost:5000"


class ListState(rx.State):
    """Manages the invoice list state."""
    invoices: List[Dict[str, Any]] = []
    is_loading: bool = False
    error: str = ""
    search_term: str = ""
    active_credential_name: str = ""
    total_count: int = 0

    async def load_invoices(self):
        """Load invoices from Flask API."""
        self.is_loading = True
        self.error = ""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{FLASK_API_BASE}/api/invoices",
                    timeout=15.0,
                )
            if resp.status_code == 200:
                data = resp.json()
                self.invoices = data.get("invoices", [])
                self.total_count = len(self.invoices)
                self.active_credential_name = data.get("active_credential", "")
            else:
                self.error = f"Σφάλμα φόρτωσης (HTTP {resp.status_code})"
        except Exception as e:
            self.error = f"Σφάλμα σύνδεσης: {e}"
        finally:
            self.is_loading = False

    def set_search_term(self, value: str):
        self.search_term = value

    @rx.var
    def filtered_invoices(self) -> List[Dict[str, Any]]:
        if not self.search_term:
            return self.invoices
        term = self.search_term.lower()
        return [
            inv for inv in self.invoices
            if any(term in str(v).lower() for v in inv.values())
        ]
