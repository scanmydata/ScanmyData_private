"""Base page layout wrapping all pages."""
import reflex as rx

from ..state import GlobalState
from .flash import flash_container
from .navbar import navbar_with_drawer


def layout(content: rx.Component, title: str = "ScanmyData") -> rx.Component:
    """Wrap content with navbar, flash messages, and footer."""
    return rx.theme(
        rx.fragment(
            navbar_with_drawer(),
            rx.box(
                rx.box(
                    flash_container(),
                    content,
                    max_width="1100px",
                    margin="28px auto",
                    padding="0 16px",
                ),
                min_height="calc(100vh - 140px)",
                background_color=rx.color_mode_cond("#f3f4f6", "#111827"),
            ),
            rx.box(
                rx.hstack(
                    rx.text(
                        "© 2024 ScanmyData",
                        font_size="13px",
                        color=rx.color_mode_cond("#6b7280", "#9ca3af"),
                    ),
                    rx.spacer(),
                    rx.hstack(
                        rx.link("Όροι Χρήσης", href="/terms", font_size="13px", color=rx.color_mode_cond("#6b7280", "#9ca3af")),
                        rx.link("Απόρρητο", href="/privacy", font_size="13px", color=rx.color_mode_cond("#6b7280", "#9ca3af")),
                        spacing="4",
                    ),
                    width="100%",
                    align="center",
                    padding="16px 24px",
                ),
                background_color=rx.color_mode_cond("#ffffff", "#1f2937"),
                border_top=rx.color_mode_cond("1px solid #e5e7eb", "1px solid #374151"),
            ),
        ),
        appearance="inherit",
    )


def card(content: rx.Component, **style) -> rx.Component:
    """Card wrapper with light/dark background."""
    return rx.box(
        content,
        background_color=rx.color_mode_cond("#ffffff", "#1f2937"),
        border_radius="12px",
        padding="24px",
        box_shadow="0 1px 3px rgba(0,0,0,0.07)",
        border=rx.color_mode_cond("1px solid #e5e7eb", "1px solid #374151"),
        **style,
    )
