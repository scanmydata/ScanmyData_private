"""Landing / home page."""
import reflex as rx

from ..components.layout import card, layout
from ..state import GlobalState
from ..styles import SKY_600


def _logged_in_home() -> rx.Component:
    return rx.vstack(
        rx.heading(
            f"Καλωσήρθες, {GlobalState.username}!",
            size="6",
            color=rx.color_mode_cond("#1f2937", "#f9fafb"),
        ),
        rx.text(
            "Χρησιμοποιήστε τη γραμμή πλοήγησης για να αποκτήσετε πρόσβαση στις λειτουργίες.",
            color=rx.color_mode_cond("#6b7280", "#9ca3af"),
        ),
        rx.hstack(
            card(
                rx.vstack(
                    rx.text("📥", font_size="32px"),
                    rx.heading("Λήψη Παραστατικών", size="4"),
                    rx.text("Ανακτήστε παραστατικά MYDATA", font_size="14px", color=rx.color_mode_cond("#6b7280", "#9ca3af")),
                    rx.link(
                        rx.button("Μετάβαση", background_color=SKY_600, color="white", border_radius="8px"),
                        href="/fetch",
                    ),
                    spacing="3",
                    align="center",
                ),
                flex="1",
                text_align="center",
                cursor="pointer",
                _hover={"box_shadow": "0 4px 12px rgba(0,0,0,0.10)"},
            ),
            card(
                rx.vstack(
                    rx.text("🔍", font_size="32px"),
                    rx.heading("Αναζήτηση MARK", size="4"),
                    rx.text("Αναζητήστε παραστατικά με MARK", font_size="14px", color=rx.color_mode_cond("#6b7280", "#9ca3af")),
                    rx.link(
                        rx.button("Μετάβαση", background_color=SKY_600, color="white", border_radius="8px"),
                        href="/search",
                    ),
                    spacing="3",
                    align="center",
                ),
                flex="1",
                text_align="center",
                cursor="pointer",
                _hover={"box_shadow": "0 4px 12px rgba(0,0,0,0.10)"},
            ),
            card(
                rx.vstack(
                    rx.text("🔐", font_size="32px"),
                    rx.heading("Credentials", size="4"),
                    rx.text("Διαχειριστείτε τα credentials σας", font_size="14px", color=rx.color_mode_cond("#6b7280", "#9ca3af")),
                    rx.link(
                        rx.button("Μετάβαση", background_color=SKY_600, color="white", border_radius="8px"),
                        href="/credentials",
                    ),
                    spacing="3",
                    align="center",
                ),
                flex="1",
                text_align="center",
                cursor="pointer",
                _hover={"box_shadow": "0 4px 12px rgba(0,0,0,0.10)"},
            ),
            width="100%",
            spacing="4",
            flex_wrap="wrap",
        ),
        width="100%",
        spacing="6",
    )


def _landing_home() -> rx.Component:
    return rx.vstack(
        rx.box(
            rx.vstack(
                rx.image(
                    src="/icons/scanmydata_logo_3000w.png",
                    height="80px",
                    alt="ScanmyData",
                    margin_bottom="16px",
                ),
                rx.heading(
                    "ScanmyData",
                    size="8",
                    color=rx.color_mode_cond("#1f2937", "#f9fafb"),
                    font_weight="700",
                ),
                rx.text(
                    "Πλατφόρμα διαχείρισης παραστατικών MYDATA",
                    font_size="18px",
                    color=rx.color_mode_cond("#6b7280", "#9ca3af"),
                    text_align="center",
                ),
                rx.hstack(
                    rx.link(
                        rx.button(
                            "🔐 Σύνδεση",
                            background_color=SKY_600,
                            color="white",
                            padding="10px 24px",
                            border_radius="8px",
                            font_size="16px",
                            font_weight="600",
                        ),
                        href="/firebase-auth/login",
                    ),
                    rx.link(
                        rx.button(
                            "📝 Εγγραφή",
                            variant="outline",
                            padding="10px 24px",
                            border_radius="8px",
                            font_size="16px",
                        ),
                        href="/firebase-auth/signup",
                    ),
                    spacing="4",
                    justify="center",
                ),
                spacing="5",
                align="center",
                padding="80px 24px",
            ),
            text_align="center",
        ),
        rx.hstack(
            card(
                rx.vstack(
                    rx.text("📥", font_size="40px"),
                    rx.heading("Λήψη Παραστατικών", size="4"),
                    rx.text("Ανακτήστε αυτόματα παραστατικά από το MYDATA της ΑΑΔΕ.", font_size="14px", color=rx.color_mode_cond("#6b7280", "#9ca3af"), text_align="center"),
                    spacing="3",
                    align="center",
                ),
                flex="1",
                text_align="center",
                min_width="220px",
            ),
            card(
                rx.vstack(
                    rx.text("🔍", font_size="40px"),
                    rx.heading("Αναζήτηση MARK", size="4"),
                    rx.text("Αναζητήστε παραστατικά με αριθμό MARK ή ΑΦΜ.", font_size="14px", color=rx.color_mode_cond("#6b7280", "#9ca3af"), text_align="center"),
                    spacing="3",
                    align="center",
                ),
                flex="1",
                text_align="center",
                min_width="220px",
            ),
            card(
                rx.vstack(
                    rx.text("🔐", font_size="40px"),
                    rx.heading("Ασφαλής Αποθήκευση", size="4"),
                    rx.text("Τα credentials σας αποθηκεύονται με κρυπτογράφηση.", font_size="14px", color=rx.color_mode_cond("#6b7280", "#9ca3af"), text_align="center"),
                    spacing="3",
                    align="center",
                ),
                flex="1",
                text_align="center",
                min_width="220px",
            ),
            width="100%",
            spacing="4",
            flex_wrap="wrap",
        ),
        width="100%",
        spacing="6",
    )


@rx.page(route="/", title="ScanmyData - Αρχική", on_load=GlobalState.check_auth)
def home_page() -> rx.Component:
    return layout(
        rx.cond(
            GlobalState.is_authenticated,
            _logged_in_home(),
            _landing_home(),
        )
    )
