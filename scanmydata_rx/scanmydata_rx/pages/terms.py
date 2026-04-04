"""Terms of service page."""
import reflex as rx

from ..components.layout import card, layout


@rx.page(route="/terms", title="Όροι Χρήσης - ScanmyData")
def terms_page() -> rx.Component:
    return layout(
        card(
            rx.vstack(
                rx.heading("📜 Όροι Χρήσης", size="5", color=rx.color_mode_cond("#1f2937", "#f9fafb")),
                rx.divider(),
                rx.text("Τελευταία ενημέρωση: 2024", font_size="12px", color=rx.color_mode_cond("#6b7280", "#9ca3af")),
                rx.heading("1. Αποδοχή Όρων", size="3", margin_top="16px"),
                rx.text(
                    "Χρησιμοποιώντας την εφαρμογή ScanmyData, αποδέχεστε τους παρόντες όρους χρήσης. "
                    "Αν δεν συμφωνείτε, παρακαλούμε μην χρησιμοποιήσετε την εφαρμογή.",
                    font_size="14px",
                    color=rx.color_mode_cond("#374151", "#d1d5db"),
                    line_height="1.7",
                ),
                rx.heading("2. Περιγραφή Υπηρεσίας", size="3", margin_top="16px"),
                rx.text(
                    "Η ScanmyData παρέχει εργαλεία για τη διαχείριση και ανάκτηση παραστατικών μέσω της πλατφόρμας MYDATA της ΑΑΔΕ. "
                    "Η εφαρμογή λειτουργεί αποκλειστικά ως βοηθητικό εργαλείο.",
                    font_size="14px",
                    color=rx.color_mode_cond("#374151", "#d1d5db"),
                    line_height="1.7",
                ),
                rx.heading("3. Διαχείριση Δεδομένων", size="3", margin_top="16px"),
                rx.text(
                    "Τα credentials MYDATA αποθηκεύονται κρυπτογραφημένα. Δεν μοιραζόμαστε τα δεδομένα σας με τρίτους. "
                    "Είστε υπεύθυνοι για τη διατήρηση της ασφάλειας των κωδικών σας.",
                    font_size="14px",
                    color=rx.color_mode_cond("#374151", "#d1d5db"),
                    line_height="1.7",
                ),
                rx.heading("4. Περιορισμός Ευθύνης", size="3", margin_top="16px"),
                rx.text(
                    "Η ScanmyData δεν φέρει ευθύνη για τυχόν σφάλματα στα δεδομένα που λαμβάνονται από το MYDATA. "
                    "Η χρήση της εφαρμογής γίνεται με δική σας ευθύνη.",
                    font_size="14px",
                    color=rx.color_mode_cond("#374151", "#d1d5db"),
                    line_height="1.7",
                ),
                rx.heading("5. Επικοινωνία", size="3", margin_top="16px"),
                rx.text(
                    "Για ερωτήσεις σχετικά με τους παρόντες όρους, επικοινωνήστε μαζί μας.",
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
