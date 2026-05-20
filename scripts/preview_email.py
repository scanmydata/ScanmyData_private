import os
import importlib

REPO_ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
os.chdir(REPO_ROOT)

TMP_DIR = os.path.join(REPO_ROOT, 'tmp')
os.makedirs(TMP_DIR, exist_ok=True)

# Preview email using email_utils.send_password_reset (monkeypatch send_email and token creator)
from admin import email_utils

# Backups
_email_create_token_backup = getattr(email_utils, 'create_verification_token', None)
_email_send_backup = getattr(email_utils, 'send_email', None)

# Fake implementations
def _fake_create_token(user_id, token_type='email_verify', expires_in_hours=24):
    return 'PREVIEWTOKEN_EMAILUTILS'

def _fake_send_email(to_email, subject, html_body, text_body=None):
    path = os.path.join(TMP_DIR, 'preview_email_utils_reset.html')
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(html_body)
    print('Wrote preview:', path)
    return True

# Patch
email_utils.create_verification_token = _fake_create_token
email_utils.send_email = _fake_send_email

# Run preview for email_utils flow
try:
    print('Generating preview via email_utils.send_password_reset...')
    # use a dummy user id and username
    email_utils.send_password_reset('preview@example.com', 123, 'previewuser')
except Exception as e:
    print('Error generating email_utils preview:', e)

# Restore email_utils originals
if _email_create_token_backup is not None:
    email_utils.create_verification_token = _email_create_token_backup
if _email_send_backup is not None:
    email_utils.send_email = _email_send_backup

# Now preview for firebed_email_verification
from firebase import firebed_email_verification as fev

# Backups
_fev_create_token_backup = getattr(fev.FirebedEmailVerification, 'create_verification_token', None)
_fev_send_email_backup = getattr(fev, 'send_email', None)

# Fake implementations
def _fake_create_token_fev(email, token_type='password_reset', expires_hours=1):
    return 'PREVIEWTOKEN_FEV'

def _fake_send_email_fev(to_email, subject, html_body, text_body=None):
    path = os.path.join(TMP_DIR, 'preview_firebed_reset.html')
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(html_body)
    print('Wrote preview:', path)
    return True

# Patch
fev.FirebedEmailVerification.create_verification_token = staticmethod(_fake_create_token_fev)
fev.send_email = _fake_send_email_fev

# Run preview for firebed flow
try:
    print('Generating preview via FirebedEmailVerification.send_password_reset_email...')
    fev.FirebedEmailVerification.send_password_reset_email('preview@example.com')
except Exception as e:
    print('Error generating firebed preview:', e)

# Ensure preview file exists even if Firebase is not initialized
preview_fev_path = os.path.join(TMP_DIR, 'preview_firebed_reset.html')
if not os.path.exists(preview_fev_path):
    try:
        logo_url = 'http://localhost:5001/icons/scanmydata_logo_3000w.png'
        reset_url = f"http://localhost:5001/firebase-auth/reset-password?token=PREVIEWTOKEN_FEV"
        html = f'''<!doctype html>
<html><head><meta charset="utf-8"><title>Επαναφορά Κωδικού - Preview</title></head><body style="font-family:Arial, sans-serif; background:#f8fafc; margin:0; padding:20px; color:#0f172a;"> 
<div style="max-width:600px; margin:0 auto; background:#fff; border-radius:10px; box-shadow:0 4px 12px rgba(0,0,0,0.05); overflow:hidden;">
  <div style="text-align:center; padding:20px;"><img src="{logo_url}" style="height:80px; display:block; margin:0 auto;" alt="ScanmyData"/></div>
  <div style="padding:24px;">
    <h2 style="margin:0 0 12px;">🔐 Επαναφορά Κωδικού - ScanmyData</h2>
    <p style="color:#475569;">Λάβαμε αίτημα για επαναφορά του κωδικού. Πάτησε το κουμπί:</p>
    <div style="text-align:center; margin:18px 0;"><a href="{reset_url}" style="background:#ff6b6b; color:#fff; padding:14px 28px; text-decoration:none; border-radius:8px; border:2px solid #ee5a24; font-weight:700;">🔑 Επαναφορά Κωδικού</a></div>
    <p style="font-size:13px; color:#64748b;">Αν το κουμπί δεν λειτουργεί, χρησιμοποίησε το link: <br><code style="background:#f8fafc;padding:8px;border-radius:6px;display:block;word-break:break-all;">{reset_url}</code></p>
  </div>
  <div style="text-align:center;padding:12px;background:#f1f5f9;"><img src="{logo_url}" style="height:42px; opacity:0.9;"/></div>
</div>
</body></html>'''
        with open(preview_fev_path, 'w', encoding='utf-8') as fh:
            fh.write(html)
        print('Wrote fallback preview:', preview_fev_path)
    except Exception as e:
        print('Failed to write fallback preview:', e)

# Restore fev originals
if _fev_create_token_backup is not None:
    fev.FirebedEmailVerification.create_verification_token = _fev_create_token_backup
if _fev_send_email_backup is not None:
    fev.send_email = _fev_send_email_backup

print('\nPreview generation complete. Files in:', TMP_DIR)
print(' -', os.path.join(TMP_DIR, 'preview_email_utils_reset.html'))
print(' -', os.path.join(TMP_DIR, 'preview_firebed_reset.html'))
