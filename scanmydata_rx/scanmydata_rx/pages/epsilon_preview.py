"""Epsilon preview page."""
import httpx
import reflex as rx

from ..components.layout import card, layout
from ..state import GlobalState

FLASK_BASE = "http://localhost:5000"


class EpsilonPreviewState(GlobalState):
    rows: list[dict] = []
    columns: list[str] = []
    ep_loading: bool = False
    ep_error: str = ""

    async def on_load(self):
        await self.check_auth()
        if not self.is_authenticated:
            return rx.redirect("/firebase-auth/login")
        await self.load_preview()

    async def load_preview(self):
        self.ep_loading = True
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{FLASK_BASE}/epsilon/preview")
                if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                    data = resp.json()
                    self.rows = data.get("rows", [])
                    if self.rows:
                        self.columns = list(self.rows[0].keys())
        except Exception as e:
            self.ep_error = f"Σφάλμα: {e}"
        finally:
            self.ep_loading = False


@rx.page(route="/epsilon-preview", title="Epsilon Preview - ScanmyData", on_load=EpsilonPreviewState.on_load)
def epsilon_preview_page() -> rx.Component:
    return layout(
        rx.vstack(
            rx.heading("📊 Epsilon Preview", size="5", color=rx.color_mode_cond("#1f2937", "#f9fafb")),
            rx.cond(EpsilonPreviewState.ep_error != "", rx.box(EpsilonPreviewState.ep_error, background="#fee2e2", color="#7f1d1d", padding="10px", border_radius="8px", width="100%", font_size="14px"), rx.fragment()),
            rx.cond(
                EpsilonPreviewState.ep_loading,
                rx.center(rx.spinner(size="3"), padding="40px"),
                rx.cond(
                    EpsilonPreviewState.rows.length() > 0,
                    card(
                        rx.scroll_area(
                            rx.table.root(
                                rx.table.header(
                                    rx.table.row(
                                        rx.foreach(
                                            EpsilonPreviewState.columns,
                                            lambda col: rx.table.column_header_cell(col, font_size="12px", white_space="nowrap", background_color="#0284c7", color="white"),
                                        )
                                    )
                                ),
                                rx.table.body(
                                    rx.foreach(
                                        EpsilonPreviewState.rows,
                                        lambda row: rx.table.row(
                                            rx.foreach(
                                                EpsilonPreviewState.columns,
                                                lambda col: rx.table.cell(rx.text(row[col], font_size="12px", white_space="nowrap"), padding="6px 10px"),
                                            )
                                        ),
                                    )
                                ),
                                width="100%",
                                variant="surface",
                            ),
                            type="always",
                            scrollbars="both",
                            max_height="600px",
                        ),
                        width="100%",
                        padding="0",
                        overflow="hidden",
                    ),
                    rx.center(rx.text("Δεν υπάρχουν δεδομένα preview.", font_size="14px", color="#6b7280"), padding="40px"),
                ),
            ),
            width="100%",
            spacing="4",
        )
    )
