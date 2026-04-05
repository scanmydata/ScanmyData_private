import reflex as rx


def theme_toggle_button() -> rx.Component:
    """Dark/light theme toggle button."""
    return rx.color_mode.button(
        border_radius="full",
        border="1px solid",
        border_color=rx.color_mode_cond("gray.200", "gray.600"),
        bg=rx.color_mode_cond("white", "gray.800"),
        _hover={"bg": rx.color_mode_cond("gray.50", "gray.700")},
    )


def nav_link(text: str, href: str, icon: str = "") -> rx.Component:
    """A single navigation link item."""
    return rx.link(
        rx.hstack(
            rx.icon(icon, size=16) if icon else rx.fragment(),
            rx.text(text, font_size="0.9rem", font_weight="500"),
            spacing="2",
            align="center",
        ),
        href=href,
        padding="8px 12px",
        border_radius="8px",
        color=rx.color_mode_cond("gray.700", "gray.200"),
        _hover={
            "bg": rx.color_mode_cond("gray.100", "gray.700"),
            "color": rx.color_mode_cond("gray.900", "white"),
            "text_decoration": "none",
        },
        text_decoration="none",
    )


def navbar() -> rx.Component:
    """Top navigation bar."""
    return rx.box(
        rx.hstack(
            # Logo / Brand
            rx.link(
                rx.hstack(
                    rx.image(src="/favicon.ico", width="28px", height="28px", border_radius="6px"),
                    rx.text(
                        "ScanmyData",
                        font_size="1.1rem",
                        font_weight="700",
                        color=rx.color_mode_cond("gray.900", "white"),
                    ),
                    spacing="2",
                    align="center",
                ),
                href="/",
                text_decoration="none",
            ),
            rx.spacer(),
            # Navigation links
            rx.hstack(
                nav_link("Credentials", "/credentials", "key"),
                nav_link("Λήψη (Fetch)", "/fetch", "download"),
                nav_link("Αναζήτηση", "/search", "search"),
                nav_link("Λίστα", "/list", "list"),
                spacing="1",
                display=["none", "none", "flex"],
            ),
            rx.spacer(),
            # Theme toggle
            theme_toggle_button(),
            justify="between",
            align="center",
            width="100%",
            padding="0 16px",
        ),
        position="sticky",
        top="0",
        z_index="100",
        bg=rx.color_mode_cond("white", "gray.900"),
        border_bottom="1px solid",
        border_color=rx.color_mode_cond("gray.200", "gray.700"),
        height="60px",
        width="100%",
        box_shadow="0 1px 3px rgba(0,0,0,0.07)",
    )
