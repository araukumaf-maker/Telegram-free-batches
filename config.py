import os

def _load_env(path=None):
    if not path:
        path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(path): return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0") or 0)
GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
UPI_ID = os.environ.get("UPI_ID", "paytm.s2shtqw@pty")
UPI_NAME = os.environ.get("UPI_NAME", "Aryan Kumar")
