"""Invoice list page (/list) — view, filter, delete, export invoices."""
import os
import httpx
import reflex as rx

from ..components.layout import card, layout
from ..state import GlobalState

FLASK_BASE = os.getenv("FLASK_API_URL", f"http://localhost:{os.getenv('PORT', '5001')}")


class ListState(GlobalState):
    invoices: list[dict] = []
    loading: bool = False
    error: str = ""
    selected_ids: list[str] = []
    search_term: str = ""
    page_length: int = 25
    file_exists: bool = False

    async def on_load(self):
        await self.check_auth()
        if not self.is_authenticated:
            return rx.redirect("/firebase-auth/login")
        await self.load_invoices()

    async def load_invoices(self):
        self.loading = True
        self.error = ""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(f"{FLASK_BASE}/list")
                if resp.status_code == 200:
                    ct = resp.headers.get("content-type", "")
                    if "json" in ct:
                        data = resp.json()
                        self.invoices = data.get("invoices", [])
                        self.file_exists = data.get("file_exists", False)
        except Exception as e:
            self.error = f"Σφάλμα φόρτωσης: {e}"
        finally:
            self.loading = False

    def set_search(self, val: str):
        self.search_term = val

    def toggle_select(self, inv_id: str):
        if inv_id in self.selected_ids:
            self.selected_ids = [i for i in self.selected_ids if i != inv_id]
        else:
            self.selected_ids = self.selected_ids + [inv_id]

    def update_page_length(self, v: str):
        try:
            self.page_length = int(v)
        except (ValueError, TypeError):
            pass

    async def delete_selected(self):
        if not self.selected_ids:
            return
        self.loading = True
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{FLASK_BASE}/delete",
                    data={"ids": ",".join(self.selected_ids)},
                )
                if resp.status_code == 200:
                    self.selected_ids = []
                    await self.load_invoices()
                else:
                    self.error = "Σφάλμα διαγραφής."
        except Exception as e:
            self.error = f"Σφάλμα: {e}"
        finally:
            self.loading = False


def _table_header() -> rx.Component:
    return rx.table.header(
        rx.table.row(
            rx.table.column_header_cell(""),
            rx.table.column_header_cell("MARK"),
            rx.table.column_header_cell("Ημερομηνία"),
            rx.table.column_header_cell("Τύπος"),
            rx.table.column_header_cell("ΑΦΜ Εκδότη"),
            rx.table.column_header_cell("Εκδότης"),
            rx.table.column_header_cell("Καθ. Αξία"),
            rx.table.column_header_cell("ΦΠΑ"),
            rx.table.column_header_cell("Σύνολο"),
            background=rx.color_mode_cond("#f3f4f6", "#374151"),
        )
    )


def _invoice_row(inv: dict) -> rx.Component:
    return rx.table.row(
        rx.table.cell(
            rx.checkbox(
                checked=ListState.selected_ids.contains(inv["mark"]),
                on_change=ListState.toggle_select(inv["mark"]),
            )
        ),
        rx.table.cell(
            rx.text(inv["mark"], font_size="12px", color=rx.color_mode_cond("#0369a1", "#60a5fa"))
        ),
        rx.table.cell(rx.text(inv["issue_date"], font_size="12px")),
        rx.table.cell(rx.text(inv["inv_type"], font_size="12px")),
        rx.table.cell(rx.text(inv["counterpart_vat"], font_size="12px")),
        rx.table.cell(rx.text(inv["counterpart_name"], font_size="12px", max_width="180px")),
        rx.table.cell(rx.text(inv["net_value"], font_size="12px", text_align="right")),
        rx.table.cell(rx.text(inv["vat_amount"], font_size="12px", text_align="right")),
        rx.table.cell(rx.text(inv["gross_value"], font_size="12px", text_align="right", font_weight="600")),
        _hover={"background_color": rx.color_mode_cond("#f0f9ff", "#1e3a5f")},
        border_bottom=rx.color_mode_cond("1px solid #e5e7eb", "1px solid #374151"),
    )


@rx.page(route="/list", title="Λίστα Παραστατικών - ScanmyData", on_load=ListState.on_load)
def list_invoices_page() -> rx.Component:
    return layout(
        rx.vstack(
            # Header row
            rx.hstack(
                rx.heading(
                    "Λίστα Παραστατικών",
                    size="5",
                    color=rx.color_mode_cond("#1f2937", "#f9fafb"),
                ),
                rx.spacer(),
                rx.cond(
                    ListState.file_exists,
                    rx.hstack(
                        rx.link(
                            rx.button(
                                "⬇️ Κατέβασμα .xlsx",
                                background_color="#0284c7",
                                color="white",
                                border_radius="8px",
                                cursor="pointer",
                                _hover={"background_color": "#0369a1"},
                            ),
                            href=f"{FLASK_BASE}/list?download=1",
                            is_external=True,
                        ),
                        spacing="2",
                    ),
                    rx.fragment(),
                ),
                width="100%",
                align="center",
            ),
            # Error banner
            rx.cond(
                ListState.error != "",
                rx.box(
                    ListState.error,
                    background="#fee2e2",
                    color="#7f1d1d",
                    padding="10px 14px",
                    border_radius="8px",
                    width="100%",
                ),
                rx.fragment(),
            ),
            # Toolbar
            card(
                rx.hstack(
                    rx.input(
                        placeholder="🔎 Αναζήτηση...",
                        value=ListState.search_term,
                        on_change=ListState.set_search,
                        width="260px",
                        border_radius="8px",
                    ),
                    rx.button(
                        "🗑️ Διαγραφή Επιλεγμένων",
                        on_click=ListState.delete_selected,
                        background_color="#dc2626",
                        color="white",
                        border_radius="8px",
                        cursor="pointer",
                        _hover={"background_color": "#b91c1c"},
                        disabled=ListState.loading,
                    ),
                    rx.spacer(),
                    rx.hstack(
                        rx.text("Εγγραφές/σελίδα:", font_size="14px", color=rx.color_mode_cond("#6b7280", "#9ca3af")),
                        rx.select(
                            ["10", "25", "50", "100"],
                            default_value="25",
                            on_change=ListState.update_page_length,
                            width="80px",
                        ),
                        align="center",
                        spacing="2",
                    ),
                    align="center",
                    width="100%",
                    wrap="wrap",
                    spacing="3",
                ),
                width="100%",
            ),
            # Table
            card(
                rx.cond(
                    ListState.loading,
                    rx.center(rx.spinner(size="3"), padding="40px"),
                    rx.cond(
                        ListState.invoices.length() > 0,
                        rx.box(
                            rx.table.root(
                                _table_header(),
                                rx.table.body(
                                    rx.foreach(ListState.invoices, _invoice_row)
                                ),
                                width="100%",
                                variant="surface",
                            ),
                            overflow_x="auto",
                            width="100%",
                        ),
                        rx.center(
                            rx.vstack(
                                rx.text("📭", font_size="48px"),
                                rx.text(
                                    "Δεν βρέθηκαν παραστατικά.",
                                    font_size="16px",
                                    color=rx.color_mode_cond("#6b7280", "#9ca3af"),
                                ),
                                rx.link(
                                    rx.button(
                                        "📥 Λήψη Παραστατικών",
                                        background_color="#0284c7",
                                        color="white",
                                        border_radius="8px",
                                    ),
                                    href="/fetch",
                                ),
                                align="center",
                                spacing="3",
                            ),
                            padding="40px",
                        ),
                    ),
                ),
                width="100%",
                overflow="hidden",
            ),
            width="100%",
            spacing="4",
        )
    )
