"""Top navigation bar component."""
import reflex as rx

from ..state import GlobalState
from ..styles import SKY_600


def _logo() -> rx.Component:
    return rx.link(
        rx.image(
            src=rx.color_mode_cond(
                light="/icons/scanmydata_logo_3000w.png",
                dark="/icons/scanmydata_logo_dark_3000w.png",
            ),
            height="36px",
            alt="ScanmyData",
        ),
        href="/",
    )


def _nav_link(label: str, href: str) -> rx.Component:
    return rx.link(
        label,
        href=href,
        font_size="14px",
        font_weight="500",
        color=rx.color_mode_cond("#1f2937", "#f9fafb"),
        padding="6px 10px",
        border_radius="6px",
        white_space="nowrap",
        _hover={
            "background_color": rx.color_mode_cond("#e5e7eb", "#374151"),
            "text_decoration": "none",
        },
    )


def _admin_link() -> rx.Component:
    return rx.cond(
        GlobalState.is_admin,
        rx.link(
            "⚙️ Admin",
            href="/admin/dashboard",
            font_size="14px",
            font_weight="600",
            color="#dc2626",
            padding="6px 10px",
            border_radius="6px",
            _hover={"background_color": "#fee2e2", "text_decoration": "none"},
        ),
        rx.fragment(),
    )


def _auth_nav() -> rx.Component:
    return rx.cond(
        GlobalState.is_authenticated,
        rx.hstack(
            _nav_link("📥 Λήψη Παραστατικών", "/fetch"),
            _nav_link("🔍 Αναζήτηση MARK", "/search"),
            _nav_link("🔐 Credentials", "/credentials"),
            _admin_link(),
            spacing="1",
            align="center",
        ),
        rx.link(
            "🔐 Σύνδεση",
            href="/firebase-auth/login",
            background_color=SKY_600,
            color="white",
            padding="7px 14px",
            border_radius="8px",
            font_size="14px",
            font_weight="600",
            _hover={"background_color": "#0369a1", "text_decoration": "none"},
        ),
    )


def _dark_mode_toggle() -> rx.Component:
    return rx.color_mode.button(size="2")


def _menu_button() -> rx.Component:
    return rx.button(
        "☰ Μενού",
        on_click=GlobalState.toggle_side_menu,
        variant="ghost",
        font_size="14px",
        cursor="pointer",
        color=rx.color_mode_cond("#1f2937", "#f9fafb"),
    )


def _active_badges() -> rx.Component:
    return rx.cond(
        GlobalState.is_authenticated,
        rx.hstack(
            rx.cond(
                GlobalState.active_credential != "",
                rx.badge(
                    rx.hstack(
                        rx.text("👤", font_size="12px"),
                        rx.text(GlobalState.active_credential, font_size="12px"),
                        spacing="1",
                    ),
                    variant="soft",
                    color_scheme="blue",
                    border_radius="20px",
                    padding="4px 10px",
                ),
                rx.fragment(),
            ),
            rx.cond(
                GlobalState.active_group != "",
                rx.badge(
                    rx.hstack(
                        rx.text("👥", font_size="12px"),
                        rx.text(GlobalState.active_group, font_size="12px"),
                        spacing="1",
                    ),
                    variant="soft",
                    color_scheme="green",
                    border_radius="20px",
                    padding="4px 10px",
                ),
                rx.fragment(),
            ),
            rx.cond(
                GlobalState.available_years.length() > 0,
                rx.select(
                    GlobalState.available_years,
                    value=GlobalState.active_year,
                    on_change=GlobalState.set_fiscal_year,
                    size="1",
                    width="100px",
                ),
                rx.fragment(),
            ),
            spacing="2",
            align="center",
            flex_wrap="wrap",
        ),
        rx.fragment(),
    )


def _side_drawer() -> rx.Component:
    return rx.drawer.root(
        rx.drawer.overlay(z_index="5"),
        rx.drawer.content(
            rx.vstack(
                rx.hstack(
                    rx.heading("Μενού", size="4"),
                    rx.spacer(),
                    rx.icon_button(
                        rx.icon("x"),
                        on_click=GlobalState.close_side_menu,
                        variant="ghost",
                        size="2",
                    ),
                    width="100%",
                    align="center",
                    padding_bottom="16px",
                ),
                rx.divider(),
                rx.cond(
                    GlobalState.is_authenticated,
                    rx.vstack(
                        rx.box(
                            rx.text(GlobalState.username, font_weight="600", font_size="15px"),
                            rx.text(GlobalState.user_email, font_size="12px", color="#6b7280"),
                            padding="12px 0",
                        ),
                        rx.divider(),
                        rx.link("📥 Λήψη Παραστατικών", href="/fetch", width="100%", padding="10px 4px", _hover={"background": "#f3f4f6"}),
                        rx.link("🔍 Αναζήτηση MARK", href="/search", width="100%", padding="10px 4px", _hover={"background": "#f3f4f6"}),
                        rx.link("🔐 Credentials", href="/credentials", width="100%", padding="10px 4px", _hover={"background": "#f3f4f6"}),
                        rx.link("👤 Προφίλ", href="/firebase-auth/profile", width="100%", padding="10px 4px", _hover={"background": "#f3f4f6"}),
                        rx.link("⚙️ Ρυθμίσεις", href="/options", width="100%", padding="10px 4px", _hover={"background": "#f3f4f6"}),
                        rx.link("👥 Ομάδες", href="/auth/groups", width="100%", padding="10px 4px", _hover={"background": "#f3f4f6"}),
                        rx.divider(),
                        rx.button(
                            "🚪 Αποσύνδεση",
                            on_click=GlobalState.logout,
                            width="100%",
                            color_scheme="red",
                            variant="soft",
                        ),
                        width="100%",
                        spacing="1",
                        align="start",
                    ),
                    rx.vstack(
                        rx.link("🔐 Σύνδεση", href="/firebase-auth/login", width="100%", padding="10px 4px"),
                        rx.link("📝 Εγγραφή", href="/firebase-auth/signup", width="100%", padding="10px 4px"),
                        rx.link("📜 Όροι Χρήσης", href="/terms", width="100%", padding="10px 4px"),
                        rx.link("🔒 Πολιτική Απορρήτου", href="/privacy", width="100%", padding="10px 4px"),
                        width="100%",
                        spacing="1",
                        align="start",
                    ),
                ),
                width="100%",
                height="100%",
                padding="20px",
                spacing="2",
            ),
            top="0",
            right="0",
            height="100%",
            width="300px",
            background_color=rx.color_mode_cond("#ffffff", "#1f2937"),
            box_shadow="-4px 0 16px rgba(0,0,0,0.12)",
        ),
        open=GlobalState.side_menu_open,
        on_open_change=GlobalState.toggle_side_menu,
        direction="right",
    )



def navbar_with_drawer() -> rx.Component:
    """Navbar including the side drawer overlay."""
    return rx.fragment(
        rx.box(
            rx.vstack(
                rx.hstack(
                    _logo(),
                    rx.spacer(),
                    _auth_nav(),
                    _dark_mode_toggle(),
                    _menu_button(),
                    width="100%",
                    align="center",
                    spacing="3",
                    padding="12px 0",
                ),
                _active_badges(),
                width="100%",
                spacing="1",
            ),
            background_color=rx.color_mode_cond("#ffffff", "#1f2937"),
            border_bottom=rx.color_mode_cond("1px solid #e5e7eb", "1px solid #374151"),
            padding="0 24px",
            position="sticky",
            top="0",
            z_index="10",
            box_shadow="0 1px 3px rgba(0,0,0,0.06)",
        ),
        _side_drawer(),
    )
