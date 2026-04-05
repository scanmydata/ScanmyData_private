import reflex as rx
from ..components.layout import page_layout, page_header
from ..components.flash import flash_message
from ..state.credentials import CredentialsState


def credential_row(cred: dict) -> rx.Component:
    """Single row in the credentials table."""
    return rx.table.row(
        rx.table.cell(
            rx.hstack(
                rx.cond(
                    CredentialsState.active_credential_name == cred["name"],
                    rx.badge("Ενεργό", color_scheme="green", variant="soft"),
                    rx.fragment(),
                ),
                rx.text(cred["name"], font_weight="600"),
                spacing="2",
                align="center",
            )
        ),
        rx.table.cell(rx.text(cred["vat"])),
        rx.table.cell(rx.text(cred["user"])),
        rx.table.cell(
            rx.hstack(
                rx.button(
                    "Ενεργοποίηση",
                    size="1",
                    variant="soft",
                    color_scheme="blue",
                    on_click=CredentialsState.set_active(cred["name"]),
                ),
                rx.button(
                    rx.icon("trash-2", size=14),
                    size="1",
                    variant="soft",
                    color_scheme="red",
                    on_click=CredentialsState.delete_credential(cred["name"]),
                ),
                spacing="2",
            )
        ),
        bg=rx.cond(
            CredentialsState.active_credential_name == cred["name"],
            rx.color_mode_cond("blue.50", "blue.900"),
            rx.color_mode_cond("white", "gray.800"),
        ),
    )


def add_credential_form() -> rx.Component:
    """Form to add a new credential."""
    return rx.box(
        rx.heading("Προσθήκη Credential", size="4", margin_bottom="16px"),
        rx.grid(
            rx.vstack(
                rx.text("Όνομα *", font_size="0.85rem", font_weight="600", color="gray.600"),
                rx.input(
                    placeholder="π.χ. Εταιρεία Α",
                    value=CredentialsState.form_name,
                    on_change=CredentialsState.set_form_name,
                    width="100%",
                ),
                spacing="1",
            ),
            rx.vstack(
                rx.text("AADE User ID", font_size="0.85rem", font_weight="600", color="gray.600"),
                rx.input(
                    placeholder="AADE User ID",
                    value=CredentialsState.form_user,
                    on_change=CredentialsState.set_form_user,
                    width="100%",
                ),
                spacing="1",
            ),
            rx.vstack(
                rx.text("Subscription Key", font_size="0.85rem", font_weight="600", color="gray.600"),
                rx.input(
                    placeholder="Subscription Key",
                    type="password",
                    value=CredentialsState.form_key,
                    on_change=CredentialsState.set_form_key,
                    width="100%",
                ),
                spacing="1",
            ),
            rx.vstack(
                rx.text("ΑΦΜ", font_size="0.85rem", font_weight="600", color="gray.600"),
                rx.input(
                    placeholder="π.χ. 123456789",
                    value=CredentialsState.form_vat,
                    on_change=CredentialsState.set_form_vat,
                    width="100%",
                ),
                spacing="1",
            ),
            columns="2",
            spacing="4",
            width="100%",
        ),
        rx.button(
            rx.hstack(
                rx.icon("plus", size=16),
                rx.text("Προσθήκη"),
                spacing="2",
            ),
            on_click=CredentialsState.add_credential,
            color_scheme="blue",
            margin_top="16px",
            loading=CredentialsState.is_loading,
        ),
        padding="20px",
        bg=rx.color_mode_cond("white", "gray.800"),
        border_radius="12px",
        border="1px solid",
        border_color=rx.color_mode_cond("gray.200", "gray.700"),
        box_shadow="0 2px 8px rgba(0,0,0,0.05)",
        margin_bottom="24px",
    )


@rx.page(route="/credentials", title="ScanmyData - Credentials", on_load=CredentialsState.load_credentials)
def credentials_page() -> rx.Component:
    return page_layout(
        page_header("Credentials", "Διαχείριση διαπιστευτηρίων AADE MYDATA"),
        flash_message(CredentialsState.error, "error"),
        flash_message(CredentialsState.success, "success"),
        add_credential_form(),
        rx.box(
            rx.heading("Αποθηκευμένα Credentials", size="4", margin_bottom="16px"),
            rx.cond(
                CredentialsState.is_loading,
                rx.center(rx.spinner(size="3"), padding="40px"),
                rx.cond(
                    CredentialsState.credentials.length() == 0,
                    rx.center(
                        rx.vstack(
                            rx.icon("inbox", size=48, color="gray.300"),
                            rx.text("Δεν υπάρχουν credentials", color="gray.400"),
                            spacing="2",
                        ),
                        padding="40px",
                    ),
                    rx.table.root(
                        rx.table.header(
                            rx.table.row(
                                rx.table.column_header_cell("Όνομα"),
                                rx.table.column_header_cell("ΑΦΜ"),
                                rx.table.column_header_cell("AADE User"),
                                rx.table.column_header_cell("Ενέργειες"),
                            )
                        ),
                        rx.table.body(
                            rx.foreach(
                                CredentialsState.credentials,
                                credential_row,
                            )
                        ),
                        width="100%",
                        variant="surface",
                    ),
                ),
            ),
            padding="20px",
            bg=rx.color_mode_cond("white", "gray.800"),
            border_radius="12px",
            border="1px solid",
            border_color=rx.color_mode_cond("gray.200", "gray.700"),
        ),
    )
