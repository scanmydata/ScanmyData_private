# -*- coding: utf-8 -*-
"""
ld_codec.py — κωδικοποίηση/αποκωδικοποίηση αρχείων .ld του Epsilon Net HyperLog
("Αρχεία Λογιστικής Διαχείρισης").

Μορφή αρχείου (από reverse engineering του hyperLog.exe v26.7.1):

    XML κείμενο
      -> UTF-8 bytes
      -> padding με byte 0x01 μέχρι πολλαπλάσιο του 8 (πάντα 1..8 bytes)
      -> DES-CBC, key = b"3p$i10nn", IV = 32 24 56 78 9B AB CD EF
      -> Base64 σε μία γραμμή + CRLF

Το module δεν έχει υποχρεωτικές εξαρτήσεις: χρησιμοποιεί pycryptodome ή
cryptography αν υπάρχουν, αλλιώς πέφτει σε καθαρή Python υλοποίηση DES.

    from ld_codec import encode_ld, decode_ld
    open("out.ld", "wb").write(encode_ld(xml_text))
    xml_text = decode_ld(open("in.ld", "rb").read())
"""
from __future__ import annotations

import base64

KEY = b"3p$i10nn"
IV = bytes((0x32, 0x24, 0x56, 0x78, 0x9B, 0xAB, 0xCD, 0xEF))
PAD_BYTE = 0x01
BLOCK = 8

__all__ = ["encode_ld", "decode_ld", "encrypt_cbc", "decrypt_cbc", "KEY", "IV"]


# --------------------------------------------------------------------------
# DES — καθαρή Python (fallback). Χρησιμοποιεί SP-boxes και πίνακες ανά byte,
# ώστε να είναι αρκετά γρήγορη για αρχεία μερικών MB.
# --------------------------------------------------------------------------
_PC1 = [56, 48, 40, 32, 24, 16, 8, 0, 57, 49, 41, 33, 25, 17, 9, 1, 58, 50, 42, 34, 26, 18, 10, 2,
        59, 51, 43, 35, 62, 54, 46, 38, 30, 22, 14, 6, 61, 53, 45, 37, 29, 21, 13, 5, 60, 52, 44,
        36, 28, 20, 12, 4, 27, 19, 11, 3]
_PC2 = [13, 16, 10, 23, 0, 4, 2, 27, 14, 5, 20, 9, 22, 18, 11, 3, 25, 7, 15, 6, 26, 19, 12, 1,
        40, 51, 30, 36, 46, 54, 29, 39, 50, 44, 32, 47, 43, 48, 38, 55, 33, 52, 45, 41, 49, 35, 28, 31]
_SHIFTS = [1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1]
_IP = [57, 49, 41, 33, 25, 17, 9, 1, 59, 51, 43, 35, 27, 19, 11, 3, 61, 53, 45, 37, 29, 21, 13, 5,
       63, 55, 47, 39, 31, 23, 15, 7, 56, 48, 40, 32, 24, 16, 8, 0, 58, 50, 42, 34, 26, 18, 10, 2,
       60, 52, 44, 36, 28, 20, 12, 4, 62, 54, 46, 38, 30, 22, 14, 6]
_FP = [39, 7, 47, 15, 55, 23, 63, 31, 38, 6, 46, 14, 54, 22, 62, 30, 37, 5, 45, 13, 53, 21, 61, 29,
       36, 4, 44, 12, 52, 20, 60, 28, 35, 3, 43, 11, 51, 19, 59, 27, 34, 2, 42, 10, 50, 18, 58, 26,
       33, 1, 41, 9, 49, 17, 57, 25, 32, 0, 40, 8, 48, 16, 56, 24]
_E = [31, 0, 1, 2, 3, 4, 3, 4, 5, 6, 7, 8, 7, 8, 9, 10, 11, 12, 11, 12, 13, 14, 15, 16,
      15, 16, 17, 18, 19, 20, 19, 20, 21, 22, 23, 24, 23, 24, 25, 26, 27, 28, 27, 28, 29, 30, 31, 0]
