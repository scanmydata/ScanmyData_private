import reflex as rx
from ..components.layout import page_layout, page_header


@rx.page(route="/login", title="ScanmyData - Σύνδεση")
def login_page() -> rx.Component:
    return page_layout(
        rx.center(
            rx.box(
                rx.vstack(
                    rx.image(src="/favicon.ico", width="64px", height="64px", border_radius="12px"),
                    rx.heading("ScanmyData", size="7", font_weight="700"),
                    rx.text("Σύνδεση στο σύστημα διαχείρισης MYDATA", color="gray.500", text_align="center"),
                    rx.divider(),
                    rx.link(
                        rx.button(
                            rx.hstack(
                                rx.icon("log-in", size=18),
                                rx.text("Σύνδεση μέσω Flask (Υφιστάμενο σύστημα)"),
                                spacing="2",
                            ),
                            color_scheme="blue",
                            size="3",
                            width="100%",
                        ),
                        href="http://localhost:5000/firebase_auth/login",
                        width="100%",
                    ),
                    rx.text(
                        "Το Reflex frontend επικοινωνεί με το Flask backend για authentication.",
                        color="gray.400",
                        font_size="0.8rem",
                        text_align="center",
                    ),
                    spacing="4",
                    align="center",
                    width="100%",
                ),
                padding="40px",
                bg=rx.color_mode_cond("white", "gray.800"),
                border_radius="16px",
                border="1px solid",
                border_color=rx.color_mode_cond("gray.200", "gray.700"),
                box_shadow="0 8px 32px rgba(0,0,0,0.1)",
                width=["90%", "420px"],
            ),
            min_height="100vh",
            bg=rx.color_mode_cond("gray.50", "gray.950"),
        )
    )
