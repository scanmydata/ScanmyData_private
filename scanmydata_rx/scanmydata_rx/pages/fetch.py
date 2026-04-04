"""Fetch invoices page."""
import httpx
import reflex as rx

from ..components.layout import card, layout
from ..state import GlobalState

FLASK_BASE = "http://localhost:5000"


class FetchState(GlobalState):
    credentials_list: list[dict] = []
    selected_credential: str = ""
    vat_number: str = ""
    date_from: str = ""
    date_to: str = ""
    last_fetch_date: str = ""
    fetch_loading: bool = False
    fetch_error: str = ""
    fetch_message: str = ""
    preview_rows: list[dict] = []
    preview_columns: list[str] = []
    show_preview: bool = False

    async def on_load(self):
        await self.check_auth()
        if not self.is_authenticated:
            return rx.redirect("/firebase-auth/login")
        await self._load_fetch_page()

    async def _load_fetch_page(self):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{FLASK_BASE}/fetch")
                if resp.status_code == 200:
                    # parse JSON context if available
                    data = resp.json() if "json" in resp.headers.get("content-type", "") else {}
                    self.credentials_list = data.get("credentials", [])
                    self.selected_credential = data.get("active_credential", "")
                    self.vat_number = data.get("vat_number", "")
                    self.date_from = data.get("default_from", "")
                    self.date_to = data.get("default_to", "")
                    self.last_fetch_date = data.get("last_fetch_date_display", "")
        except Exception:
            pass

    async def load_credentials(self):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{FLASK_BASE}/credentials")
                if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                    data = resp.json()
                    self.credentials_list = data.get("credentials", [])
        except Exception:
            pass

    async def do_fetch(self):
        self.fetch_error = ""
        self.fetch_message = ""
        self.fetch_loading = True
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{FLASK_BASE}/fetch",
                    data={
                        "use_credential": self.selected_credential,
                        "vat_number": self.vat_number,
                        "date_from": self.date_from,
                        "date_to": self.date_to,
                    },
                )
                if resp.status_code == 200:
                    ct = resp.headers.get("content-type", "")
                    if "json" in ct:
                        data = resp.json()
                        self.fetch_message = data.get("message", "Η ανάκτηση ολοκληρώθηκε.")
                        preview = data.get("preview", [])
                        if preview:
                            self.preview_rows = preview[:40]
                            self.preview_columns = list(preview[0].keys()) if preview else []
                        self.show_preview = True
                    else:
                        self.fetch_message = "Η ανάκτηση ολοκληρώθηκε."
                else:
                    self.fetch_error = "Σφάλμα κατά την ανάκτηση παραστατικών."
        except Exception as e:
            self.fetch_error = f"Σφάλμα: {e}"
        finally:
            self.fetch_loading = False

    def toggle_preview(self):
        self.show_preview = not self.show_preview


def _preview_table() -> rx.Component:
    return rx.cond(
        FetchState.show_preview,
        rx.box(
            rx.hstack(
                rx.heading("Preview (πρώτες 40 γραμμές)", size="3"),
                rx.spacer(),
                rx.text("Οριζόντιο scroll διαθέσιμο", font_size="12px", color=rx.color_mode_cond("#6b7280", "#9ca3af")),
                width="100%",
                align="center",
                margin_bottom="12px",
            ),
            rx.cond(
                FetchState.preview_rows.length() > 0,
                rx.scroll_area(
                    rx.table.root(
                        rx.table.header(
                            rx.table.row(
                                rx.foreach(
                                    FetchState.preview_columns,
                                    lambda col: rx.table.column_header_cell(
                                        col,
                                        font_size="12px",
                                        white_space="nowrap",
                                        background_color="#0284c7",
                                        color="white",
                                        padding="6px 10px",
                                    ),
                                )
                            )
                        ),
                        rx.table.body(
                            rx.foreach(
                                FetchState.preview_rows,
                                lambda row: rx.table.row(
                                    rx.foreach(
                                        FetchState.preview_columns,
                                        lambda col: rx.table.cell(
                                            rx.text(row[col], font_size="12px", white_space="nowrap"),
                                            padding="6px 10px",
                                        ),
                                    )
                                ),
                            )
                        ),
                        width="100%",
                        variant="surface",
                    ),
                    type="always",
                    scrollbars="horizontal",
                    max_height="520px",
                ),
                rx.text("Δεν υπάρχουν δεδομένα preview.", font_size="14px", color="#6b7280", padding="20px", text_align="center"),
            ),
            background_color=rx.color_mode_cond("#ffffff", "#1f2937"),
            border_radius="12px",
            padding="16px",
            border=rx.color_mode_cond("1px solid #e5e7eb", "1px solid #374151"),
            margin_top="16px",
        ),
        rx.fragment(),
    )


