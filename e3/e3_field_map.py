"""
E3 Field Map — Έντυπο Ε3 (2025)
================================
Βασισμένο στα επίσημα PDFs:
  • E3_katastasi_oikonomik_st_epixeirimatikis_0.pdf  (Κύριο Έντυπο)
  • E3_Ypopinakes_0.pdf                              (Υποπίνακες)

Δομή κωδικού στο Excel ισοζυγίου: [ΚΥΡΙΟΣ_ΚΩΔΙΚΟΣ].[ΥΠΟΚΩΔΙΚΟΣ]
  π.χ.  461.001  →  Πωλήσεις Παροχής Υπηρεσιών / Χονδρικές-Επιτηδευματιών

Δραστηριότητες (πρώτο ψηφίο ή εκατοντάδα):
  1xx  Εμπορική δραστηριότητα
  2xx  Παραγωγική δραστηριότητα
  3xx  Αγροτική - Βιολογική δραστηριότητα
  4xx  Παροχή Υπηρεσιών
  5xx  Σύνολο
  7xx  Προσωρινές Διαφορές (Πίνακας Ε)
  8xx  Λοιπά Πληροφοριακά (Πίνακας Ζ3)
  9xx  Ειδικές περιπτώσεις
"""

# ---------------------------------------------------------------------------
# Υποκωδικοί πωλήσεων (χρησιμοποιούνται σε: 161/261/361/461/561 = Ζ1)
# ---------------------------------------------------------------------------
SALES_SUB = {
    "001": "Χονδρικές - Επιτηδευματιών",
    "002": "Χονδρικές βάσει άρθρ. 39α παρ.5 Κώδικα ΦΠΑ",
    "003": "Λιανικές - Ιδιωτική Πελατεία",
    "004": "Λιανικές βάσει άρθρ. 39α παρ.5 Κώδικα ΦΠΑ",
    "005": "Εξωτερικού - Ενδοκοινοτικές",
    "006": "Εξωτερικού - Τρίτες Χώρες",
    "007": "Λοιπά",
    "008": "Προμήθεια πλανόδιων λαχειοπωλών (παρ.7 άρθρ.29 ν.4172/2013)",
}

# ---------------------------------------------------------------------------
# Υποκωδικοί αγορών (Δ2/Δ3/Δ4 + Ζ3 πάγια)
# ---------------------------------------------------------------------------
PURCHASE_SUB = {
    "001": "Χονδρικές",
    "002": "Λιανικές",
    "003": "Εξωτερικού - Ενδοκοινοτικές",
    "004": "Εξωτερικού - Τρίτες Χώρες",
    "005": "Λοιπά",
}

PURCHASE_GOODS_SUB = {   # Μόνο για αγορές εμπορευμάτων (102)
    "001": "Χονδρικές",
    "002": "Λιανικές",
    "003": "Αγαθών του άρθρου 39α παρ.5 Κώδικα ΦΠΑ",
    "004": "Εξωτερικού - Ενδοκοινοτικές",
    "005": "Εξωτερικού - Τρίτες Χώρες",
    "006": "Λοιπά",
}

# ---------------------------------------------------------------------------
# Υποκωδικοί παροχών σε εργαζόμενους (181/281/381/481/581)
# ---------------------------------------------------------------------------
PAYROLL_SUB = {
    "001": "Μικτές αποδοχές",
    "002": "Εργοδοτικές εισφορές",
    "003": "Λοιπές παροχές",
}

# ---------------------------------------------------------------------------
# Υποκωδικοί Λοιπών λειτουργικών εξόδων (185/285/385/485/585)
# ---------------------------------------------------------------------------
OPEX_SUB = {
    "001": "Προμήθειες διαχείρισης ημεδαπής - αλλοδαπής (management fees)",
    "002": "Δαπάνες από συνδεδεμένες επιχειρήσεις",
    "003": "Δαπάνες από μη συνεργαζόμενα κράτη ή κράτη με προνομιακό φορολογικό καθεστώς",
    "004": "Δαπάνες για ενημερωτικές ημερίδες",
    "005": "Έξοδα υποδοχής και φιλοξενίας",
    "006": "Έξοδα ταξιδίου",
    "007": "Ασφαλιστικές εισφορές αυτοαπασχολούμενων",
    "008": "Έξοδα και προμήθειες παραγγελιοδόχου για λογαριασμό αγροτών",
    "009": "Λοιπές αμοιβές για υπηρεσίες ημεδαπής",
    "010": "Λοιπές αμοιβές για υπηρεσίες αλλοδαπής",
    "011": "Ενέργεια",
    "012": "Ύδρευση",
    "013": "Τηλεπικοινωνίες",
    "014": "Ενοίκια",
    "015": "Διαφήμιση και προβολή",
    "016": "Λοιπά έξοδα",
    "017": "Ποσό 7% υπέρ ΕΛΚΕ",
}

# ---------------------------------------------------------------------------
# Υποκωδικοί παγίων (800-883)
# ---------------------------------------------------------------------------
ASSETS_SUB = {
    "001": "Χονδρικές",
    "002": "Λιανικές",
    "003": "Εξωτερικού - Ενδοκοινοτικές",
    "004": "Εξωτερικού - Τρίτες Χώρες",
}

# ---------------------------------------------------------------------------
# Κεντρικός χάρτης Ε3
# Κλειδί: int ή str του κυρίου κωδικού
# Τιμή: dict με label, table, activity, sub_codes
#
# activity:
#   "commercial"  = Εμπορική
#   "production"  = Παραγωγική
#   "agricultural"= Αγροτική-Βιολογική
#   "services"    = Παροχή Υπηρεσιών
#   "total"       = Σύνολο
#   "common"      = Κοινό για όλες (χωρίς ανάλυση δραστηριότητας)
# ---------------------------------------------------------------------------

