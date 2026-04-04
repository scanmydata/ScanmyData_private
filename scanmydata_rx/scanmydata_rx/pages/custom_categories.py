"""Custom categories page."""
import os
import httpx
import reflex as rx

from ..components.layout import card, layout
from ..state import GlobalState

FLASK_BASE = os.getenv("FLASK_API_URL", f"http://localhost:{os.getenv('PORT', '5001')}")


class CustomCategoriesState(GlobalState):
    categories: list[dict] = []
    cats_loading: bool = False
    cats_error: str = ""
    cats_success: str = ""

    async def on_load(self):
        await self.check_auth()
        if not self.is_authenticated:
            return rx.redirect("/firebase-auth/login")
        await self.load_categories()

    async def load_categories(self):
        self.cats_loading = True
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{FLASK_BASE}/custom_categories")
                if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                    data = resp.json()
                    self.categories = data.get("categories", [])
        except Exception as e:
            self.cats_error = f"Σφάλμα: {e}"
        finally:
            self.cats_loading = False


@rx.page(route="/custom-categories", title="Προσαρμοσμένες Κατηγορίες - ScanmyData", on_load=CustomCategoriesState.on_load)
def custom_categories_page() -> rx.Component:
    return layout(
        rx.vstack(
            rx.heading("🏷️ Προσαρμοσμένες Κατηγορίες", size="5", color=rx.color_mode_cond("#1f2937", "#f9fafb")),
            rx.text("Διαχειριστείτε τις προσαρμοσμένες κατηγορίες για τα παραστατικά σας.", font_size="13px", color=rx.color_mode_cond("#6b7280", "#9ca3af")),
            rx.cond(CustomCategoriesState.cats_error != "", rx.box(CustomCategoriesState.cats_error, background="#fee2e2", color="#7f1d1d", padding="10px", border_radius="8px", width="100%", font_size="14px"), rx.fragment()),
            rx.cond(
                CustomCategoriesState.cats_loading,
                rx.center(rx.spinner(size="3"), padding="40px"),
                rx.cond(
                    CustomCategoriesState.categories.length() > 0,
                    card(
                        rx.table.root(
                            rx.table.header(
                                rx.table.row(
                                    rx.table.column_header_cell("Κωδικός", font_size="13px"),
                                    rx.table.column_header_cell("Περιγραφή", font_size="13px"),
                                    rx.table.column_header_cell("Ενεργό", font_size="13px"),
                                )
                            ),
                            rx.table.body(
                                rx.foreach(
                                    CustomCategoriesState.categories,
                                    lambda cat: rx.table.row(
                                        rx.table.cell(rx.text(cat["code"], font_size="13px")),
                                        rx.table.cell(rx.text(cat["description"], font_size="13px")),
                                        rx.table.cell(rx.cond(cat["enabled"], rx.badge("✓", color_scheme="green"), rx.badge("✗", color_scheme="gray"))),
                                        _hover={"background_color": rx.color_mode_cond("#f9fafb", "#374151")},
                                    ),
                                )
                            ),
                            width="100%",
                            variant="surface",
                        ),
                        width="100%",
                    ),
                    rx.center(
                        rx.vstack(rx.text("🏷️", font_size="36px"), rx.text("Δεν υπάρχουν κατηγορίες.", font_size="14px", color="#6b7280"), spacing="2", align="center"),
                        padding="40px",
                    ),
                ),
            ),
            width="100%",
            spacing="4",
        )
    )
