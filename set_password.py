"""Change the shared team login:  python set_password.py"""
from getpass import getpass

import auth

user = input("Username [wodo]: ").strip() or "wodo"
pw = getpass("New password: ")
if len(pw) < 8:
    raise SystemExit("Use at least 8 characters.")
if pw != getpass("Repeat password: "):
    raise SystemExit("Passwords do not match.")
auth.save_credentials(user, pw)
print("Saved. Restart the app is not needed; the new login works immediately.")