E3_FIELD_MAP = {

    # ── ΠΙΝΑΚΑΣ Δ1 - ΠΩΛΗΣΕΙΣ ──────────────────────────────────────────────
    100: {"label": "Πωλήσεις αγαθών και παροχή υπηρεσιών — Εμπορική",     "table": "Δ1", "activity": "commercial",   "sub_codes": {}},
    200: {"label": "Πωλήσεις αγαθών και παροχή υπηρεσιών — Παραγωγική",   "table": "Δ1", "activity": "production",   "sub_codes": {}},
    300: {"label": "Πωλήσεις αγαθών και παροχή υπηρεσιών — Αγροτική",     "table": "Δ1", "activity": "agricultural", "sub_codes": {}},
    400: {"label": "Πωλήσεις αγαθών και παροχή υπηρεσιών — Υπηρεσίες",   "table": "Δ1", "activity": "services",     "sub_codes": {}},
    500: {"label": "Πωλήσεις αγαθών και παροχή υπηρεσιών — Σύνολο",       "table": "Δ1", "activity": "total",        "sub_codes": {}},

    # ── ΠΙΝΑΚΑΣ Δ2 - ΚΟΣΤΟΣ ΕΜΠΟΡΙΚΗΣ ─────────────────────────────────────
    101: {"label": "Εμπορεύματα έναρξης",                                  "table": "Δ2", "activity": "commercial",   "sub_codes": {}},
    102: {"label": "Αγορές εμπορευμάτων χρήσης (καθαρό ποσό)",            "table": "Δ2", "activity": "commercial",   "sub_codes": PURCHASE_GOODS_SUB},
    103: {"label": "Απομείωση εμπορευμάτων",                               "table": "Δ2", "activity": "commercial",   "sub_codes": {}},
    104: {"label": "Εμπορεύματα λήξης",                                    "table": "Δ2", "activity": "commercial",   "sub_codes": {}},
    105: {"label": "Λοιπά έξοδα εμπορικής δραστηριότητας",                "table": "Δ2", "activity": "commercial",   "sub_codes": {}},
    106: {"label": "Ιδιοπαραγωγή παγίων - Αυτοπαραδόσεις - Καταστροφές (μείον)", "table": "Δ2", "activity": "commercial", "sub_codes": {}},
    107: {"label": "Κόστος πωληθέντων εμπορικής δραστηριότητας (Δ2)",     "table": "Δ2", "activity": "commercial",   "sub_codes": {}},

    # ── ΠΙΝΑΚΑΣ Δ3 - ΚΟΣΤΟΣ ΠΑΡΑΓΩΓΙΚΗΣ ───────────────────────────────────
    201: {"label": "Πρώτες ύλες και υλικά έναρξης — Παραγωγική",          "table": "Δ3", "activity": "production",   "sub_codes": {}},
    202: {"label": "Αγορές πρώτων υλών και υλικών χρήσης (καθαρό) — Παραγωγική", "table": "Δ3", "activity": "production", "sub_codes": PURCHASE_SUB},
    203: {"label": "Απομείωση πρώτων υλών και υλικών — Παραγωγική",       "table": "Δ3", "activity": "production",   "sub_codes": {}},
    204: {"label": "Αποθέματα λήξης πρώτων υλών και υλικών — Παραγωγική", "table": "Δ3", "activity": "production",   "sub_codes": {}},
    205: {"label": "Ιδιοπαραγωγή/Αυτοπαραδόσεις/Καταστροφές (μείον) — Παρ.", "table": "Δ3", "activity": "production", "sub_codes": {}},
    206: {"label": "Κόστος αναλώσεων πρώτων υλών — Παραγωγική",           "table": "Δ3", "activity": "production",   "sub_codes": {}},
    207: {"label": "Προϊόντα και παραγωγή σε εξέλιξη έναρξης",            "table": "Δ3", "activity": "production",   "sub_codes": {}},
    208: {"label": "Απομείωση προϊόντων σε εξέλιξη",                      "table": "Δ3", "activity": "production",   "sub_codes": {}},
    209: {"label": "Προϊόντα και παραγωγή σε εξέλιξη λήξης",              "table": "Δ3", "activity": "production",   "sub_codes": {}},
    210: {"label": "Ιδιοπαραγωγή/Αυτοπαραδόσεις παραγωγής (μείον)",       "table": "Δ3", "activity": "production",   "sub_codes": {}},
    211: {"label": "Κόστος αναλώσεων προϊόντων σε εξέλιξη",               "table": "Δ3", "activity": "production",   "sub_codes": {}},
    212: {"label": "Λοιπά έξοδα παραγωγής",                               "table": "Δ3", "activity": "production",   "sub_codes": {}},
    213: {"label": "Κόστος πωληθέντων παραγωγικής δραστηριότητας (Δ3)",   "table": "Δ3", "activity": "production",   "sub_codes": {}},
    # Αγροτική στήλη Δ3
    301: {"label": "Πρώτες ύλες και υλικά έναρξης — Αγροτική",            "table": "Δ3", "activity": "agricultural", "sub_codes": {}},
    302: {"label": "Αγορές πρώτων υλών και υλικών χρήσης (καθαρό) — Αγροτική", "table": "Δ3", "activity": "agricultural", "sub_codes": PURCHASE_SUB},
    303: {"label": "Απομείωση πρώτων υλών — Αγροτική",                    "table": "Δ3", "activity": "agricultural", "sub_codes": {}},
    304: {"label": "Αποθέματα λήξης πρώτων υλών — Αγροτική",              "table": "Δ3", "activity": "agricultural", "sub_codes": {}},
    305: {"label": "Ιδιοπαραγωγή/Αυτοπαραδόσεις/Καταστροφές (μείον) — Αγρ.", "table": "Δ3", "activity": "agricultural", "sub_codes": {}},
    306: {"label": "Κόστος αναλώσεων πρώτων υλών — Αγροτική",             "table": "Δ3", "activity": "agricultural", "sub_codes": {}},
    307: {"label": "Παραγωγή σε εξέλιξη έναρξης — Αγροτική",              "table": "Δ3", "activity": "agricultural", "sub_codes": {}},
    308: {"label": "Απομείωση παραγωγής — Αγροτική",                      "table": "Δ3", "activity": "agricultural", "sub_codes": {}},
    309: {"label": "Παραγωγή σε εξέλιξη λήξης — Αγροτική",                "table": "Δ3", "activity": "agricultural", "sub_codes": {}},
    310: {"label": "Ιδιοπαραγωγή παραγωγής (μείον) — Αγροτική",           "table": "Δ3", "activity": "agricultural", "sub_codes": {}},
    311: {"label": "Κόστος αναλώσεων παραγωγής — Αγροτική",               "table": "Δ3", "activity": "agricultural", "sub_codes": {}},

    # ── ΠΙΝΑΚΑΣ Δ4 - ΚΟΣΤΟΣ ΑΓΡΟΤΙΚΗΣ ─────────────────────────────────────
    312: {"label": "Αποθέματα έναρξης ζώων - φυτών",                      "table": "Δ4", "activity": "agricultural", "sub_codes": {}},
    313: {"label": "Αγορές ζώων - φυτών (καθαρό ποσό)",                   "table": "Δ4", "activity": "agricultural", "sub_codes": PURCHASE_SUB},
    314: {"label": "Απομείωση ζώων - φυτών - εμπορευμάτων",               "table": "Δ4", "activity": "agricultural", "sub_codes": {}},
    315: {"label": "Αποθέματα τέλους ζώων - φυτών",                       "table": "Δ4", "activity": "agricultural", "sub_codes": {}},
    316: {"label": "Κόστος αναλώσεων ζώων και φυτών",                     "table": "Δ4", "activity": "agricultural", "sub_codes": {}},
    317: {"label": "Έξοδα παραγωγής — Αγροτική",                          "table": "Δ4", "activity": "agricultural", "sub_codes": {}},
    318: {"label": "Ιδιοπαραγωγή/Αυτοπαραδόσεις/Καταστροφές (μείον) — Αγρ.", "table": "Δ4", "activity": "agricultural", "sub_codes": {}},
    319: {"label": "Κόστος πωληθέντων αγροτικής - βιολογικής δ/τας (Δ4)","table": "Δ4", "activity": "agricultural", "sub_codes": {}},

    # ── ΠΙΝΑΚΑΣ Δ5 - ΠΑΡΟΧΗ ΥΠΗΡΕΣΙΩΝ ─────────────────────────────────────
    401: {"label": "Σύνολο δαπανών από παροχή υπηρεσιών (Δ5)",            "table": "Δ5", "activity": "services",     "sub_codes": {}},

    # ── ΠΙΝΑΚΑΣ Δ6-Δ15 (ΣΥΝΟΛΙΚΑ/ΑΠΟΤΕΛΕΣΜΑΤΑ) ────────────────────────────
    120: {"label": "Σύνολο (Δ2+Δ3+Δ4+Δ5) — Εμπορική",                    "table": "Δ6", "activity": "commercial",   "sub_codes": {}},
    220: {"label": "Σύνολο — Παραγωγική",                                  "table": "Δ6", "activity": "production",   "sub_codes": {}},
    320: {"label": "Σύνολο — Αγροτική",                                    "table": "Δ6", "activity": "agricultural", "sub_codes": {}},
    420: {"label": "Σύνολο — Υπηρεσίες",                                   "table": "Δ6", "activity": "services",     "sub_codes": {}},
    520: {"label": "Σύνολο — Σύνολο",                                      "table": "Δ6", "activity": "total",        "sub_codes": {}},
    121: {"label": "Μικτό κέρδος (Δ1-Δ6) — Εμπορική",                    "table": "Δ7", "activity": "commercial",   "sub_codes": {}},
    221: {"label": "Μικτό κέρδος — Παραγωγική",                           "table": "Δ7", "activity": "production",   "sub_codes": {}},
    321: {"label": "Μικτό κέρδος — Αγροτική",                             "table": "Δ7", "activity": "agricultural", "sub_codes": {}},
    421: {"label": "Μικτό κέρδος — Υπηρεσίες",                            "table": "Δ7", "activity": "services",     "sub_codes": {}},
    521: {"label": "Μικτό κέρδος — Σύνολο",                               "table": "Δ7", "activity": "total",        "sub_codes": {}},
    122: {"label": "Λοιπά έσοδα (εκτός τόκων) — Εμπορική",                "table": "Δ8", "activity": "commercial",   "sub_codes": {}},
    222: {"label": "Λοιπά έσοδα — Παραγωγική",                            "table": "Δ8", "activity": "production",   "sub_codes": {}},
    322: {"label": "Λοιπά έσοδα — Αγροτική",                              "table": "Δ8", "activity": "agricultural", "sub_codes": {}},
    422: {"label": "Λοιπά έσοδα — Υπηρεσίες",                             "table": "Δ8", "activity": "services",     "sub_codes": {}},
    522: {"label": "Λοιπά έσοδα — Σύνολο",                                "table": "Δ8", "activity": "total",        "sub_codes": {}},
    123: {"label": "Λοιπά έξοδα (εκτός τόκων/αποσβέσεων) — Εμπορική",    "table": "Δ9", "activity": "commercial",   "sub_codes": {}},
    223: {"label": "Λοιπά έξοδα — Παραγωγική",                            "table": "Δ9", "activity": "production",   "sub_codes": {}},
    323: {"label": "Λοιπά έξοδα — Αγροτική",                              "table": "Δ9", "activity": "agricultural", "sub_codes": {}},
    423: {"label": "Λοιπά έξοδα — Υπηρεσίες",                             "table": "Δ9", "activity": "services",     "sub_codes": {}},
    523: {"label": "Λοιπά έξοδα — Σύνολο",                                "table": "Δ9", "activity": "total",        "sub_codes": {}},
    124: {"label": "EBITDA — Εμπορική",  "table": "Δ10", "activity": "commercial",   "sub_codes": {}},
    224: {"label": "EBITDA — Παραγωγική","table": "Δ10", "activity": "production",   "sub_codes": {}},
    324: {"label": "EBITDA — Αγροτική",  "table": "Δ10", "activity": "agricultural", "sub_codes": {}},
    424: {"label": "EBITDA — Υπηρεσίες", "table": "Δ10", "activity": "services",     "sub_codes": {}},
    524: {"label": "EBITDA — Σύνολο",    "table": "Δ10", "activity": "total",        "sub_codes": {}},
    125: {"label": "Αποσβέσεις — Εμπορική",  "table": "Δ11", "activity": "commercial",   "sub_codes": {}},
    225: {"label": "Αποσβέσεις — Παραγωγική","table": "Δ11", "activity": "production",   "sub_codes": {}},
    325: {"label": "Αποσβέσεις — Αγροτική",  "table": "Δ11", "activity": "agricultural", "sub_codes": {}},
    425: {"label": "Αποσβέσεις — Υπηρεσίες", "table": "Δ11", "activity": "services",     "sub_codes": {}},
    525: {"label": "Αποσβέσεις — Σύνολο",    "table": "Δ11", "activity": "total",        "sub_codes": {}},
    126: {"label": "EBIT — Εμπορική",  "table": "Δ12", "activity": "commercial",   "sub_codes": {}},
    226: {"label": "EBIT — Παραγωγική","table": "Δ12", "activity": "production",   "sub_codes": {}},
    326: {"label": "EBIT — Αγροτική",  "table": "Δ12", "activity": "agricultural", "sub_codes": {}},
    426: {"label": "EBIT — Υπηρεσίες", "table": "Δ12", "activity": "services",     "sub_codes": {}},
    526: {"label": "EBIT — Σύνολο",    "table": "Δ12", "activity": "total",        "sub_codes": {}},
    127: {"label": "Πιστωτικοί τόκοι και συναφή έσοδα — Εμπορική",  "table": "Δ13", "activity": "commercial",   "sub_codes": {}},
    227: {"label": "Πιστωτικοί τόκοι — Παραγωγική","table": "Δ13", "activity": "production",   "sub_codes": {}},
    327: {"label": "Πιστωτικοί τόκοι — Αγροτική",  "table": "Δ13", "activity": "agricultural", "sub_codes": {}},
    427: {"label": "Πιστωτικοί τόκοι — Υπηρεσίες", "table": "Δ13", "activity": "services",     "sub_codes": {}},
    527: {"label": "Πιστωτικοί τόκοι — Σύνολο",    "table": "Δ13", "activity": "total",        "sub_codes": {}},
    128: {"label": "Χρεωστικοί τόκοι και συναφή έξοδα — Εμπορική",  "table": "Δ14", "activity": "commercial",   "sub_codes": {}},
    228: {"label": "Χρεωστικοί τόκοι — Παραγωγική","table": "Δ14", "activity": "production",   "sub_codes": {}},
    328: {"label": "Χρεωστικοί τόκοι — Αγροτική",  "table": "Δ14", "activity": "agricultural", "sub_codes": {}},
    428: {"label": "Χρεωστικοί τόκοι — Υπηρεσίες", "table": "Δ14", "activity": "services",     "sub_codes": {}},
    528: {"label": "Χρεωστικοί τόκοι — Σύνολο",    "table": "Δ14", "activity": "total",        "sub_codes": {}},
    129: {"label": "Αποτελέσματα προ φόρων — Εμπορική",  "table": "Δ15", "activity": "commercial",   "sub_codes": {}},
    229: {"label": "Αποτελέσματα προ φόρων — Παραγωγική","table": "Δ15", "activity": "production",   "sub_codes": {}},
    329: {"label": "Αποτελέσματα προ φόρων — Αγροτική",  "table": "Δ15", "activity": "agricultural", "sub_codes": {}},
    429: {"label": "Αποτελέσματα προ φόρων — Υπηρεσίες", "table": "Δ15", "activity": "services",     "sub_codes": {}},
    529: {"label": "Αποτελέσματα προ φόρων — Σύνολο",    "table": "Δ15", "activity": "total",        "sub_codes": {}},

    # ── ΠΙΝΑΚΑΣ Ε - ΠΡΟΣΩΡΙΝΕΣ ΔΙΑΦΟΡΕΣ ───────────────────────────────────
    700: {"label": "Ενσώματα πάγια — Λογιστική Βάση",        "table": "Ε", "activity": "common", "sub_codes": {}},
    701: {"label": "Άυλα στοιχεία — Λογιστική Βάση",         "table": "Ε", "activity": "common", "sub_codes": {}},
    702: {"label": "Χρηματοοικονομικά στοιχεία — Λογιστική", "table": "Ε", "activity": "common", "sub_codes": {}},
    703: {"label": "Προβλέψεις — Λογιστική Βάση",             "table": "Ε", "activity": "common", "sub_codes": {}},
    704: {"label": "Λοιπές διαφορές ενεργητικού — Λογιστική","table": "Ε", "activity": "common", "sub_codes": {}},
    705: {"label": "Λοιπές διαφορές παθητικού — Λογιστική",  "table": "Ε", "activity": "common", "sub_codes": {}},
    706: {"label": "Διαφορές από ετεροχρονισμό εσόδων — Λογιστική", "table": "Ε", "activity": "common", "sub_codes": {}},
    707: {"label": "Διαφορές από ετεροχρονισμό εξόδων — Λογιστική", "table": "Ε", "activity": "common", "sub_codes": {}},
    708: {"label": "Ενσώματα πάγια — Φορολογική Βάση",        "table": "Ε", "activity": "common", "sub_codes": {}},
    709: {"label": "Άυλα στοιχεία — Φορολογική Βάση",         "table": "Ε", "activity": "common", "sub_codes": {}},
    710: {"label": "Χρηματοοικονομικά — Φορολογική Βάση",     "table": "Ε", "activity": "common", "sub_codes": {}},
    711: {"label": "Προβλέψεις — Φορολογική Βάση",            "table": "Ε", "activity": "common", "sub_codes": {}},
    712: {"label": "Λοιπές διαφορές ενεργητικού — Φορολογική","table": "Ε", "activity": "common", "sub_codes": {}},
    713: {"label": "Λοιπές διαφορές παθητικού — Φορολογική",  "table": "Ε", "activity": "common", "sub_codes": {}},
    714: {"label": "Διαφορές ετεροχρονισμού εσόδων — Φορολογική", "table": "Ε", "activity": "common", "sub_codes": {}},
    715: {"label": "Διαφορές ετεροχρονισμού εξόδων — Φορολογική", "table": "Ε", "activity": "common", "sub_codes": {}},
    716: {"label": "Ενσώματα πάγια — Θετικές Διαφορές",       "table": "Ε", "activity": "common", "sub_codes": {}},
    717: {"label": "Άυλα στοιχεία — Θετικές Διαφορές",        "table": "Ε", "activity": "common", "sub_codes": {}},
    718: {"label": "Χρηματοοικονομικά — Θετικές Διαφορές",    "table": "Ε", "activity": "common", "sub_codes": {}},
    719: {"label": "Προβλέψεις — Θετικές Διαφορές",           "table": "Ε", "activity": "common", "sub_codes": {}},
    720: {"label": "Λοιπές ενεργητικού — Θετικές Διαφορές",   "table": "Ε", "activity": "common", "sub_codes": {}},
    721: {"label": "Λοιπές παθητικού — Θετικές Διαφορές",     "table": "Ε", "activity": "common", "sub_codes": {}},
    722: {"label": "Ετεροχρονισμός εσόδων — Θετικές Διαφορές","table": "Ε", "activity": "common", "sub_codes": {}},
    723: {"label": "Ετεροχρονισμός εξόδων — Θετικές Διαφορές","table": "Ε", "activity": "common", "sub_codes": {}},
    724: {"label": "Ενσώματα πάγια — Αρνητικές Διαφορές",     "table": "Ε", "activity": "common", "sub_codes": {}},
    725: {"label": "Άυλα στοιχεία — Αρνητικές Διαφορές",      "table": "Ε", "activity": "common", "sub_codes": {}},
    726: {"label": "Χρηματοοικονομικά — Αρνητικές Διαφορές",  "table": "Ε", "activity": "common", "sub_codes": {}},
    727: {"label": "Προβλέψεις — Αρνητικές Διαφορές",         "table": "Ε", "activity": "common", "sub_codes": {}},
    728: {"label": "Λοιπές ενεργητικού — Αρνητικές Διαφορές", "table": "Ε", "activity": "common", "sub_codes": {}},
    729: {"label": "Λοιπές παθητικού — Αρνητικές Διαφορές",   "table": "Ε", "activity": "common", "sub_codes": {}},
    730: {"label": "Ετεροχρονισμός εσόδων — Αρνητικές Διαφορές","table": "Ε", "activity": "common", "sub_codes": {}},
    731: {"label": "Ετεροχρονισμός εξόδων — Αρνητικές Διαφορές","table": "Ε", "activity": "common", "sub_codes": {}},
    732: {"label": "Σύνολο Θετικών Διαφορών Πίνακα Ε",         "table": "Ε", "activity": "common", "sub_codes": {}},
    733: {"label": "Σύνολο Αρνητικών Διαφορών Πίνακα Ε",       "table": "Ε", "activity": "common", "sub_codes": {}},

    # ── ΠΙΝΑΚΑΣ ΣΤ - ΦΟΡΟΛΟΓΗΤΕΑ ΚΕΡΔΗ ΑΤΟΜΙΚΩΝ ───────────────────────────
    140: {"label": "Αποτελέσματα προ φόρου — Εμπορική",       "table": "ΣΤ", "activity": "commercial",   "sub_codes": {}},
    240: {"label": "Αποτελέσματα προ φόρου — Παραγωγική",     "table": "ΣΤ", "activity": "production",   "sub_codes": {}},
    340: {"label": "Αποτελέσματα προ φόρου — Αγροτική",       "table": "ΣΤ", "activity": "agricultural", "sub_codes": {}},
    440: {"label": "Αποτελέσματα προ φόρου — Υπηρεσίες",      "table": "ΣΤ", "activity": "services",     "sub_codes": {}},
    540: {"label": "Αποτελέσματα προ φόρου — Σύνολο",         "table": "ΣΤ", "activity": "total",        "sub_codes": {}},
    152: {"label": "Φορολογητέα καθαρά αποτελέσματα — Εμπορική",  "table": "ΣΤ", "activity": "commercial",   "sub_codes": {}},
    252: {"label": "Φορολογητέα καθαρά αποτελέσματα — Παραγωγική","table": "ΣΤ", "activity": "production",   "sub_codes": {}},
    452: {"label": "Φορολογητέα καθαρά αποτελέσματα — Υπηρεσίες", "table": "ΣΤ", "activity": "services",     "sub_codes": {}},
    552: {"label": "Φορολογητέα καθαρά αποτελέσματα — Σύνολο",    "table": "ΣΤ", "activity": "total",        "sub_codes": {}},
    352: {"label": "Φορολογητέα καθαρά αποτελέσματα αγροτικής",   "table": "ΣΤ", "activity": "agricultural", "sub_codes": {}},
    555: {"label": "Συνολικά Καθαρά Αποτελέσματα",                "table": "ΣΤ", "activity": "total",        "sub_codes": {}},

    # ── ΠΙΝΑΚΑΣ Ζ1 - ΣΥΝΟΛΟ ΕΣΟΔΩΝ ────────────────────────────────────────
    160: {"label": "Σύνολο Εσόδων — Εμπορική",       "table": "Ζ1", "activity": "commercial",   "sub_codes": {}},
    260: {"label": "Σύνολο Εσόδων — Παραγωγική",     "table": "Ζ1", "activity": "production",   "sub_codes": {}},
    360: {"label": "Σύνολο Εσόδων — Αγροτική",       "table": "Ζ1", "activity": "agricultural", "sub_codes": {}},
    460: {"label": "Σύνολο Εσόδων — Υπηρεσίες",      "table": "Ζ1", "activity": "services",     "sub_codes": {}},
    560: {"label": "Σύνολο Εσόδων — Σύνολο",         "table": "Ζ1", "activity": "total",        "sub_codes": {}},
    161: {"label": "Πωλήσεις αγαθών και υπηρεσιών — Εμπορική",   "table": "Ζ1", "activity": "commercial",   "sub_codes": SALES_SUB},
    261: {"label": "Πωλήσεις αγαθών και υπηρεσιών — Παραγωγική", "table": "Ζ1", "activity": "production",   "sub_codes": SALES_SUB},
    361: {"label": "Πωλήσεις αγαθών και υπηρεσιών — Αγροτική",   "table": "Ζ1", "activity": "agricultural", "sub_codes": SALES_SUB},
    461: {"label": "Πωλήσεις αγαθών και υπηρεσιών — Υπηρεσίες",  "table": "Ζ1", "activity": "services",     "sub_codes": SALES_SUB},
    561: {"label": "Πωλήσεις αγαθών και υπηρεσιών — Σύνολο",     "table": "Ζ1", "activity": "total",        "sub_codes": SALES_SUB},
    162: {"label": "Λοιπά συνήθη έσοδα — Εμπορική",    "table": "Ζ1", "activity": "commercial",   "sub_codes": {}},
    262: {"label": "Λοιπά συνήθη έσοδα — Παραγωγική",  "table": "Ζ1", "activity": "production",   "sub_codes": {}},
    362: {"label": "Λοιπά συνήθη έσοδα — Αγροτική",    "table": "Ζ1", "activity": "agricultural", "sub_codes": {}},
    462: {"label": "Λοιπά συνήθη έσοδα — Υπηρεσίες",   "table": "Ζ1", "activity": "services",     "sub_codes": {}},
    562: {"label": "Λοιπά συνήθη έσοδα — Σύνολο",      "table": "Ζ1", "activity": "total",        "sub_codes": {}},
    163: {"label": "Πιστωτικοί τόκοι και συναφή — Εμπορική",    "table": "Ζ1", "activity": "commercial",   "sub_codes": {}},
    263: {"label": "Πιστωτικοί τόκοι — Παραγωγική",             "table": "Ζ1", "activity": "production",   "sub_codes": {}},
    363: {"label": "Πιστωτικοί τόκοι — Αγροτική",               "table": "Ζ1", "activity": "agricultural", "sub_codes": {}},
    463: {"label": "Πιστωτικοί τόκοι — Υπηρεσίες",              "table": "Ζ1", "activity": "services",     "sub_codes": {}},
    563: {"label": "Πιστωτικοί τόκοι — Σύνολο",                 "table": "Ζ1", "activity": "total",        "sub_codes": {}},
    164: {"label": "Πιστωτικές συναλλαγματικές διαφορές — Εμπορική", "table": "Ζ1", "activity": "commercial",   "sub_codes": {}},
    264: {"label": "Πιστ. συναλλαγματικές — Παραγωγική",              "table": "Ζ1", "activity": "production",   "sub_codes": {}},
    364: {"label": "Πιστ. συναλλαγματικές — Αγροτική",                "table": "Ζ1", "activity": "agricultural", "sub_codes": {}},
    464: {"label": "Πιστ. συναλλαγματικές — Υπηρεσίες",               "table": "Ζ1", "activity": "services",     "sub_codes": {}},
    564: {"label": "Πιστ. συναλλαγματικές — Σύνολο",                  "table": "Ζ1", "activity": "total",        "sub_codes": {}},
    165: {"label": "Έσοδα συμμετοχών — Εμπορική",    "table": "Ζ1", "activity": "commercial",   "sub_codes": {}},
    265: {"label": "Έσοδα συμμετοχών — Παραγωγική",  "table": "Ζ1", "activity": "production",   "sub_codes": {}},
    365: {"label": "Έσοδα συμμετοχών — Αγροτική",    "table": "Ζ1", "activity": "agricultural", "sub_codes": {}},
    465: {"label": "Έσοδα συμμετοχών — Υπηρεσίες",   "table": "Ζ1", "activity": "services",     "sub_codes": {}},
    565: {"label": "Έσοδα συμμετοχών — Σύνολο",      "table": "Ζ1", "activity": "total",        "sub_codes": {}},
    166: {"label": "Κέρδη από διάθεση μη κυκλοφορούντων — Εμπορική",   "table": "Ζ1", "activity": "commercial",   "sub_codes": {}},
    266: {"label": "Κέρδη από διάθεση μη κυκλοφορούντων — Παραγωγική", "table": "Ζ1", "activity": "production",   "sub_codes": {}},
    366: {"label": "Κέρδη από διάθεση μη κυκλοφορούντων — Αγροτική",   "table": "Ζ1", "activity": "agricultural", "sub_codes": {}},
    466: {"label": "Κέρδη από διάθεση μη κυκλοφορούντων — Υπηρεσίες",  "table": "Ζ1", "activity": "services",     "sub_codes": {}},
    566: {"label": "Κέρδη από διάθεση μη κυκλοφορούντων — Σύνολο",     "table": "Ζ1", "activity": "total",        "sub_codes": {}},
    167: {"label": "Κέρδη από αναστροφή προβλέψεων — Εμπορική",    "table": "Ζ1", "activity": "commercial",   "sub_codes": {}},
    267: {"label": "Κέρδη από αναστροφή προβλέψεων — Παραγωγική",  "table": "Ζ1", "activity": "production",   "sub_codes": {}},
    367: {"label": "Κέρδη από αναστροφή προβλέψεων — Αγροτική",    "table": "Ζ1", "activity": "agricultural", "sub_codes": {}},
    467: {"label": "Κέρδη από αναστροφή προβλέψεων — Υπηρεσίες",   "table": "Ζ1", "activity": "services",     "sub_codes": {}},
    567: {"label": "Κέρδη από αναστροφή προβλέψεων — Σύνολο",      "table": "Ζ1", "activity": "total",        "sub_codes": {}},
    168: {"label": "Κέρδη από επιμέτρηση στην εύλογη αξία — Εμπορική",   "table": "Ζ1", "activity": "commercial",   "sub_codes": {}},
    268: {"label": "Κέρδη από επιμέτρηση — Παραγωγική",                   "table": "Ζ1", "activity": "production",   "sub_codes": {}},
    368: {"label": "Κέρδη από επιμέτρηση — Αγροτική",                     "table": "Ζ1", "activity": "agricultural", "sub_codes": {}},
    468: {"label": "Κέρδη από επιμέτρηση — Υπηρεσίες",                    "table": "Ζ1", "activity": "services",     "sub_codes": {}},
    568: {"label": "Κέρδη από επιμέτρηση — Σύνολο",                       "table": "Ζ1", "activity": "total",        "sub_codes": {}},
    169: {"label": "Φόρος Εισοδήματος - έσοδα — Εμπορική",    "table": "Ζ1", "activity": "commercial",   "sub_codes": {}},
    269: {"label": "Φόρος Εισοδήματος - έσοδα — Παραγωγική",  "table": "Ζ1", "activity": "production",   "sub_codes": {}},
    369: {"label": "Φόρος Εισοδήματος - έσοδα — Αγροτική",    "table": "Ζ1", "activity": "agricultural", "sub_codes": {}},
    469: {"label": "Φόρος Εισοδήματος - έσοδα — Υπηρεσίες",   "table": "Ζ1", "activity": "services",     "sub_codes": {}},
    569: {"label": "Φόρος Εισοδήματος - έσοδα — Σύνολο",      "table": "Ζ1", "activity": "total",        "sub_codes": {}},
    170: {"label": "Ασυνήθη έσοδα και κέρδη — Εμπορική",    "table": "Ζ1", "activity": "commercial",   "sub_codes": {}},
    270: {"label": "Ασυνήθη έσοδα και κέρδη — Παραγωγική",  "table": "Ζ1", "activity": "production",   "sub_codes": {}},
    370: {"label": "Ασυνήθη έσοδα και κέρδη — Αγροτική",    "table": "Ζ1", "activity": "agricultural", "sub_codes": {}},
    470: {"label": "Ασυνήθη έσοδα και κέρδη — Υπηρεσίες",   "table": "Ζ1", "activity": "services",     "sub_codes": {}},
    570: {"label": "Ασυνήθη έσοδα και κέρδη — Σύνολο",      "table": "Ζ1", "activity": "total",        "sub_codes": {}},

    # ── ΠΙΝΑΚΑΣ Ζ2 - ΣΥΝΟΛΟ ΕΞΟΔΩΝ ────────────────────────────────────────
    180: {"label": "Σύνολο Εξόδων — Εμπορική",       "table": "Ζ2", "activity": "commercial",   "sub_codes": {}},
    280: {"label": "Σύνολο Εξόδων — Παραγωγική",     "table": "Ζ2", "activity": "production",   "sub_codes": {}},
    380: {"label": "Σύνολο Εξόδων — Αγροτική",       "table": "Ζ2", "activity": "agricultural", "sub_codes": {}},
    480: {"label": "Σύνολο Εξόδων — Υπηρεσίες",      "table": "Ζ2", "activity": "services",     "sub_codes": {}},
    580: {"label": "Σύνολο Εξόδων — Σύνολο",         "table": "Ζ2", "activity": "total",        "sub_codes": {}},
    181: {"label": "Παροχές σε εργαζόμενους — Εμπορική",   "table": "Ζ2", "activity": "commercial",   "sub_codes": PAYROLL_SUB},
    281: {"label": "Παροχές σε εργαζόμενους — Παραγωγική", "table": "Ζ2", "activity": "production",   "sub_codes": PAYROLL_SUB},
    381: {"label": "Παροχές σε εργαζόμενους — Αγροτική",   "table": "Ζ2", "activity": "agricultural", "sub_codes": PAYROLL_SUB},
    481: {"label": "Παροχές σε εργαζόμενους — Υπηρεσίες",  "table": "Ζ2", "activity": "services",     "sub_codes": PAYROLL_SUB},
    581: {"label": "Παροχές σε εργαζόμενους — Σύνολο",     "table": "Ζ2", "activity": "total",        "sub_codes": PAYROLL_SUB},
    182: {"label": "Ζημιές επιμέτρησης περιουσιακών στοιχείων — Εμπορική",   "table": "Ζ2", "activity": "commercial",   "sub_codes": {}},
    282: {"label": "Ζημιές επιμέτρησης — Παραγωγική", "table": "Ζ2", "activity": "production",   "sub_codes": {}},
    382: {"label": "Ζημιές επιμέτρησης — Αγροτική",   "table": "Ζ2", "activity": "agricultural", "sub_codes": {}},
    482: {"label": "Ζημιές επιμέτρησης — Υπηρεσίες",  "table": "Ζ2", "activity": "services",     "sub_codes": {}},
    582: {"label": "Ζημιές επιμέτρησης — Σύνολο",     "table": "Ζ2", "activity": "total",        "sub_codes": {}},
    183: {"label": "Χρεωστικές συναλλαγματικές διαφορές — Εμπορική",   "table": "Ζ2", "activity": "commercial",   "sub_codes": {}},
    283: {"label": "Χρεωστ. συναλλαγματικές — Παραγωγική", "table": "Ζ2", "activity": "production",   "sub_codes": {}},
    383: {"label": "Χρεωστ. συναλλαγματικές — Αγροτική",   "table": "Ζ2", "activity": "agricultural", "sub_codes": {}},
    483: {"label": "Χρεωστ. συναλλαγματικές — Υπηρεσίες",  "table": "Ζ2", "activity": "services",     "sub_codes": {}},
    583: {"label": "Χρεωστ. συναλλαγματικές — Σύνολο",     "table": "Ζ2", "activity": "total",        "sub_codes": {}},
    184: {"label": "Ζημιές από διάθεση-απόσυρση μη κυκλοφορούντων — Εμπορική",   "table": "Ζ2", "activity": "commercial",   "sub_codes": {}},
    284: {"label": "Ζημιές από διάθεση μη κυκλοφορούντων — Παραγωγική",           "table": "Ζ2", "activity": "production",   "sub_codes": {}},
    384: {"label": "Ζημιές από διάθεση μη κυκλοφορούντων — Αγροτική",             "table": "Ζ2", "activity": "agricultural", "sub_codes": {}},
    484: {"label": "Ζημιές από διάθεση μη κυκλοφορούντων — Υπηρεσίες",            "table": "Ζ2", "activity": "services",     "sub_codes": {}},
    584: {"label": "Ζημιές από διάθεση μη κυκλοφορούντων — Σύνολο",               "table": "Ζ2", "activity": "total",        "sub_codes": {}},
    185: {"label": "Διάφορα λειτουργικά έξοδα — Εμπορική",   "table": "Ζ2", "activity": "commercial",   "sub_codes": OPEX_SUB},
    285: {"label": "Διάφορα λειτουργικά έξοδα — Παραγωγική", "table": "Ζ2", "activity": "production",   "sub_codes": OPEX_SUB},
    385: {"label": "Διάφορα λειτουργικά έξοδα — Αγροτική",   "table": "Ζ2", "activity": "agricultural", "sub_codes": OPEX_SUB},
    485: {"label": "Διάφορα λειτουργικά έξοδα — Υπηρεσίες",  "table": "Ζ2", "activity": "services",     "sub_codes": OPEX_SUB},
    585: {"label": "Διάφορα λειτουργικά έξοδα — Σύνολο",     "table": "Ζ2", "activity": "total",        "sub_codes": OPEX_SUB},
    186: {"label": "Χρεωστικοί τόκοι και συναφή έξοδα — Εμπορική",   "table": "Ζ2", "activity": "commercial",   "sub_codes": {}},
    286: {"label": "Χρεωστικοί τόκοι — Παραγωγική",                   "table": "Ζ2", "activity": "production",   "sub_codes": {}},
    386: {"label": "Χρεωστικοί τόκοι — Αγροτική",                     "table": "Ζ2", "activity": "agricultural", "sub_codes": {}},
    486: {"label": "Χρεωστικοί τόκοι — Υπηρεσίες",                    "table": "Ζ2", "activity": "services",     "sub_codes": {}},
    586: {"label": "Χρεωστικοί τόκοι — Σύνολο",                       "table": "Ζ2", "activity": "total",        "sub_codes": {}},
    187: {"label": "Αποσβέσεις — Εμπορική",   "table": "Ζ2", "activity": "commercial",   "sub_codes": {}},
    287: {"label": "Αποσβέσεις — Παραγωγική", "table": "Ζ2", "activity": "production",   "sub_codes": {}},
    387: {"label": "Αποσβέσεις — Αγροτική",   "table": "Ζ2", "activity": "agricultural", "sub_codes": {}},
    487: {"label": "Αποσβέσεις — Υπηρεσίες",  "table": "Ζ2", "activity": "services",     "sub_codes": {}},
    587: {"label": "Αποσβέσεις — Σύνολο",     "table": "Ζ2", "activity": "total",        "sub_codes": {}},
    188: {"label": "Ασυνήθη έξοδα, ζημιές και πρόστιμα — Εμπορική",   "table": "Ζ2", "activity": "commercial",   "sub_codes": {}},
    288: {"label": "Ασυνήθη έξοδα — Παραγωγική", "table": "Ζ2", "activity": "production",   "sub_codes": {}},
    388: {"label": "Ασυνήθη έξοδα — Αγροτική",   "table": "Ζ2", "activity": "agricultural", "sub_codes": {}},
    488: {"label": "Ασυνήθη έξοδα — Υπηρεσίες",  "table": "Ζ2", "activity": "services",     "sub_codes": {}},
    588: {"label": "Ασυνήθη έξοδα — Σύνολο",     "table": "Ζ2", "activity": "total",        "sub_codes": {}},
    189: {"label": "Προβλέψεις (εκτός προσωπικού) — Εμπορική",   "table": "Ζ2", "activity": "commercial",   "sub_codes": {}},
    289: {"label": "Προβλέψεις — Παραγωγική",                     "table": "Ζ2", "activity": "production",   "sub_codes": {}},
    389: {"label": "Προβλέψεις — Αγροτική",                       "table": "Ζ2", "activity": "agricultural", "sub_codes": {}},
    489: {"label": "Προβλέψεις — Υπηρεσίες",                      "table": "Ζ2", "activity": "services",     "sub_codes": {}},
    589: {"label": "Προβλέψεις — Σύνολο",                         "table": "Ζ2", "activity": "total",        "sub_codes": {}},
    190: {"label": "Φόρος Εισοδήματος — Εμπορική",   "table": "Ζ2", "activity": "commercial",   "sub_codes": {}},
    290: {"label": "Φόρος Εισοδήματος — Παραγωγική", "table": "Ζ2", "activity": "production",   "sub_codes": {}},
    390: {"label": "Φόρος Εισοδήματος — Αγροτική",   "table": "Ζ2", "activity": "agricultural", "sub_codes": {}},
    490: {"label": "Φόρος Εισοδήματος — Υπηρεσίες",  "table": "Ζ2", "activity": "services",     "sub_codes": {}},
    590: {"label": "Φόρος Εισοδήματος — Σύνολο",     "table": "Ζ2", "activity": "total",        "sub_codes": {}},

    # ── ΠΙΝΑΚΑΣ Ζ3 - ΛΟΙΠΑ ΠΛΗΡΟΦΟΡΙΑΚΑ ───────────────────────────────────
    195: {"label": "Έξοδα σε ιδιοπαραγωγή — Εμπορική",   "table": "Ζ3", "activity": "commercial",   "sub_codes": {}},
    295: {"label": "Έξοδα σε ιδιοπαραγωγή — Παραγωγική", "table": "Ζ3", "activity": "production",   "sub_codes": {}},
    395: {"label": "Έξοδα σε ιδιοπαραγωγή — Αγροτική",   "table": "Ζ3", "activity": "agricultural", "sub_codes": {}},
    495: {"label": "Έξοδα σε ιδιοπαραγωγή — Υπηρεσίες",  "table": "Ζ3", "activity": "services",     "sub_codes": {}},
    595: {"label": "Έξοδα σε ιδιοπαραγωγή — Σύνολο",     "table": "Ζ3", "activity": "total",        "sub_codes": {}},
    196: {"label": "Επιδοτήσεις - Επιχορηγήσεις — Εμπορική",   "table": "Ζ3", "activity": "commercial",   "sub_codes": {}},
    296: {"label": "Επιδοτήσεις - Επιχορηγήσεις — Παραγωγική", "table": "Ζ3", "activity": "production",   "sub_codes": {}},
    396: {"label": "Επιδοτήσεις - Επιχορηγήσεις — Αγροτική",   "table": "Ζ3", "activity": "agricultural", "sub_codes": {}},
    496: {"label": "Επιδοτήσεις - Επιχορηγήσεις — Υπηρεσίες",  "table": "Ζ3", "activity": "services",     "sub_codes": {}},
    596: {"label": "Επιδοτήσεις - Επιχορηγήσεις — Σύνολο",     "table": "Ζ3", "activity": "total",        "sub_codes": {}},
    197: {"label": "Επιδοτήσεις-Επιχορηγήσεις για επενδύσεις — Εμπορική",   "table": "Ζ3", "activity": "commercial",   "sub_codes": {}},
    297: {"label": "Επιδοτήσεις-Επιχορηγήσεις για επενδύσεις — Παραγωγική", "table": "Ζ3", "activity": "production",   "sub_codes": {}},
    397: {"label": "Επιδοτήσεις-Επιχορηγήσεις για επενδύσεις — Αγροτική",   "table": "Ζ3", "activity": "agricultural", "sub_codes": {}},
    497: {"label": "Επιδοτήσεις-Επιχορηγήσεις για επενδύσεις — Υπηρεσίες",  "table": "Ζ3", "activity": "services",     "sub_codes": {}},
    597: {"label": "Επιδοτήσεις-Επιχορηγήσεις για επενδύσεις — Σύνολο",     "table": "Ζ3", "activity": "total",        "sub_codes": {}},
    198: {"label": "Λοιπά πληροφοριακά — Εμπορική",   "table": "Ζ3", "activity": "commercial",   "sub_codes": {"001": "Πωλήσεις αγαθών ΕΦΚ", "002": "Αγορές αγαθών ΕΦΚ", "003": "Πωλήσεις για αγρότες μέσω συν/σμού"}},
    298: {"label": "Λοιπά πληροφοριακά — Παραγωγική", "table": "Ζ3", "activity": "production",   "sub_codes": {"001": "Πωλήσεις αγαθών ΕΦΚ", "002": "Αγορές αγαθών ΕΦΚ", "003": "Πωλήσεις για αγρότες μέσω συν/σμού"}},
    398: {"label": "Λοιπά πληροφοριακά — Αγροτική",   "table": "Ζ3", "activity": "agricultural", "sub_codes": {"001": "Πωλήσεις αγαθών ΕΦΚ", "002": "Αγορές αγαθών ΕΦΚ", "003": "Πωλήσεις για αγρότες μέσω συν/σμού"}},
    498: {"label": "Λοιπά πληροφοριακά — Υπηρεσίες",  "table": "Ζ3", "activity": "services",     "sub_codes": {"001": "Πωλήσεις αγαθών ΕΦΚ", "002": "Αγορές αγαθών ΕΦΚ", "003": "Πωλήσεις για αγρότες μέσω συν/σμού"}},
    598: {"label": "Λοιπά πληροφοριακά — Σύνολο",     "table": "Ζ3", "activity": "total",        "sub_codes": {"001": "Πωλήσεις αγαθών ΕΦΚ", "002": "Αγορές αγαθών ΕΦΚ", "003": "Πωλήσεις για αγρότες μέσω συν/σμού"}},

    # Πωλήσεις παγίων
    800: {"label": "Πωλήσεις παγίων — Εμπορική",   "table": "Ζ3", "activity": "commercial",   "sub_codes": ASSETS_SUB},
    820: {"label": "Πωλήσεις παγίων — Παραγωγική", "table": "Ζ3", "activity": "production",   "sub_codes": ASSETS_SUB},
    840: {"label": "Πωλήσεις παγίων — Αγροτική",   "table": "Ζ3", "activity": "agricultural", "sub_codes": ASSETS_SUB},
    860: {"label": "Πωλήσεις παγίων — Υπηρεσίες",  "table": "Ζ3", "activity": "services",     "sub_codes": ASSETS_SUB},
    880: {"label": "Πωλήσεις παγίων — Σύνολο",     "table": "Ζ3", "activity": "total",        "sub_codes": ASSETS_SUB},
    801: {"label": "Πωλήσεις για λογαριασμό Τρίτων — Εμπορική",   "table": "Ζ3", "activity": "commercial",   "sub_codes": ASSETS_SUB},
    821: {"label": "Πωλήσεις για λογαριασμό Τρίτων — Παραγωγική", "table": "Ζ3", "activity": "production",   "sub_codes": ASSETS_SUB},
    841: {"label": "Πωλήσεις για λογαριασμό Τρίτων — Αγροτική",   "table": "Ζ3", "activity": "agricultural", "sub_codes": ASSETS_SUB},
    861: {"label": "Πωλήσεις για λογαριασμό Τρίτων — Υπηρεσίες",  "table": "Ζ3", "activity": "services",     "sub_codes": ASSETS_SUB},
    881: {"label": "Πωλήσεις για λογαριασμό Τρίτων — Σύνολο",     "table": "Ζ3", "activity": "total",        "sub_codes": ASSETS_SUB},
    802: {"label": "Αγορές ενσώματων παγίων χρήσης — Εμπορική",   "table": "Ζ3", "activity": "commercial",   "sub_codes": ASSETS_SUB},
    822: {"label": "Αγορές ενσώματων παγίων χρήσης — Παραγωγική", "table": "Ζ3", "activity": "production",   "sub_codes": ASSETS_SUB},
    842: {"label": "Αγορές ενσώματων παγίων χρήσης — Αγροτική",   "table": "Ζ3", "activity": "agricultural", "sub_codes": ASSETS_SUB},
    862: {"label": "Αγορές ενσώματων παγίων χρήσης — Υπηρεσίες",  "table": "Ζ3", "activity": "services",     "sub_codes": ASSETS_SUB},
    882: {"label": "Αγορές ενσώματων παγίων χρήσης — Σύνολο",     "table": "Ζ3", "activity": "total",        "sub_codes": ASSETS_SUB},
    803: {"label": "Αγορές μη ενσώματων παγίων χρήσης — Εμπορική",   "table": "Ζ3", "activity": "commercial",   "sub_codes": ASSETS_SUB},
    823: {"label": "Αγορές μη ενσώματων παγίων χρήσης — Παραγωγική", "table": "Ζ3", "activity": "production",   "sub_codes": ASSETS_SUB},
    843: {"label": "Αγορές μη ενσώματων παγίων χρήσης — Αγροτική",   "table": "Ζ3", "activity": "agricultural", "sub_codes": ASSETS_SUB},
    863: {"label": "Αγορές μη ενσώματων παγίων χρήσης — Υπηρεσίες",  "table": "Ζ3", "activity": "services",     "sub_codes": ASSETS_SUB},
    883: {"label": "Αγορές μη ενσώματων παγίων χρήσης — Σύνολο",     "table": "Ζ3", "activity": "total",        "sub_codes": ASSETS_SUB},
    804: {"label": "Εσωτερικές πράξεις ΕΕΣΔΟΠ — Εμπορική",   "table": "Ζ3", "activity": "commercial",   "sub_codes": {}},
    824: {"label": "Εσωτερικές πράξεις ΕΕΣΔΟΠ — Παραγωγική", "table": "Ζ3", "activity": "production",   "sub_codes": {}},
    844: {"label": "Εσωτερικές πράξεις ΕΕΣΔΟΠ — Αγροτική",   "table": "Ζ3", "activity": "agricultural", "sub_codes": {}},
    864: {"label": "Εσωτερικές πράξεις ΕΕΣΔΟΠ — Υπηρεσίες",  "table": "Ζ3", "activity": "services",     "sub_codes": {}},
    884: {"label": "Εσωτερικές πράξεις ΕΕΣΔΟΠ — Σύνολο",     "table": "Ζ3", "activity": "total",        "sub_codes": {}},
    805: {"label": "Πράξεις ΕΕΣΔΟΠ με τρίτους — Εμπορική",   "table": "Ζ3", "activity": "commercial",   "sub_codes": {}},
    825: {"label": "Πράξεις ΕΕΣΔΟΠ με τρίτους — Παραγωγική", "table": "Ζ3", "activity": "production",   "sub_codes": {}},
    845: {"label": "Πράξεις ΕΕΣΔΟΠ με τρίτους — Αγροτική",   "table": "Ζ3", "activity": "agricultural", "sub_codes": {}},
    865: {"label": "Πράξεις ΕΕΣΔΟΠ με τρίτους — Υπηρεσίες",  "table": "Ζ3", "activity": "services",     "sub_codes": {}},
    885: {"label": "Πράξεις ΕΕΣΔΟΠ με τρίτους — Σύνολο",     "table": "Ζ3", "activity": "total",        "sub_codes": {}},
    806: {"label": "Χρηματοδοτήσεις/Επιχορηγήσεις κοινωφελούς φορέα — Εμπορική",   "table": "Ζ3", "activity": "commercial",   "sub_codes": {}},
    826: {"label": "Χρηματοδοτήσεις/Επιχορηγήσεις κοινωφελούς φορέα — Παραγωγική", "table": "Ζ3", "activity": "production",   "sub_codes": {}},
    846: {"label": "Χρηματοδοτήσεις/Επιχορηγήσεις κοινωφελούς φορέα — Αγροτική",   "table": "Ζ3", "activity": "agricultural", "sub_codes": {}},
    866: {"label": "Χρηματοδοτήσεις/Επιχορηγήσεις κοινωφελούς φορέα — Υπηρεσίες",  "table": "Ζ3", "activity": "services",     "sub_codes": {}},
    886: {"label": "Χρηματοδοτήσεις/Επιχορηγήσεις κοινωφελούς φορέα — Σύνολο",     "table": "Ζ3", "activity": "total",        "sub_codes": {}},
    999: {"label": "Σύνολο εισπραχθείσας επιστρεπτέας προκαταβολής",                  "table": "Ζ3", "activity": "common",        "sub_codes": {}},
}