@rx.page(route="/fetch", title="Λήψη Παραστατικών - ScanmyData", on_load=FetchState.on_load)
def fetch_page() -> rx.Component:
    return layout(
        rx.vstack(
            # Wait overlay
            rx.cond(
                FetchState.fetch_loading,
                rx.box(
                    rx.vstack(
                        rx.spinner(size="3"),
                        rx.text("Λήψη παραστατικών...", font_weight="600", font_size="16px"),
                        rx.text("Παρακαλώ περιμένετε — η διαδικασία μπορεί να διαρκέσει.", font_size="13px", color="#6b7280"),
                        spacing="3",
                        align="center",
                        background_color="white",
                        border_radius="12px",
                        padding="32px 48px",
                        box_shadow="0 8px 24px rgba(0,0,0,0.15)",
                    ),
                    position="fixed",
                    top="0",
                    left="0",
                    width="100%",
                    height="100%",
                    background_color="rgba(0,0,0,0.5)",
                    z_index="50",
                    display="flex",
                    align_items="center",
                    justify_content="center",
                ),
                rx.fragment(),
            ),
            rx.hstack(
                rx.vstack(
                    rx.heading("Λήψη Παραστατικών MYDATA", size="5", color=rx.color_mode_cond("#1f2937", "#f9fafb")),
                    rx.text("Λήψη παραστατικών για διάστημα — preview των πρώτων 40 εγγραφών.", font_size="13px", color=rx.color_mode_cond("#6b7280", "#9ca3af")),
                    spacing="1",
                    align="start",
                ),
                width="100%",
                margin_bottom="16px",
            ),
            rx.cond(
                FetchState.fetch_error != "",
                rx.box(FetchState.fetch_error, background="#fee2e2", color="#7f1d1d", padding="10px 14px", border_radius="8px", width="100%", font_size="14px", margin_bottom="12px"),
                rx.fragment(),
            ),
            rx.cond(
                FetchState.fetch_message != "",
                rx.box(FetchState.fetch_message, background="#ecfdf5", color="#065f46", padding="10px 14px", border_radius="8px", width="100%", font_size="14px", margin_bottom="12px"),
                rx.fragment(),
            ),
            card(
                rx.vstack(
                    rx.hstack(
                        rx.vstack(
                            rx.text("Χρήση Credential", font_size="14px", font_weight="500", color=rx.color_mode_cond("#374151", "#d1d5db")),
                            rx.select(
                                rx.foreach(
                                    FetchState.credentials_list,
                                    lambda c: rx.option(c["name"] + rx.cond(c["vat"] != "", " (" + c["vat"] + ")", ""), value=c["name"]),
                                ),
                                placeholder="-- Επιλογή --",
                                value=FetchState.selected_credential,
                                on_change=FetchState.set_selected_credential,
                                width="100%",
                            ),
                            width="100%",
                            spacing="1",
                        ),
                        rx.vstack(
                            rx.text("ΑΦΜ Πελάτη", font_size="14px", font_weight="500", color=rx.color_mode_cond("#374151", "#d1d5db")),
                            rx.input(
                                placeholder="π.χ. 802576637",
                                value=FetchState.vat_number,
                                on_change=FetchState.set_vat_number,
                                width="100%",
                                border_radius="8px",
                            ),
                            width="100%",
                            spacing="1",
                        ),
                        rx.vstack(
                            rx.text("Τελευταία Λήψη", font_size="14px", font_weight="500", color=rx.color_mode_cond("#374151", "#d1d5db")),
                            rx.text(
                                rx.cond(FetchState.last_fetch_date != "", FetchState.last_fetch_date, "—"),
                                font_size="18px",
                                font_weight="700",
                                color=rx.color_mode_cond("#1f2937", "#f9fafb"),
                            ),
                            width="100%",
                            spacing="1",
                            align="center",
                        ),
                        width="100%",
                        spacing="4",
                        flex_wrap="wrap",
                        align="end",
                    ),
                    rx.hstack(
                        rx.vstack(
                            rx.text("Από (dd/mm/yyyy)", font_size="14px", font_weight="500", color=rx.color_mode_cond("#374151", "#d1d5db")),
                            rx.input(
                                placeholder="dd/mm/yyyy",
                                value=FetchState.date_from,
                                on_change=FetchState.set_date_from,
                                width="100%",
                                border_radius="8px",
                            ),
                            width="100%",
                            spacing="1",
                        ),
                        rx.vstack(
                            rx.text("Έως (dd/mm/yyyy)", font_size="14px", font_weight="500", color=rx.color_mode_cond("#374151", "#d1d5db")),
                            rx.input(
                                placeholder="dd/mm/yyyy",
                                value=FetchState.date_to,
                                on_change=FetchState.set_date_to,
                                width="100%",
                                border_radius="8px",
                            ),
                            width="100%",
                            spacing="1",
                        ),
                        width="100%",
                        spacing="4",
                        flex_wrap="wrap",
                    ),
                    rx.hstack(
                        rx.button(
                            "🚀 Ανάκτηση",
                            on_click=FetchState.do_fetch,
                            background_color="#0284c7",
                            color="white",
                            border_radius="8px",
                            font_weight="600",
                            padding="9px 18px",
                            disabled=FetchState.fetch_loading,
                            cursor="pointer",
                        ),
                        rx.button(
                            rx.cond(FetchState.show_preview, "Απόκρυψη Preview", "Εμφάνιση Preview"),
                            on_click=FetchState.toggle_preview,
                            variant="outline",
                            border_radius="8px",
                            padding="9px 18px",
                        ),
                        rx.text("Preview: πρώτες 40 εγγραφές", font_size="13px", color=rx.color_mode_cond("#6b7280", "#9ca3af")),
                        spacing="3",
                        align="center",
                        padding_top="8px",
                    ),
                    spacing="5",
                    width="100%",
                ),
                width="100%",
            ),
            _preview_table(),
            width="100%",
            spacing="4",
        )
    )
