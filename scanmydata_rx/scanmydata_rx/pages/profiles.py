"""Profiles page."""
import httpx
import reflex as rx

from ..components.layout import card, layout
from ..state import GlobalState

FLASK_BASE = "http://localhost:5000"


class ProfilesState(GlobalState):
    profiles: list[dict] = []
    profiles_loading: bool = False
    profiles_error: str = ""
    profiles_success: str = ""
    new_profile_name: str = ""
    add_open: bool = False

    async def on_load(self):
        await self.check_auth()
        if not self.is_authenticated:
            return rx.redirect("/firebase-auth/login")
        await self.load_profiles()

    async def load_profiles(self):
        self.profiles_loading = True
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{FLASK_BASE}/api/profiles")
                if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                    data = resp.json()
                    self.profiles = data.get("profiles", [])
        except Exception as e:
            self.profiles_error = f"Σφάλμα: {e}"
        finally:
            self.profiles_loading = False

    async def save_profile(self):
        if not self.new_profile_name:
            self.profiles_error = "Εισάγετε όνομα προφίλ."
            return
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{FLASK_BASE}/api/profiles/save", json={"name": self.new_profile_name})
                if resp.status_code == 200:
                    self.profiles_success = "Το προφίλ αποθηκεύτηκε."
                    self.new_profile_name = ""
                    self.add_open = False
                    await self.load_profiles()
                else:
                    self.profiles_error = "Σφάλμα αποθήκευσης."
        except Exception as e:
            self.profiles_error = f"Σφάλμα: {e}"

    async def delete_profile(self, name: str):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{FLASK_BASE}/api/profiles/delete", json={"name": name})
                if resp.status_code == 200:
                    self.profiles_success = f"Το προφίλ '{name}' διαγράφηκε."
                    await self.load_profiles()
                else:
                    self.profiles_error = "Σφάλμα διαγραφής."
        except Exception as e:
            self.profiles_error = f"Σφάλμα: {e}"


@rx.page(route="/profiles", title="Προφίλ - ScanmyData", on_load=ProfilesState.on_load)
def profiles_page() -> rx.Component:
    return layout(
        rx.vstack(
            rx.hstack(
                rx.heading("📋 Προφίλ", size="5", color=rx.color_mode_cond("#1f2937", "#f9fafb")),
                rx.spacer(),
                rx.button("➕ Νέο Προφίλ", on_click=ProfilesState.set_add_open(True), background_color="#0284c7", color="white", border_radius="8px", cursor="pointer"),
                width="100%",
                align="center",
            ),
            rx.cond(ProfilesState.profiles_error != "", rx.box(ProfilesState.profiles_error, background="#fee2e2", color="#7f1d1d", padding="10px", border_radius="8px", width="100%", font_size="14px"), rx.fragment()),
            rx.cond(ProfilesState.profiles_success != "", rx.box(ProfilesState.profiles_success, background="#ecfdf5", color="#065f46", padding="10px", border_radius="8px", width="100%", font_size="14px"), rx.fragment()),
            rx.cond(
                ProfilesState.profiles_loading,
                rx.center(rx.spinner(size="3"), padding="40px"),
                rx.cond(
                    ProfilesState.profiles.length() > 0,
                    rx.vstack(
                        rx.foreach(
                            ProfilesState.profiles,
                            lambda p: card(
                                rx.hstack(
                                    rx.text(p["name"], font_weight="500", font_size="15px"),
                                    rx.spacer(),
                                    rx.button("🗑️", on_click=ProfilesState.delete_profile(p["name"]), size="1", color_scheme="red", variant="soft", cursor="pointer"),
                                    align="center",
                                    width="100%",
                                ),
                                width="100%",
                                padding="14px 18px",
                            ),
                        ),
                        width="100%",
                        spacing="2",
                    ),
                    rx.center(
                        rx.vstack(
                            rx.text("📋", font_size="36px"),
                            rx.text("Δεν υπάρχουν προφίλ.", font_size="14px", color="#6b7280"),
                            spacing="2",
                            align="center",
                        ),
                        padding="40px",
                    ),
                ),
            ),
            rx.dialog.root(
                rx.dialog.content(
                    rx.dialog.title("➕ Νέο Προφίλ"),
                    rx.vstack(
                        rx.input(placeholder="Όνομα προφίλ", value=ProfilesState.new_profile_name, on_change=ProfilesState.set_new_profile_name, width="100%"),
                        rx.hstack(
                            rx.dialog.close(rx.button("Ακύρωση", variant="soft")),
                            rx.button("💾 Αποθήκευση", on_click=ProfilesState.save_profile, background_color="#0284c7", color="white"),
                            spacing="3",
                            justify="end",
                            width="100%",
                        ),
                        spacing="3",
                    ),
                    max_width="360px",
                ),
                open=ProfilesState.add_open,
                on_open_change=ProfilesState.set_add_open,
            ),
            width="100%",
            spacing="4",
        )
    )
