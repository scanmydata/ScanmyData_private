import reflex as rx
from ..components.layout import page_layout, page_header


@rx.page(route="/", title="ScanmyData - Αρχική")
def index() -> rx.Component:
    return page_layout(
        page_header("ScanmyData", "Σύστημα διαχείρισης παραστατικών MYDATA"),
        rx.grid(
            rx.box(
                rx.vstack(
                    rx.icon("key", size=32, color="blue.500"),
                    rx.heading("Credentials", size="5"),
                    rx.text("Διαχειριστείτε τα διαπιστευτήρια AADE", color="gray.500", font_size="0.85rem"),
                    rx.link(rx.button("Μετάβαση →", variant="soft"), href="/credentials"),
                    spacing="3",
                    align="center",
                ),
                padding="24px",
                bg=rx.color_mode_cond("white", "gray.800"),
                border_radius="12px",
                border="1px solid",
                border_color=rx.color_mode_cond("gray.200", "gray.700"),
                box_shadow="0 2px 8px rgba(0,0,0,0.05)",
                _hover={"box_shadow": "0 4px 16px rgba(0,0,0,0.1)"},
                transition="all 0.2s ease",
                text_align="center",
            ),
            rx.box(
                rx.vstack(
                    rx.icon("download", size=32, color="green.500"),
                    rx.heading("Λήψη Παραστατικών", size="5"),
                    rx.text("Λήψη από MYDATA AADE", color="gray.500", font_size="0.85rem"),
                    rx.link(rx.button("Μετάβαση →", variant="soft", color_scheme="green"), href="/fetch"),
                    spacing="3",
                    align="center",
                ),
                padding="24px",
                bg=rx.color_mode_cond("white", "gray.800"),
                border_radius="12px",
                border="1px solid",
                border_color=rx.color_mode_cond("gray.200", "gray.700"),
                box_shadow="0 2px 8px rgba(0,0,0,0.05)",
                _hover={"box_shadow": "0 4px 16px rgba(0,0,0,0.1)"},
                transition="all 0.2s ease",
                text_align="center",
            ),
            rx.box(
                rx.vstack(
                    rx.icon("search", size=32, color="purple.500"),
                    rx.heading("Αναζήτηση MARK", size="5"),
                    rx.text("Αναζήτηση παραστατικού με MARK", color="gray.500", font_size="0.85rem"),
                    rx.link(rx.button("Μετάβαση →", variant="soft", color_scheme="purple"), href="/search"),
                    spacing="3",
                    align="center",
                ),
                padding="24px",
                bg=rx.color_mode_cond("white", "gray.800"),
                border_radius="12px",
                border="1px solid",
                border_color=rx.color_mode_cond("gray.200", "gray.700"),
                box_shadow="0 2px 8px rgba(0,0,0,0.05)",
                _hover={"box_shadow": "0 4px 16px rgba(0,0,0,0.1)"},
                transition="all 0.2s ease",
                text_align="center",
            ),
            rx.box(
                rx.vstack(
                    rx.icon("list", size=32, color="orange.500"),
                    rx.heading("Λίστα Παραστατικών", size="5"),
                    rx.text("Προβολή και εξαγωγή παραστατικών", color="gray.500", font_size="0.85rem"),
                    rx.link(rx.button("Μετάβαση →", variant="soft", color_scheme="orange"), href="/list"),
                    spacing="3",
                    align="center",
                ),
                padding="24px",
                bg=rx.color_mode_cond("white", "gray.800"),
                border_radius="12px",
                border="1px solid",
                border_color=rx.color_mode_cond("gray.200", "gray.700"),
                box_shadow="0 2px 8px rgba(0,0,0,0.05)",
                _hover={"box_shadow": "0 4px 16px rgba(0,0,0,0.1)"},
                transition="all 0.2s ease",
                text_align="center",
            ),
            columns=rx.breakpoints(initial="1", sm="2", md="4"),
            spacing="4",
        ),
    )
