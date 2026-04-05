import reflex as rx
from ..components.layout import page_layout, page_header
from ..components.flash import flash_message
from ..state.fetch_state import FetchState


def progress_bar() -> rx.Component:
    """Animated progress bar for fetch operation."""
    return rx.box(
        rx.vstack(
            rx.hstack(
                rx.text(FetchState.fetch_message, font_size="0.9rem", font_weight="500"),
                rx.spacer(),
                rx.text(FetchState.fetch_progress.to_string() + "%", font_size="0.9rem", color="gray.500"),
                width="100%",
            ),
            rx.box(
                rx.box(
                    width=FetchState.fetch_progress.to_string() + "%",
                    height="8px",
                    bg="blue.500",
                    border_radius="full",
                    transition="width 0.5s ease",
                ),
                width="100%",
                height="8px",
                bg="gray.200",
                border_radius="full",
                overflow="hidden",
            ),
            spacing="2",
            width="100%",
        ),
        padding="16px",
        bg=rx.color_mode_cond("blue.50", "blue.900"),
        border_radius="8px",
        border="1px solid",
        border_color=rx.color_mode_cond("blue.200", "blue.700"),
        margin_bottom="16px",
    )


@rx.page(route="/fetch", title="ScanmyData - Λήψη Παραστατικών", on_load=FetchState.load_initial_data)
def fetch_page() -> rx.Component:
    return page_layout(
        page_header("Λήψη Παραστατικών", "Λήψη παραστατικών από MYDATA AADE API"),
        flash_message(FetchState.error, "error"),
        flash_message(FetchState.success, "success"),
        rx.cond(
            FetchState.is_fetching,
            progress_bar(),
            rx.fragment(),
        ),
        rx.box(
            rx.vstack(
                # Active credential badge
                rx.cond(
                    FetchState.active_credential_name != "",
                    rx.hstack(
                        rx.text("Ενεργό Credential:", font_size="0.85rem", color="gray.500"),
                        rx.badge(FetchState.active_credential_name, color_scheme="blue", variant="soft"),
                        spacing="2",
                    ),
                    rx.text("Δεν έχει επιλεγεί credential", color="red.500", font_size="0.85rem"),
                ),
                # Last fetch date
                rx.cond(
                    FetchState.last_fetch_date != "",
                    rx.hstack(
                        rx.text("Τελευταία λήψη:", font_size="0.85rem", color="gray.500"),
                        rx.text(FetchState.last_fetch_date, font_size="0.85rem", font_weight="600"),
                        spacing="2",
                    ),
                    rx.fragment(),
                ),
                rx.divider(),
                # Date pickers
                rx.grid(
                    rx.vstack(
                        rx.text("Από ημερομηνία", font_size="0.85rem", font_weight="600", color="gray.600"),
                        rx.input(
                            type="date",
                            value=FetchState.date_from,
                            on_change=FetchState.set_date_from,
                            width="100%",
                        ),
                        spacing="1",
                    ),
                    rx.vstack(
                        rx.text("Έως ημερομηνία", font_size="0.85rem", font_weight="600", color="gray.600"),
                        rx.input(
                            type="date",
                            value=FetchState.date_to,
                            on_change=FetchState.set_date_to,
                            width="100%",
                        ),
                        spacing="1",
                    ),
                    columns="2",
                    spacing="4",
                    width="100%",
                ),
                # Submit button
                rx.button(
                    rx.hstack(
                        rx.icon("download", size=16),
                        rx.text("Εκκίνηση Λήψης"),
                        spacing="2",
                    ),
                    on_click=FetchState.start_fetch,
                    color_scheme="blue",
                    size="3",
                    loading=FetchState.is_fetching,
                    disabled=FetchState.is_fetching,
                    width="100%",
                ),
                spacing="4",
                width="100%",
            ),
            padding="24px",
            bg=rx.color_mode_cond("white", "gray.800"),
            border_radius="12px",
            border="1px solid",
            border_color=rx.color_mode_cond("gray.200", "gray.700"),
            box_shadow="0 2px 8px rgba(0,0,0,0.05)",
        ),
    )