_P = [15, 6, 19, 20, 28, 11, 27, 16, 0, 14, 22, 25, 4, 17, 30, 9, 1, 7, 23, 13, 31, 26, 2, 8,
      18, 12, 29, 5, 21, 10, 3, 24]
_S = [
    [14, 4, 13, 1, 2, 15, 11, 8, 3, 10, 6, 12, 5, 9, 0, 7, 0, 15, 7, 4, 14, 2, 13, 1, 10, 6, 12, 11, 9, 5, 3, 8,
     4, 1, 14, 8, 13, 6, 2, 11, 15, 12, 9, 7, 3, 10, 5, 0, 15, 12, 8, 2, 4, 9, 1, 7, 5, 11, 3, 14, 10, 0, 6, 13],
    [15, 1, 8, 14, 6, 11, 3, 4, 9, 7, 2, 13, 12, 0, 5, 10, 3, 13, 4, 7, 15, 2, 8, 14, 12, 0, 1, 10, 6, 9, 11, 5,
     0, 14, 7, 11, 10, 4, 13, 1, 5, 8, 12, 6, 9, 3, 2, 15, 13, 8, 10, 1, 3, 15, 4, 2, 11, 6, 7, 12, 0, 5, 14, 9],
    [10, 0, 9, 14, 6, 3, 15, 5, 1, 13, 12, 7, 11, 4, 2, 8, 13, 7, 0, 9, 3, 4, 6, 10, 2, 8, 5, 14, 12, 11, 15, 1,
     13, 6, 4, 9, 8, 15, 3, 0, 11, 1, 2, 12, 5, 10, 14, 7, 1, 10, 13, 0, 6, 9, 8, 7, 4, 15, 14, 3, 11, 5, 2, 12],
    [7, 13, 14, 3, 0, 6, 9, 10, 1, 2, 8, 5, 11, 12, 4, 15, 13, 8, 11, 5, 6, 15, 0, 3, 4, 7, 2, 12, 1, 10, 14, 9,
     10, 6, 9, 0, 12, 11, 7, 13, 15, 1, 3, 14, 5, 2, 8, 4, 3, 15, 0, 6, 10, 1, 13, 8, 9, 4, 5, 11, 12, 7, 2, 14],
    [2, 12, 4, 1, 7, 10, 11, 6, 8, 5, 3, 15, 13, 0, 14, 9, 14, 11, 2, 12, 4, 7, 13, 1, 5, 0, 15, 10, 3, 9, 8, 6,
     4, 2, 1, 11, 10, 13, 7, 8, 15, 9, 12, 5, 6, 3, 0, 14, 11, 8, 12, 7, 1, 14, 2, 13, 6, 15, 0, 9, 10, 4, 5, 3],
    [12, 1, 10, 15, 9, 2, 6, 8, 0, 13, 3, 4, 14, 7, 5, 11, 10, 15, 4, 2, 7, 12, 9, 5, 6, 1, 13, 14, 0, 11, 3, 8,
     9, 14, 15, 5, 2, 8, 12, 3, 7, 0, 4, 10, 1, 13, 11, 6, 4, 3, 2, 12, 9, 5, 15, 10, 11, 14, 1, 7, 6, 0, 8, 13],
    [4, 11, 2, 14, 15, 0, 8, 13, 3, 12, 9, 7, 5, 10, 6, 1, 13, 0, 11, 7, 4, 9, 1, 10, 14, 3, 5, 12, 2, 15, 8, 6,
     1, 4, 11, 13, 12, 3, 7, 14, 10, 15, 6, 8, 0, 5, 9, 2, 6, 11, 13, 8, 1, 4, 10, 7, 9, 5, 0, 15, 14, 2, 3, 12],
    [13, 2, 8, 4, 6, 15, 11, 1, 10, 9, 3, 14, 5, 0, 12, 7, 1, 15, 13, 8, 10, 3, 7, 4, 12, 5, 6, 11, 0, 14, 9, 2,
     7, 11, 4, 1, 9, 12, 14, 2, 0, 6, 10, 13, 15, 3, 5, 8, 2, 1, 14, 7, 4, 10, 8, 13, 15, 12, 9, 0, 3, 5, 6, 11],
]


