import reflex as rx
from ..components.layout import page_layout, page_header
from ..components.flash import flash_message
from ..state.list_state import ListState


def invoice_table_row(inv: dict) -> rx.Component:
    """Single invoice row."""
    return rx.table.row(
        rx.table.cell(rx.text(inv["mark"], font_family="monospace", font_size="0.85rem")),
        rx.table.cell(rx.text(inv["issue_date"], font_size="0.85rem")),
        rx.table.cell(rx.text(inv["issuer_vat"], font_family="monospace", font_size="0.85rem")),
        rx.table.cell(rx.text(inv["doc_type"], font_size="0.85rem")),
        rx.table.cell(
            rx.text(
                inv["total_amount"],
                font_weight="600",
                font_size="0.85rem",
                color="green.600",
            )
        ),
    )


@rx.page(route="/list", title="ScanmyData - Λίστα Παραστατικών", on_load=ListState.load_invoices)
def list_page() -> rx.Component:
    return page_layout(
        page_header("Λίστα Παραστατικών", "Προβολή και διαχείριση παραστατικών MYDATA"),
        flash_message(ListState.error, "error"),
        rx.hstack(
            rx.cond(
                ListState.active_credential_name != "",
                rx.hstack(
                    rx.text("Ενεργό Credential:", font_size="0.85rem", color="gray.500"),
                    rx.badge(ListState.active_credential_name, color_scheme="blue", variant="soft"),
                    spacing="2",
                ),
                rx.fragment(),
            ),
            rx.spacer(),
            rx.cond(
                ListState.total_count > 0,
                rx.text(
                    "Σύνολο: " + ListState.total_count.to_string() + " παραστατικά",
                    font_size="0.85rem",
                    color="gray.500",
                ),
                rx.fragment(),
            ),
            width="100%",
            margin_bottom="16px",
        ),
        # Search
        rx.input(
            placeholder="🔍  Αναζήτηση...",
            value=ListState.search_term,
            on_change=ListState.set_search_term,
            margin_bottom="16px",
            width=["100%", "100%", "400px"],
        ),
        rx.cond(
            ListState.is_loading,
            rx.center(
                rx.vstack(
                    rx.spinner(size="3"),
                    rx.text("Φόρτωση παραστατικών...", color="gray.500"),
                    spacing="2",
                    align="center",
                ),
                padding="60px",
            ),
            rx.cond(
                ListState.filtered_invoices.length() == 0,
                rx.center(
                    rx.vstack(
                        rx.icon("inbox", size=48, color="gray.300"),
                        rx.text("Δεν βρέθηκαν παραστατικά", color="gray.400", font_size="1rem"),
                        rx.text(
                            "Κάντε λήψη παραστατικών από τη σελίδα Fetch",
                            color="gray.400",
                            font_size="0.85rem",
                        ),
                        spacing="2",
                        align="center",
                    ),
                    padding="60px",
                ),
                rx.box(
                    rx.table.root(
                        rx.table.header(
                            rx.table.row(
                                rx.table.column_header_cell("MARK"),
                                rx.table.column_header_cell("Ημερομηνία"),
                                rx.table.column_header_cell("ΑΦΜ Εκδότη"),
                                rx.table.column_header_cell("Τύπος"),
                                rx.table.column_header_cell("Ποσό"),
                            )
                        ),
                        rx.table.body(
                            rx.foreach(
                                ListState.filtered_invoices,
                                invoice_table_row,
                            )
                        ),
                        width="100%",
                        variant="surface",
                    ),
                    overflow_x="auto",
                    border_radius="12px",
                    border="1px solid",
                    border_color=rx.color_mode_cond("gray.200", "gray.700"),
                ),
            ),
        ),
    )
