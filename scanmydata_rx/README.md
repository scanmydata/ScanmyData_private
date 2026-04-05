# ScanmyData — Reflex Frontend

Αυτός ο φάκελος περιέχει το **νέο Reflex frontend** για το ScanmyData, το οποίο αντικαθιστά
όλα τα HTML/Jinja2 templates με σύγχρονα Python/React components.

## Αρχιτεκτονική

```
┌─────────────────────────┐          ┌─────────────────────────┐
│  Reflex Frontend        │  httpx   │  Flask API Backend      │
│  Port: 3000             │ ──────►  │  Port: 5001             │
│  scanmydata_rx/         │          │  app.py                 │
└─────────────────────────┘          └─────────────────────────┘
```

- **Flask** (port 5001): Διαχειρίζεται όλη τη business logic, authentication, AADE API, database
- **Reflex** (port 3000): Αποδίδει το UI, καλεί Flask APIs μέσω `httpx`

## Σελίδες

| Route | Αρχείο | Περιγραφή |
|-------|--------|-----------|
| `/` | `pages/home.py` | Αρχική σελίδα / Landing |
| `/firebase-auth/login` | `pages/login.py` | Σύνδεση |
| `/firebase-auth/signup` | `pages/signup.py` | Εγγραφή |
| `/firebase-auth/forgot-password` | `pages/forgot_password.py` | Ξεχάσατε κωδικό |
| `/firebase-auth/profile` | `pages/profile.py` | Προφίλ χρήστη |
| `/fetch` | `pages/fetch.py` | Λήψη παραστατικών MYDATA |
| `/list` | `pages/list_invoices.py` | Λίστα παραστατικών |
| `/search` | `pages/search.py` | Αναζήτηση MARK |
| `/credentials` | `pages/credentials.py` | Διαχείριση Credentials |
| `/credentials/edit/<name>` | `pages/credentials_edit.py` | Επεξεργασία Credential |
| `/profiles` | `pages/profiles.py` | Προφίλ λογαριασμών |
| `/custom-categories` | `pages/custom_categories.py` | Προσαρμοσμένες κατηγορίες |
| `/epsilon-preview` | `pages/epsilon_preview.py` | Epsilon preview |
| `/auth/groups` | `pages/groups.py` | Διαχείριση ομάδων |
| `/auth/account` | `pages/account.py` | Ρυθμίσεις λογαριασμού |
| `/terms` | `pages/terms.py` | Όροι χρήσης |
| `/privacy` | `pages/privacy.py` | Πολιτική απορρήτου |
| `/admin/dashboard` | `pages/admin/dashboard.py` | Admin dashboard (με tabs) |
| `/admin/users` | `pages/admin/users.py` | Λίστα χρηστών (admin) |
| `/admin/users/<uid>` | `pages/admin/user_detail.py` | Λεπτομέρειες χρήστη |
| `/admin/groups` | `pages/admin/groups.py` | Λίστα ομάδων (admin) |
| `/admin/groups/<gid>` | `pages/admin/group_detail.py` | Λεπτομέρειες ομάδας |
| `/admin/settings` | `pages/admin/settings.py` | Admin ρυθμίσεις |

## Εκκίνηση

### 1. Προαπαιτούμενα

```bash
pip install -r requirements.txt   # από το root του project
```

> **Node.js** (≥18) απαιτείται για το Reflex frontend build.
> Κατεβάστε το από https://nodejs.org ή εγκαταστήστε με `nvm`.

### 2. Εκκίνηση (single command)

```bash
# Από το root του project:
python app.py
# → Flask + Reflex τρέχουν μαζί στο http://localhost:5001
```

Κατά την **πρώτη εκτέλεση** το `rx_integration.py` τρέχει αυτόματα `reflex init`
(εγκαθιστά τα npm packages κ.λπ.) πριν ξεκινήσει το Reflex. Αυτό μπορεί να πάρει
**2-5 λεπτά**. Η σελίδα "⏳ Starting ScanmyData…" ανανεώνεται μόνη της κάθε 5 δευτερόλεπτα
— απλά περιμένετε να ολοκληρωθεί η compilation.

### 3. Πρόσβαση

Ανοίξτε browser στο: **http://localhost:5001**

## Δομή αρχείων

```
scanmydata_rx/
├── rxconfig.py                      # Reflex configuration
└── scanmydata_rx/
    ├── __init__.py
    ├── scanmydata_rx.py             # Main app entry point
    ├── state.py                     # GlobalState: auth, flash, navigation
    ├── styles.py                    # Color palette (light/dark mode)
    ├── components/
    │   ├── layout.py                # Base layout (navbar + content wrapper)
    │   ├── navbar.py                # Top navbar + side drawer
    │   └── flash.py                 # Flash/toast notifications
    └── pages/
        ├── home.py                  # Landing page
        ├── login.py                 # Login
        ├── signup.py                # Sign up
        ├── forgot_password.py       # Forgot password
        ├── profile.py               # User profile
        ├── fetch.py                 # Invoice fetch
        ├── list_invoices.py         # Invoice list
        ├── search.py                # MARK search
        ├── credentials.py           # Credentials management
        ├── credentials_edit.py      # Edit credential
        ├── profiles.py              # Account profiles
        ├── custom_categories.py     # Custom categories
        ├── epsilon_preview.py       # Epsilon preview
        ├── groups.py                # Group management
        ├── account.py               # Account settings
        ├── terms.py                 # Terms of service
        ├── privacy.py               # Privacy policy
        └── admin/
            ├── dashboard.py         # Unified admin dashboard (8 tabs)
            ├── users.py             # Users list
            ├── user_detail.py       # User detail
            ├── groups.py            # Groups list
            ├── group_detail.py      # Group detail
            └── settings.py          # System settings
```

## Dark/Light Mode

Το theme toggle βρίσκεται στην navbar (πάνω δεξιά). Το Reflex χρησιμοποιεί
`rx.color_mode_cond(light=..., dark=...)` για να υποστηρίξει και τα δύο themes.

Τα χρώματα είναι τα ίδια με το αρχικό app:
- **Light**: `#f3f4f6` background, `#ffffff` cards, `#1f2937` text
- **Dark**: `#111827` background, `#1f2937` cards, `#f9fafb` text
- **Accent**: `#0284c7` (sky-600)

## Σημείωση

Το Reflex frontend καλεί το Flask backend μέσω HTTP. Βεβαιωθείτε ότι:
1. Το Flask backend τρέχει στο port 5001
2. Τα session cookies λειτουργούν σωστά (ίδιο domain ή proxy setup)
