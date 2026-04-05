import reflex as rx


def flash_message(message: rx.Var, kind: str = "info") -> rx.Component:
    """Display a flash/notification message."""
    colors = {
        "success": ("green.50", "green.700", "green.200"),
        "error": ("red.50", "red.700", "red.200"),
        "warning": ("yellow.50", "yellow.700", "yellow.200"),
        "info": ("blue.50", "blue.700", "blue.200"),
    }
    bg, color, border = colors.get(kind, colors["info"])
    icons = {
        "success": "circle-check",
        "error": "circle-x",
        "warning": "triangle-alert",
        "info": "info",
    }
    return rx.cond(
        message != "",
        rx.box(
            rx.hstack(
                rx.icon(icons.get(kind, "info"), size=18),
                rx.text(message, font_size="0.9rem", font_weight="500"),
                spacing="2",
                align="center",
            ),
            bg=bg,
            color=color,
            border="1px solid",
            border_color=border,
            border_radius="8px",
            padding="10px 14px",
            margin_bottom="12px",
        ),
        rx.fragment(),
    )