def _perm_tables(table, in_bytes):
    """Πίνακες ανά byte για την εφαρμογή μιας μετάθεσης bit."""
    out_bits = len(table)
    tabs = []
    for p in range(in_bytes):
        col = [0] * 256
        for v in range(256):
            o = 0
            for j, src in enumerate(table):
                if src // 8 == p and (v >> (7 - (src % 8))) & 1:
                    o |= 1 << (out_bits - 1 - j)
            col[v] = o
        tabs.append(col)
    return tabs


def _apply(tabs, value, in_bytes):
    o = 0
    for p in range(in_bytes):
        o |= tabs[p][(value >> (8 * (in_bytes - 1 - p))) & 0xFF]
    return o


_IP_T = _perm_tables(_IP, 8)
_FP_T = _perm_tables(_FP, 8)
_PC1_T = _perm_tables(_PC1, 8)
_PC2_T = _perm_tables(_PC2, 7)
_E_T = _perm_tables(_E, 4)
_P_T = _perm_tables(_P, 4)

# SP-boxes: S-box i συνδυασμένο με τη μετάθεση P
_SP = []
for _i in range(8):
    _box = [0] * 64
    for _b in range(64):
        _row = ((_b >> 5) & 1) * 2 + (_b & 1)
        _col = (_b >> 1) & 0xF
        _box[_b] = _apply(_P_T, _S[_i][_row * 16 + _col] << (28 - 4 * _i), 4)
    _SP.append(_box)


def _key_schedule(key: bytes):
    if len(key) != 8:
        raise ValueError("το κλειδί DES πρέπει να είναι ακριβώς 8 bytes")
    k = _apply(_PC1_T, int.from_bytes(key, "big"), 8)      # 56 bits
    c, d = k >> 28, k & 0x0FFFFFFF
    out = []
    for r in range(16):
        s = _SHIFTS[r]
        c = ((c << s) | (c >> (28 - s))) & 0x0FFFFFFF
        d = ((d << s) | (d >> (28 - s))) & 0x0FFFFFFF
        out.append(_apply(_PC2_T, (c << 28) | d, 7))       # 48 bits
    return out


def _block(value: int, subkeys) -> int:
    x = _apply(_IP_T, value, 8)
    l, r = x >> 32, x & 0xFFFFFFFF
    for k in subkeys:
        e = _apply(_E_T, r, 4) ^ k
        f = (_SP[0][(e >> 42) & 0x3F] | _SP[1][(e >> 36) & 0x3F]
             | _SP[2][(e >> 30) & 0x3F] | _SP[3][(e >> 24) & 0x3F]
             | _SP[4][(e >> 18) & 0x3F] | _SP[5][(e >> 12) & 0x3F]
             | _SP[6][(e >> 6) & 0x3F] | _SP[7][e & 0x3F])
        l, r = r, l ^ f
    return _apply(_FP_T, (r << 32) | l, 8)


def _pure_des_cbc(data: bytes, key: bytes, iv: bytes, decrypt: bool) -> bytes:
    sk = _key_schedule(key)
    rk = sk[::-1] if decrypt else sk
    out = bytearray()
    prev = int.from_bytes(iv, "big")
    for i in range(0, len(data), BLOCK):
        blk = int.from_bytes(data[i:i + BLOCK], "big")
        if decrypt:
            out += (_block(blk, rk) ^ prev).to_bytes(8, "big")
            prev = blk
        else:
            prev = _block(blk ^ prev, rk)
            out += prev.to_bytes(8, "big")
    return bytes(out)


