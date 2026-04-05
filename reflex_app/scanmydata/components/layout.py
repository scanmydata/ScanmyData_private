import reflex as rx
from .navbar import navbar


def page_layout(*children, title: str = "ScanmyData") -> rx.Component:
    """Standard page layout with navbar."""
    return rx.box(
        navbar(),
        rx.box(
            rx.container(
                *children,
                max_width="1200px",
                padding="24px 16px",
            ),
            min_height="calc(100vh - 60px)",
            bg=rx.color_mode_cond("gray.50", "gray.950"),
        ),
        font_family="'Inter', sans-serif",
    )


def page_header(title: str, subtitle: str = "") -> rx.Component:
    """Standard page header."""
    return rx.box(
        rx.vstack(
            rx.heading(
                title,
                size="7",
                color=rx.color_mode_cond("gray.900", "white"),
                font_weight="700",
            ),
            rx.cond(
                subtitle != "",
                rx.text(
                    subtitle,
                    color=rx.color_mode_cond("gray.500", "gray.400"),
                    font_size="0.9rem",
                ),
                rx.fragment(),
            ),
            spacing="1",
            align="start",
        ),
        padding="20px 0 16px 0",
        border_bottom="2px solid",
        border_color=rx.color_mode_cond("gray.200", "gray.700"),
        margin_bottom="24px",
    )
