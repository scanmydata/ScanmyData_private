# Οδηγίες για Agents — Σύνδεση και Σωστή Πλοήγηση

Σκοπός
- Να παρέχει σαφείς, επαναλήψιμες οδηγίες για αυτοματοποιημένους agents (π.χ. Playwright, Selenium) ώστε να κάνουν login και να πλοηγηθούν σωστά στην web εφαρμογή.

Προαπαιτούμενα
- URL εφαρμογής (π.χ. https://example.com/login)
- Λογαριασμός agent (όνομα χρήστη/κλειδί) με τα κατάλληλα δικαιώματα.
- Ασφαλής πρόσβαση σε διαχειριζόμενα credentials (π.χ. Vault, environment vars).
- Εργαλεία browser automation (Playwright / Selenium) και headless browser.

Γενικές αρχές
- Μην ενσωματώνετε credentials στο repo. Χρησιμοποιήστε μυστικά.
- Να υπάρχει λογική αναμονής (explicit waits) για στοιχεία και φόρτωση σελίδας.
- Να καταγράφεται (log) κάθε βήμα και να αποθηκεύονται screenshots σε σφάλμα.
- Αν υπάρχει captcha ή MFA που δεν μπορεί να αυτοματοποιηθεί, σταματήστε και ειδοποιήστε ανθρώπινη παρέμβαση.

Βήματα σύνδεσης (Login) — Τοπική Εφαρμογή
- URL εφαρμογής: `http://127.0.0.1:5001`
- Email: `douradonis@hotmail.com`
- Password: `12345678`
- Σελίδα login: `http://127.0.0.1:5001/firebase-auth/login`
- Selectors: `input[name="email"]`, `input[name="password"]`, `button[type="submit"]`

1. Μεταβείτε στο `http://127.0.0.1:5001/firebase-auth/login`
2. Συμπληρώστε `input[name="email"]` με `douradonis@hotmail.com`
3. Συμπληρώστε `input[name="password"]` με `12345678`
4. Κάντε κλικ στο `button[type="submit"]`
5. Αναμένετε ανακατεύθυνση (redirect) - συνήθως πηγαίνει στο `/groups`

ΣΗΜΑΝΤΙΚΟ: Επιλογή ενεργής ομάδας και πελάτη (πρέπει ΠΑΝΤΑ να γίνεται πριν από οποιοδήποτε άλλο τεστ)
6. Μεταβείτε στο `/groups` και επιλέξτε την ομάδα **tony** (κάντε κλικ στο αντίστοιχο link/κουμπί)
7. Μεταβείτε στο `/credentials` και ορίστε ενεργό πελάτη **ΛΟΥΓΑΡΗΣ ΣΠΥΡΟΣ** (ΑΦΜ: 036209456)
8. Επιβεβαιώστε ότι το header δείχνει "tony" ως ενεργή ομάδα και "ΛΟΥΓΑΡΗΣ ΣΠΥΡΟΣ" ως ενεργό πελάτη

Επιβεβαίωση: αν δεν εντοπιστεί το στοιχείο μετά από timeout (π.χ. 15s), πάρτε screenshot, log το σφάλμα και επαναλάβετε μέχρι 2 φορές πριν αποτύχετε.

Διαχείριση 2FA / MFA
- Αν εμφανίζεται 2FA: ελέγξτε αν υπάρχει API/endpoint για προσωρινά tokens. Αν όχι, αποθηκεύστε την κατάσταση και απαιτήστε ανθρώπινη παρέμβαση.

Πλοήγηση (Navigation)
- Βασικός κανόνας: περιμένετε πάντα για το επόμενο στοιχείο πριν αλληλεπιδράσετε.
- Μετά το login, ο agent πρέπει να πλοηγηθεί στις βασικές σελίδες με συγκεκριμένες ενέργειες:
  - Dashboard: κλικ σε `[Μενου → Dashboard]` ή direct navigate σε `/dashboard` και επαλήθευση στοιχείου `h1:contains("Dashboard")`.
  - Αναζητήσεις / Listings: άνοιγμα σελίδας `/items` και επαλήθευση λίστας `ul.items` ή `table#items`.
  - Προφίλ/Ρυθμίσεις: άνοιγμα `nav a[href="/profile"]` και επαλήθευση φορμαρισμένων στοιχείων.

Παραδείγματα εντολών (Playwright-like pseudocode)
- Navigation & login skeleton:

```javascript
await page.goto('https://YOUR_APP_DOMAIN/login');
await page.waitForSelector('#username');
await page.fill('#username', process.env.AGENT_USER);
await page.fill('#password', process.env.AGENT_PASS);
await page.click('button[type="submit"]');
await page.waitForSelector('nav .user-avatar', { timeout: 15000 });
```

Έλεγχοι εγκυρότητας
- Επαληθεύστε ότι τα βασικά στοιχεία της σελίδας υπάρχουν πριν συνεχίσετε.
- Ελέγξτε HTTP status codes όταν κάνετε direct API calls.

Αντιμετώπιση σφαλμάτων
- Σφάλμα φόρτωσης: retry 2 φορές με αύξουσα καθυστέρηση (2s, 5s).
- Σφάλμα σύνδεσης: πάρε screenshot, log, και αποστολή ειδοποίησης (email / webhook).
- Captcha/MFA: abort και alert για χειροκίνητη επίλυση.

Ασφάλεια
- Μην logάρετε ευαίσθητα (passwords, tokens).
- Χρησιμοποιήστε secure storage για credentials.

Πώς να δοκιμάσετε το workflow τοπικά
- Ρυθμίστε env vars: `AGENT_USER`, `AGENT_PASS`, `APP_URL`.
- Τρέξτε το test σε headless=false για εποπτεία.

Σημειώσεις για developers
- Προσθέστε επιπλέον selectors/διαδρομές εδώ αν αλλάξει το UI.
- Καταγράψτε κάθε αλλαγή στο UI ως νέα έκδοση αυτού του αρχείου.

Τέλος
- Ενημερώστε την ομάδα όταν το automation απαιτεί νέα δικαιώματα ή πρόσβαση σε endpoints.

Καταγραφή ενεργειών (Live Log)
- Σκοπός: Να καταγράφονται ζωντανά οι ενέργειες που εκτελούνται από agents ώστε οι developers/διαχειριστές να βλέπουν τι συνέβη και γιατί.

Παραδείγματα καταγραφής (τρέχουσα κατάσταση)
- [2026-05-29T17:16] Πατήθηκε «Αποδοχή» στα cookies στη σελίδα `http://127.0.0.1:5001/`.
- [2026-05-29T17:16] Εγινε προσπάθεια σύνδεσης με email `douradonis@hotmail.com` (password masked). Η αίτηση σύνδεσης υποβλήθηκε.
- [2026-05-29T17:18] Μετά τη σύνδεση, έγινε προσπάθεια πλοήγησης στη σελίδα ομάδων `/groups` αλλά η σελίδα δεν φόρτωσε σωστά (πιθανό 404 / backend route issue).
- [2026-05-29T17:18] Στην κεντρική σελίδα εντοπίστηκαν σύνδεσμοι για επιλογή ομάδας (`/groups`) και ενεργού πελάτη (`/credentials`).
- [2026-05-29T17:18] Προτάσεις UI/λειτουργικότητας προστέθηκαν στην ενότητα "Σημειώσεις για developers".
- [2026-05-29T17:19] Επιλέχθηκε ομάδα `douras` από τη σελίδα ` /groups` (header δείχνει `douras`).
- [2026-05-29T17:21] Ορίστηκε ενεργός πελάτης `ΛΟΥΓΑΡΗΣ ΣΠΥΡΟΣ ΠΑΝΑΓΙΩΤΗΣ` (AFM 036209456) μέσω ` /credentials` — επιτυχής (UI confirmation εμφανίστηκε).
 - [2026-05-29T17:24] Εφαρμόστηκε hotfix: το `updateActiveCredentialInDOM` επεκτάθηκε ώστε να κρύβει το banner `#globalNoCredentialBanner` και να αλλάζει το style του header badge σε ενεργό (`bg-sky-600 text-white`). Αυτό διορθώνει το partial-reload issue όπου το κίτρινο μήνυμα παρέμενε.
 - [2026-05-29T17:24] Επαλήθευση: επιλέχθηκε άλλο credential και το partial update ενημέρωσε σωστά το header (έγινε visible το "Όνομα (VAT)") και το κίτρινο banner εξαφανίστηκε.
 - [2026-05-29T17:33] Εφαρμόστηκε CSS fix στο `e3_check.html` για το `.e3-expand-icon`: πρόσθεσα `vertical-align: middle`, `line-height:1`, και μείωσα/έθεσα ελεγχόμενο `font-size` & `font-weight` ώστε να αποφεύγονται τα πολύ μεγάλα μαύρα βέλη που εμφανίζονταν κάτω από τις σειρές.
 - [2026-05-29T17:42] Εφαρμόστηκε CSS light-mode color normalization στο `e3_check.html`: έκανα τα στοιχεία με `text-gray-600/700/800` μέσα στο `#e3CheckForm` να έχουν `color: #6b7280` (ίδιο με το `#brainLoadingNote`) για καλύτερη αναγνωσιμότητα στο light theme.
 - [2026-05-29T17:50] Επέκτεινα το light-mode color normalization στο `#appShell` και `#analyticTab` του `e3_check.html` ώστε παράγραφοι και μικρά secondary κείμενα (π.χ. το "Επιλέξτε τη ροή της σελίδας...") να γίνουν σαφέστερα σε light theme.
 - [2026-05-29T17:58] Προστέθηκε delegated click handler στο `templates/base.html` για anchors μέσα σε `.help-attachments` ώστε τα επισυναπτόμενα να ανοίγουν αξιόπιστα (με `window.open`) μετά από partial reloads που μερικές φορές μπλοκάρουν το default behavior.
 - [2026-05-29T18:05] Πρόσθεσα καθολικό CSS στο `templates/base.html` για να επιβάλλεται σκούρο κείμενο (`#111827`) σε header active-customer anchors και σε inputs/selects στο light theme, ώστε μετά από partial-nav το κείμενο να παραμένει ορατό.
 - [2026-05-29T18:05] Πρόσθεσα hooks στο partial-nav (`swapPage`) για να ανανεώνονται οι λίστες του E3 Brain μετά από partial navigation: καλούνται `fetchE3BrainActiveClients`, `fetchE3BrainCredentials`, `renderBrainActiveClientSelect` και `syncTopCredentialToBrain` όταν το μονοπάτι είναι `/e3_check`.
 - [2026-05-29T18:05] Πρόσθεσα cleanup για stray SVG/text nodes και βελτίωσα την init του `flatpickr` ώστε να χρησιμοποιεί το ελληνικό locale μόνο αν το locale bundle φορτώθηκε — αυτό αποφεύγει errors όταν οι CDN locale requests μπλοκάρονται.

Οδηγίες για συνεχή ενημέρωση
- Κάθε agent που τρέχει automation πρέπει να προσθέτει μία γραμμή με timestamp, ενέργεια και αποτέλεσμα σε αυτήν την ενότητα.
- Αν προκύψει σφάλμα (HTTP error, captcha, MFA), προσθέστε λεπτομέρειες και το path του screenshot.
- Μην καταχωρείτε ευαίσθητα δεδομένα (passwords, tokens) — αναφέρετε μόνο `***masked***` ή `from secrets`.

Σημείωση
- Θα ενημερώνω αυτό το τμήμα κάθε φορά που εκτελώ ενέργειες στην ανοιχτή σελίδα. Αν θες, μπορώ να επιχειρήσω ξανά να φορτώσω `/groups` ή να ανοίξω `/credentials` και θα καταγράψω τα αποτελέσματα εδώ.
