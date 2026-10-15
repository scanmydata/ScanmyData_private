"""Το «όνομα εμφάνισης» (GDPR) του χρήστη αποθηκεύεται και στο Firebase.

Γιατί: το `User.display_name` ζει στην τοπική SQLite (`firebed.db`). Σε deploy όπου η βάση ξαναδημιουργείται
(νέο image / μη μόνιμος δίσκος) οι χρήστες ξαναδημιουργούνται από το Firebase στο login και το όνομα χανόταν —
ο χρήστης ξαναρωτιόταν σε κάθε σύνδεση. Εδώ το κρατάμε στο RTDB (`/users/<uid>/public_name`, ξεχωριστό κλειδί από
το `display_name` του Firebase Auth που από την εγγραφή περιέχει το τμήμα του email) και το επαναφέρουμε αυτόματα.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_KEY = "public_name"


def _uid(user) -> Optional[str]:
    uid = (getattr(user, "firebase_uid", None) or "").strip()
    return uid or None


def _clean(name) -> str:
    return " ".join(str(name or "").split())[:60]


def save_remote(user, name: str) -> bool:
    """Γράφει το όνομα εμφάνισης στο RTDB (partial update). Δεν σηκώνει ποτέ exception."""
    try:
        uid = _uid(user)
        name = _clean(name)
        if not uid or len(name) < 2 or "@" in name:
            return False
        from firebase import firebase_config
        if not firebase_config.is_firebase_enabled():
            return False
        return bool(firebase_config.firebase_update_data(f"/users/{uid}", {_KEY: name}))
    except Exception:
        logger.exception("display_name_sync.save_remote failed")
        return False


def restore_local(user) -> bool:
    """Αν ο χρήστης δεν έχει τοπικά όνομα εμφάνισης, το φέρνει από το RTDB και το αποθηκεύει τοπικά."""
    try:
        if getattr(user, "display_name", None):
            return False
        uid = _uid(user)
        if not uid:
            return False
        from firebase import firebase_config
        if not firebase_config.is_firebase_enabled():
            return False
        remote = firebase_config.firebase_read_data(f"/users/{uid}/{_KEY}")
        name = _clean(remote) if isinstance(remote, str) else ""
        if len(name) < 2 or "@" in name:
            return False
        from models import db
        user.display_name = name
        db.session.commit()
        return True
    except Exception:
        logger.exception("display_name_sync.restore_local failed")
        try:
            from models import db
            db.session.rollback()
        except Exception:
            pass
        return False
