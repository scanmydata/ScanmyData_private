import reflex as rx
import httpx
from ..components.layout import page_layout, page_header
from ..components.flash import flash_message

FLASK_API_BASE = "http://localhost:5000"


class SearchState(rx.State):
    """State for MARK search page."""
    mark_input: str = ""
    is_searching: bool = False
    result: dict = {}
    error: str = ""

    def set_mark(self, value: str):
        self.mark_input = value

    async def search_mark(self):
        if not self.mark_input.strip():
            self.error = "Παρακαλώ εισάγετε αριθμό MARK"
            return
        self.is_searching = True
        self.error = ""
        self.result = {}
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{FLASK_API_BASE}/api/check_mark",
                    json={"mark": self.mark_input.strip()},
                    timeout=30.0,
                )
            if resp.status_code == 200:
                data = resp.json()
                self.result = data
                if not data.get("ok", True):
                    self.error = data.get("error", "Δεν βρέθηκε παραστατικό")
            else:
                self.error = f"Σφάλμα (HTTP {resp.status_code})"
        except Exception as e:
            self.error = f"Σφάλμα σύνδεσης: {e}"
        finally:
            self.is_searching = False


def result_card() -> rx.Component:
    """Display search result, referencing SearchState directly."""
    return rx.box(
        rx.heading("Αποτέλεσμα Αναζήτησης", size="4", margin_bottom="16px"),
        rx.grid(
            rx.vstack(
                rx.text("MARK", font_size="0.75rem", color="gray.500", font_weight="600"),
                rx.text(SearchState.result["MARK"], font_family="monospace", font_weight="700"),
                spacing="1",
            ),
            rx.vstack(
                rx.text("Ημερομηνία", font_size="0.75rem", color="gray.500", font_weight="600"),
                rx.text(SearchState.result["issue_date"]),
                spacing="1",
            ),
            rx.vstack(
                rx.text("ΑΦΜ Εκδότη", font_size="0.75rem", color="gray.500", font_weight="600"),
                rx.text(SearchState.result["issuer_vat"], font_family="monospace"),
                spacing="1",
            ),
            rx.vstack(
                rx.text("Συνολικό Ποσό", font_size="0.75rem", color="gray.500", font_weight="600"),
                rx.text(SearchState.result["total_amount"], color="green.600", font_weight="700"),
                spacing="1",
            ),
            rx.vstack(
                rx.text("Τύπος", font_size="0.75rem", color="gray.500", font_weight="600"),
                rx.text(SearchState.result["doc_type"]),
                spacing="1",
            ),
            columns="3",
            spacing="4",
            width="100%",
        ),
        padding="20px",
        bg=rx.color_mode_cond("green.50", "green.900"),
        border_radius="12px",
        border="1px solid",
        border_color=rx.color_mode_cond("green.200", "green.700"),
        margin_top="16px",
    )


@rx.page(route="/search", title="ScanmyData - Αναζήτηση MARK")
def search_page() -> rx.Component:
    return page_layout(
        page_header("Αναζήτηση MARK", "Αναζητήστε παραστατικό βάσει αριθμού MARK"),
        flash_message(SearchState.error, "error"),
        rx.box(
            rx.vstack(
                rx.text("Αριθμός MARK (15 ψηφία)", font_size="0.85rem", font_weight="600", color="gray.600"),
                rx.hstack(
                    rx.input(
                        placeholder="π.χ. 400001234567890",
                        value=SearchState.mark_input,
                        on_change=SearchState.set_mark,
                        max_length=15,
                        font_family="monospace",
                        font_size="1.1rem",
                        flex="1",
                    ),
                    rx.button(
                        rx.hstack(
                            rx.icon("search", size=16),
                            rx.text("Αναζήτηση"),
                            spacing="2",
                        ),
                        on_click=SearchState.search_mark,
                        color_scheme="purple",
                        loading=SearchState.is_searching,
                        disabled=SearchState.is_searching,
                    ),
                    width="100%",
                    spacing="3",
                ),
                spacing="2",
                width="100%",
            ),
            padding="24px",
            bg=rx.color_mode_cond("white", "gray.800"),
            border_radius="12px",
            border="1px solid",
            border_color=rx.color_mode_cond("gray.200", "gray.700"),
            box_shadow="0 2px 8px rgba(0,0,0,0.05)",
        ),
        rx.cond(
            SearchState.result.length() > 0,
            result_card(),
            rx.fragment(),
        ),
    )