# --------------------------------------------------------------------------
# Επιλογή υλοποίησης: pycryptodome -> cryptography -> pure python
# --------------------------------------------------------------------------
def _backend():
    try:                                                  # pycryptodome
        from Crypto.Cipher import DES as _DES

        def run(data, key, iv, decrypt):
            c = _DES.new(key, _DES.MODE_CBC, iv)
            return c.decrypt(data) if decrypt else c.encrypt(data)
        return run, "pycryptodome"
    except Exception:
        pass
    try:                                                  # cryptography
        # DES με K1=K2=K3 είναι ταυτόσημο με απλό DES
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        def run(data, key, iv, decrypt):
            c = Cipher(algorithms.TripleDES(key * 3), modes.CBC(iv))
            op = c.decryptor() if decrypt else c.encryptor()
            return op.update(data) + op.finalize()
        run(b"\0" * 8, KEY, IV, False)                    # smoke-test
        return run, "cryptography"
    except Exception:
        pass
    return _pure_des_cbc, "pure-python"


_RUN, BACKEND = _backend()


def encrypt_cbc(data: bytes, key: bytes = KEY, iv: bytes = IV) -> bytes:
    if len(data) % BLOCK:
        raise ValueError("το μήκος πρέπει να είναι πολλαπλάσιο του 8")
    return _RUN(data, key, iv, False)


def decrypt_cbc(data: bytes, key: bytes = KEY, iv: bytes = IV) -> bytes:
    if len(data) % BLOCK:
        raise ValueError("το μήκος πρέπει να είναι πολλαπλάσιο του 8")
    return _RUN(data, key, iv, True)


# --------------------------------------------------------------------------
# Δημόσιο API
# --------------------------------------------------------------------------
_B64_ALPHABET = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
)


def encode_ld(xml: str) -> bytes:
    """XML κείμενο -> περιεχόμενο αρχείου .ld (bytes, έτοιμα για εγγραφή)."""
    payload = xml.encode("utf-8")
    pad = BLOCK - (len(payload) % BLOCK)          # πάντα 1..8, όπως κάνει το HyperLog
    padded = payload + bytes([PAD_BYTE]) * pad
    return base64.b64encode(encrypt_cbc(padded)) + b"\r\n"


def decode_ld(data: bytes) -> str:
    """Περιεχόμενο αρχείου .ld -> XML κείμενο."""
    b64 = bytes(ch for ch in data if ch in _B64_ALPHABET)
    cipher = base64.b64decode(b64)
    if not cipher or len(cipher) % BLOCK:
        raise ValueError("μη έγκυρο .ld: το ωφέλιμο φορτίο δεν είναι πολλαπλάσιο του 8")
    payload = decrypt_cbc(cipher).rstrip(bytes([PAD_BYTE]))
    xml = payload.decode("utf-8")
    if not xml.startswith("<?xml"):
        raise ValueError("η αποκρυπτογράφηση δεν έδωσε XML — λάθος κλειδί ή χαλασμένο αρχείο")
    return xml


if __name__ == "__main__":  # γρήγορος αυτοέλεγχος
    import sys

    KNOWN_XML = '<?xml version="1.0" encoding="iso-8859-7" ?>\r\n<DATA/>'
    KNOWN_LD = (b"UnPFv0ha0YwJDCNDG+GknhRZhvkpMd+cNqnsyzZYnEW0LgMP5FzDZcsJEfB+/JNz"
                b"A1u3X1VTpqY=\r\n")
    ok = encode_ld(KNOWN_XML) == KNOWN_LD and decode_ld(KNOWN_LD) == KNOWN_XML
    print(f"backend={BACKEND}  known-answer test: {'OK' if ok else 'FAILED'}")
    for p in sys.argv[1:]:
        with open(p, "rb") as fh:
            raw = fh.read()
        xml = decode_ld(raw)
        print(f"{p}: {len(xml)} χαρακτήρες XML, round-trip="
              f"{'ταυτόσημο' if encode_ld(xml) == raw else 'ΔΙΑΦΟΡΑ'}")
    raise SystemExit(0 if ok else 1)
