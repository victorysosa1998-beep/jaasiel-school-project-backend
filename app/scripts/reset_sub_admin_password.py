"""
app/scripts/reset_sub_admin_password.py

Resets ONE sub-admin's (or any staff user's) password directly,
bypassing the frontend reset page entirely. Useful when the
frontend's reset flow is misbehaving and you need to confirm what's
actually being saved vs. what you're typing at login.

Fill in USERNAME and NEW_PASSWORD below, then deploy/run.

WHERE THIS FILE GOES
---------------------
Put it at:  app/scripts/reset_sub_admin_password.py

HOW TO RUN
-----------
Option A — one-off via Railway CLI:
    railway run python -m app.scripts.reset_sub_admin_password

Option B — via GitHub push + startup hook:
    In main.py, add:
        from app.scripts.reset_sub_admin_password import reset_sub_admin_password
    And call it once inside your startup event:
        reset_sub_admin_password()
    Push, check the logs, then REMOVE the import and the call again
    — this is a one-off, not something to leave running on every deploy.
"""
from app.db.base import SessionLocal
from app.models.models import User
from app.core.security import hash_password

# ═══════════════════════════════════════════════════════════════
# CONFIG — fill these in, then run
# ═══════════════════════════════════════════════════════════════
USERNAME     = "superadmin1"   # check /admin/sub-admins in your DB for the exact value
NEW_PASSWORD = "superadmin2026"                          # whatever you want to log in with — write it down exactly
# ═══════════════════════════════════════════════════════════════


def reset_sub_admin_password():
    db = SessionLocal()
    try:
        clean_username = USERNAME.strip().lower()
        user = db.query(User).filter(User.username == clean_username).first()
        if not user:
            print(f"[reset_sub_admin_password] FAILED — no user found with username "
                  f"{clean_username!r}. Check /admin/sub-admins for the exact username.")
            return
        print(f"[reset_sub_admin_password] Found user: id={user.id}, "
              f"full_name={user.full_name!r}, role={user.role.value}, "
              f"is_active={user.is_active}")
        if not user.is_active:
            print("[reset_sub_admin_password] WARNING — this account is marked inactive. "
                  "Even with a correct password, login will fail with 403 until it's reactivated.")
        user.password_hash = hash_password(NEW_PASSWORD)
        db.commit()
        print(f"[reset_sub_admin_password] Password reset for {user.full_name} "
              f"(username: {clean_username}). Try logging in with exactly: {NEW_PASSWORD!r}")
    except Exception as e:
        db.rollback()
        print(f"[reset_sub_admin_password] FAILED — {e}")
    finally:
        db.close()


if __name__ == "__main__":
    reset_sub_admin_password()