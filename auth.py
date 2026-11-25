"""Single shared company login.

Credentials come from APP_USERNAME / APP_PASSWORD env vars if set. Otherwise they are read from
credentials.json (password stored hashed). On first run a random password is generated and
printed once in the terminal. Change it any time with:  python set_password.py
"""
import json
import os
import secrets
from hmac import compare_digest
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

ROOT = Path(__file__).parent
CRED_FILE = ROOT / "credentials.json"
KEY_FILE = ROOT / "secret.key"


def save_credentials(username, password):
    CRED_FILE.write_text(json.dumps({"username": username,
                                     "hash": generate_password_hash(password)}), encoding="utf-8")


def _load():
    env_user, env_pw = os.environ.get("APP_USERNAME"), os.environ.get("APP_PASSWORD")
    if env_pw:
        return env_user or "wodo", generate_password_hash(env_pw)
    if not CRED_FILE.exists():
        pw = secrets.token_urlsafe(9)
        save_credentials("wodo", pw)
        print("\n" + "=" * 52)
        print("  First run: team login created")
        print(f"  Username: wodo\n  Password: {pw}")
        print("  Change it with:  python set_password.py")
        print("=" * 52 + "\n")
    d = json.loads(CRED_FILE.read_text(encoding="utf-8"))
    return d["username"], d["hash"]


def check(username, password):
    user, pw_hash = _load()
    ok_user = compare_digest(username.strip().lower().encode(), user.lower().encode())
    ok_pw = check_password_hash(pw_hash, password)
    return ok_user and ok_pw


def secret_key():
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    if not KEY_FILE.exists():
        KEY_FILE.write_text(secrets.token_hex(32))
    return KEY_FILE.read_text().strip()
