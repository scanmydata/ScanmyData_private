"""Flash / toast notification component."""
import reflex as rx

from ..state import GlobalState


def _flash_item(flash: dict, idx: int) -> rx.Component:
    """Render a single flash message with a dismiss button."""
    color_map = {
        "success": ("ecfdf5", "065f46"),
        "error": ("fee2e2", "7f1d1d"),
        "warning": ("fef3c7", "92400e"),
        "info": ("e0f2fe", "075985"),
    }
    # Use cond-based rendering for the four types
    return rx.box(
        rx.hstack(
            rx.text(flash["message"], flex="1", font_size="14px"),
            rx.button(
                "×",
                on_click=GlobalState.clear_flash(idx),
                background="transparent",
                border="none",
                font_size="18px",
                font_weight="bold",
                cursor="pointer",
                color="inherit",
                padding="0 4px",
            ),
            align="center",
            width="100%",
        ),
        padding="10px 14px",
        border_radius="8px",
        margin_bottom="8px",
        background_color=rx.match(
            flash["type"],
            ("success", "#ecfdf5"),
            ("error", "#fee2e2"),
            ("warning", "#fef3c7"),
            ("info", "#e0f2fe"),
            "#f3f4f6",
        ),
        color=rx.match(
            flash["type"],
            ("success", "#065f46"),
            ("error", "#7f1d1d"),
            ("warning", "#92400e"),
            ("info", "#075985"),
            "#1f2937",
        ),
        border=rx.match(
            flash["type"],
            ("success", "1px solid #a7f3d0"),
            ("error", "1px solid #fca5a5"),
            ("warning", "1px solid #fcd34d"),
            ("info", "1px solid #7dd3fc"),
            "1px solid #e5e7eb",
        ),
    )


def flash_container() -> rx.Component:
    """Render all current flash messages."""
    return rx.cond(
        GlobalState.flash_messages.length() > 0,
        rx.vstack(
            rx.foreach(
                GlobalState.flash_messages,
                lambda flash, idx: _flash_item(flash, idx),
            ),
            width="100%",
            spacing="2",
            margin_bottom="16px",
        ),
        rx.fragment(),
    )
