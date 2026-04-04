"""Privacy policy page."""
import reflex as rx

from ..components.layout import card, layout


@rx.page(route="/privacy", title="Πολιτική Απορρήτου - ScanmyData")
def privacy_page() -> rx.Component:
    return layout(
        card(
            rx.vstack(
                rx.heading("🔒 Πολιτική Απορρήτου", size="5", color=rx.color_mode_cond("#1f2937", "#f9fafb")),
                rx.divider(),
                rx.text("Τελευταία ενημέρωση: 2024", font_size="12px", color=rx.color_mode_cond("#6b7280", "#9ca3af")),
                rx.heading("1. Συλλογή Δεδομένων", size="3", margin_top="16px"),
                rx.text(
                    "Συλλέγουμε μόνο τα απαραίτητα δεδομένα για τη λειτουργία της υπηρεσίας: "
                    "email, όνομα χρήστη και credentials MYDATA (κρυπτογραφημένα).",
                    font_size="14px",
                    color=rx.color_mode_cond("#374151", "#d1d5db"),
                    line_height="1.7",
                ),
                rx.heading("2. Χρήση Δεδομένων", size="3", margin_top="16px"),
                rx.text(
                    "Τα δεδομένα σας χρησιμοποιούνται αποκλειστικά για την παροχή της υπηρεσίας ScanmyData. "
                    "Δεν πωλούμε ή μοιραζόμαστε προσωπικά δεδομένα με τρίτους.",
                    font_size="14px",
                    color=rx.color_mode_cond("#374151", "#d1d5db"),
                    line_height="1.7",
                ),
                rx.heading("3. Ασφάλεια", size="3", margin_top="16px"),
                rx.text(
                    "Χρησιμοποιούμε κρυπτογράφηση για την αποθήκευση ευαίσθητων δεδομένων. "
                    "Η authentication γίνεται μέσω Firebase για μέγιστη ασφάλεια.",
                    font_size="14px",
                    color=rx.color_mode_cond("#374151", "#d1d5db"),
                    line_height="1.7",
                ),
                rx.heading("4. Cookies", size="3", margin_top="16px"),
                rx.text(
                    "Χρησιμοποιούμε session cookies για τη διαχείριση της σύνδεσης. "
                    "Δεν χρησιμοποιούμε cookies παρακολούθησης τρίτων.",
                    font_size="14px",
                    color=rx.color_mode_cond("#374151", "#d1d5db"),
                    line_height="1.7",
                ),
                rx.heading("5. Δικαιώματά σας", size="3", margin_top="16px"),
                rx.text(
                    "Έχετε δικαίωμα πρόσβασης, διόρθωσης και διαγραφής των δεδομένων σας. "
                    "Επικοινωνήστε μαζί μας για οποιοδήποτε αίτημα.",
                    font_size="14px",
                    color=rx.color_mode_cond("#374151", "#d1d5db"),
                    line_height="1.7",
                ),
                spacing="3",
                width="100%",
                align="start",
            ),
            width="100%",
        )
    )
