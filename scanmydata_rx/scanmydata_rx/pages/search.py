"""MARK search page."""
import httpx
import reflex as rx

from ..components.layout import card, layout
from ..state import GlobalState

FLASK_BASE = "http://localhost:5000"


class SearchState(GlobalState):
    mark: str = ""
    search_vat: str = ""
    date_from: str = ""
    date_to: str = ""
    amount_min: str = ""
    amount_max: str = ""
    search_loading: bool = False
    search_error: str = ""
    results: list[dict] = []
    result_columns: list[str] = []
    total_results: int = 0

    async def on_load(self):
        await self.check_auth()
        if not self.is_authenticated:
            return rx.redirect("/firebase-auth/login")

    async def do_search(self):
        self.search_error = ""
        self.results = []
        self.search_loading = True
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{FLASK_BASE}/search",
                    data={
                        "mark": self.mark,
                        "vat": self.search_vat,
                        "date_from": self.date_from,
                        "date_to": self.date_to,
                        "amount_min": self.amount_min,
                        "amount_max": self.amount_max,
                    },
                )
                if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                    data = resp.json()
                    self.results = data.get("results", [])
                    self.total_results = len(self.results)
                    if self.results:
                        self.result_columns = list(self.results[0].keys())
                elif resp.status_code == 200:
                    self.search_error = "Η αναζήτηση δεν επέστρεψε JSON δεδομένα."
                else:
                    self.search_error = "Σφάλμα κατά την αναζήτηση."
        except Exception as e:
            self.search_error = f"Σφάλμα: {e}"
        finally:
            self.search_loading = False

    def clear_search(self):
        self.mark = ""
        self.search_vat = ""
        self.date_from = ""
        self.date_to = ""
        self.amount_min = ""
        self.amount_max = ""
        self.results = []
        self.result_columns = []
        self.search_error = ""
        self.total_results = 0


def _result_row(row: dict) -> rx.Component:
    return rx.table.row(
        rx.foreach(
            SearchState.result_columns,
            lambda col: rx.table.cell(
                rx.text(row[col], font_size="12px", white_space="nowrap"),
                padding="6px 10px",
            ),
        ),
        _hover={"background_color": rx.color_mode_cond("#f9fafb", "#374151")},
    )


@rx.page(route="/search", title="Αναζήτηση MARK - ScanmyData", on_load=SearchState.on_load)
def search_page() -> rx.Component:
    return layout(
        rx.vstack(
            rx.heading("🔍 Αναζήτηση MARK", size="5", color=rx.color_mode_cond("#1f2937", "#f9fafb")),
            rx.text("Αναζητήστε παραστατικά με φίλτρα.", font_size="13px", color=rx.color_mode_cond("#6b7280", "#9ca3af")),
            rx.cond(SearchState.search_error != "", rx.box(SearchState.search_error, background="#fee2e2", color="#7f1d1d", padding="10px", border_radius="8px", width="100%", font_size="14px"), rx.fragment()),
            card(
                rx.vstack(
                    rx.heading("Φίλτρα Αναζήτησης", size="3", color=rx.color_mode_cond("#374151", "#d1d5db")),
                    rx.hstack(
                        rx.vstack(rx.text("MARK", font_size="13px", font_weight="500"), rx.input(placeholder="Αριθμός MARK", value=SearchState.mark, on_change=SearchState.set_mark, width="100%", border_radius="8px"), width="100%", spacing="1"),
                        rx.vstack(rx.text("ΑΦΜ", font_size="13px", font_weight="500"), rx.input(placeholder="ΑΦΜ", value=SearchState.search_vat, on_change=SearchState.set_search_vat, width="100%", border_radius="8px"), width="100%", spacing="1"),
                        width="100%",
                        spacing="4",
                        flex_wrap="wrap",
                    ),
                    rx.hstack(
                        rx.vstack(rx.text("Από (dd/mm/yyyy)", font_size="13px", font_weight="500"), rx.input(placeholder="dd/mm/yyyy", value=SearchState.date_from, on_change=SearchState.set_date_from, width="100%", border_radius="8px"), width="100%", spacing="1"),
                        rx.vstack(rx.text("Έως (dd/mm/yyyy)", font_size="13px", font_weight="500"), rx.input(placeholder="dd/mm/yyyy", value=SearchState.date_to, on_change=SearchState.set_date_to, width="100%", border_radius="8px"), width="100%", spacing="1"),
                        width="100%",
                        spacing="4",
                        flex_wrap="wrap",
                    ),
                    rx.hstack(
                        rx.vstack(rx.text("Ποσό Από (€)", font_size="13px", font_weight="500"), rx.input(placeholder="0.00", type="number", value=SearchState.amount_min, on_change=SearchState.set_amount_min, width="100%", border_radius="8px"), width="100%", spacing="1"),
                        rx.vstack(rx.text("Ποσό Έως (€)", font_size="13px", font_weight="500"), rx.input(placeholder="9999.99", type="number", value=SearchState.amount_max, on_change=SearchState.set_amount_max, width="100%", border_radius="8px"), width="100%", spacing="1"),
                        width="100%",
                        spacing="4",
                        flex_wrap="wrap",
                    ),
                    rx.hstack(
                        rx.button(
                            rx.cond(SearchState.search_loading, rx.hstack(rx.spinner(size="2"), rx.text("Αναζήτηση..."), spacing="2"), rx.text("🔍 Αναζήτηση")),
                            on_click=SearchState.do_search,
                            background_color="#0284c7",
                            color="white",
                            border_radius="8px",
                            font_weight="600",
                            disabled=SearchState.search_loading,
                            cursor="pointer",
                        ),
                        rx.button(
                            "🗑️ Καθαρισμός",
                            on_click=SearchState.clear_search,
                            variant="soft",
                            border_radius="8px",
                        ),
                        spacing="3",
                        align="center",
                        padding_top="8px",
                    ),
                    spacing="4",
                    width="100%",
                ),
                width="100%",
            ),
            rx.cond(
                SearchState.results.length() > 0,
                rx.vstack(
                    rx.hstack(
                        rx.heading("Αποτελέσματα", size="3"),
                        rx.badge(f"{SearchState.total_results} εγγραφές", color_scheme="blue", variant="soft"),
                        spacing="3",
                        align="center",
                    ),
                    card(
                        rx.scroll_area(
                            rx.table.root(
                                rx.table.header(
                                    rx.table.row(
                                        rx.foreach(
                                            SearchState.result_columns,
                                            lambda col: rx.table.column_header_cell(col, font_size="12px", white_space="nowrap", background_color="#0284c7", color="white", padding="6px 10px"),
                                        )
                                    )
                                ),
                                rx.table.body(rx.foreach(SearchState.results, _result_row)),
                                width="100%",
                                variant="surface",
                            ),
                            type="always",
                            scrollbars="both",
                            max_height="520px",
                        ),
                        width="100%",
                        padding="0",
                        overflow="hidden",
                    ),
                    width="100%",
                    spacing="3",
                ),
                rx.cond(
                    SearchState.search_loading,
                    rx.center(rx.spinner(size="3"), padding="40px"),
                    rx.fragment(),
                ),
            ),
            width="100%",
            spacing="4",
        )
    )