# ---------------------------------------------------------------------------
# Βοηθητικές συναρτήσεις
# ---------------------------------------------------------------------------

def lookup(e3_float_code: float) -> dict:
    """
    Αναζητά κωδικό Ε3 από την float αναπαράσταση που χρησιμοποιείται στα Excel.
    π.χ.  461.001  →  {"label": "...", "table": "Ζ1", "activity": "services",
                        "sub_label": "Χονδρικές - Επιτηδευματιών"}
    Επιστρέφει None αν δεν βρεθεί.
    """
    if e3_float_code is None:
        return None

    # Χωρίζουμε σε κύριο + υποκωδικό
    parts = f"{e3_float_code:.3f}".split(".")   # e.g. "461.001"
    main_code = int(parts[0])
    sub_code  = parts[1] if len(parts) > 1 else "000"   # e.g. "001"

    entry = E3_FIELD_MAP.get(main_code)
    if entry is None:
        return None

    sub_label = entry["sub_codes"].get(sub_code, "")
    return {
        "code": main_code,
        "sub_code": sub_code,
        "label": entry["label"],
        "sub_label": sub_label,
        "table": entry["table"],
        "activity": entry["activity"],
        "full_description": f"{entry['label']}" + (f" / {sub_label}" if sub_label else ""),
    }


def describe(e3_float_code: float) -> str:
    """Σύντομη περιγραφή ενός κωδικού Ε3."""
    r = lookup(e3_float_code)
    if r is None:
        return f"Άγνωστος κωδικός {e3_float_code}"
    return r["full_description"]


# Ομαδοποίηση κωδικών ανά πίνακα (για UI)
def group_by_table(e3_results: list) -> dict:
    """
    Δέχεται λίστα από {"e3_code": float, "amount": float, ...}
    Επιστρέφει dict: {"Ζ1": [...], "Ζ2": [...], ...}
    """
    from collections import defaultdict
    grouped = defaultdict(list)
    for item in e3_results:
        r = lookup(item["e3_code"])
        table = r["table"] if r else "Άγνωστο"
        grouped[table].append({**item, **(r or {}), "description": describe(item["e3_code"])})
    return dict(grouped)
