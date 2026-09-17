import sqlite3, json, time, os

DB_PATH = os.path.expanduser("~/pw_bot/pw.db")

def _conn():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c

def init_db():
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS user_token (
            user_id INTEGER PRIMARY KEY, token TEXT NOT NULL, created_at REAL);
        CREATE TABLE IF NOT EXISTS user_session (
            user_id INTEGER PRIMARY KEY, batch_slug TEXT, subject_slug TEXT,
            topic_slug TEXT, updated_at REAL);
        CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY, value TEXT, expires_at REAL);
        CREATE TABLE IF NOT EXISTS otp_pending (
            user_id INTEGER PRIMARY KEY, phone TEXT, random_id TEXT, created_at REAL);
        CREATE TABLE IF NOT EXISTS donations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            name TEXT, amount REAL, txn_note TEXT, created_at REAL);
        CREATE TABLE IF NOT EXISTS pending_payments (
            user_id INTEGER PRIMARY KEY, name TEXT, base_amount REAL,
            unique_amount REAL, created_at REAL);
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            action TEXT, item_name TEXT, detail TEXT, created_at REAL);
        CREATE TABLE IF NOT EXISTS rate_limit (
            user_id INTEGER PRIMARY KEY, hour_start REAL, count INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS relay_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT, when_str TEXT,
            title TEXT, content TEXT, package_name TEXT, received_at REAL);
        """)

def save_token(u, t):
    with _conn() as c:
        c.execute("""INSERT INTO user_token (user_id, token, created_at)
                     VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET
                     token=excluded.token, created_at=excluded.created_at""",
                  (u, t, time.time()))

def get_token(u):
    with _conn() as c:
        r = c.execute("SELECT token FROM user_token WHERE user_id=?", (u,)).fetchone()
        return r["token"] if r else None

def get_all_users():
    with _conn() as c:
        return [r["user_id"] for r in c.execute("SELECT user_id FROM user_token")]

def save_session(u, batch_slug=None, subject_slug=None, topic_slug=None):
    with _conn() as c:
        c.execute("""INSERT INTO user_session
                     (user_id, batch_slug, subject_slug, topic_slug, updated_at)
                     VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET
                     batch_slug=excluded.batch_slug, subject_slug=excluded.subject_slug,
                     topic_slug=excluded.topic_slug, updated_at=excluded.updated_at""",
                  (u, batch_slug, subject_slug, topic_slug, time.time()))

def get_session(u):
    with _conn() as c:
        r = c.execute("SELECT * FROM user_session WHERE user_id=?", (u,)).fetchone()
        if not r:
            return {"user_id": u, "batch_slug": None, "subject_slug": None, "topic_slug": None}
        return dict(r)

def cache_set(k, v, ttl=1800):
    with _conn() as c:
        c.execute("""INSERT INTO cache (key, value, expires_at)
                     VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET
                     value=excluded.value, expires_at=excluded.expires_at""",
                  (k, json.dumps(v), time.time() + ttl))

def cache_get(k):
    with _conn() as c:
        r = c.execute("SELECT value, expires_at FROM cache WHERE key=?", (k,)).fetchone()
        if not r or r["expires_at"] < time.time(): return None
        return json.loads(r["value"])

def save_otp_pending(u, phone, rid):
    with _conn() as c:
        c.execute("""INSERT INTO otp_pending (user_id, phone, random_id, created_at)
                     VALUES (?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET
                     phone=excluded.phone, random_id=excluded.random_id,
                     created_at=excluded.created_at""",
                  (u, phone, rid, time.time()))

def get_otp_pending(u):
    with _conn() as c:
        r = c.execute("SELECT * FROM otp_pending WHERE user_id=?", (u,)).fetchone()
        return dict(r) if r else None

def clear_otp_pending(u):
    with _conn() as c:
        c.execute("DELETE FROM otp_pending WHERE user_id=?", (u,))

def set_setting(k, v):
    with _conn() as c:
        c.execute("""INSERT INTO settings (key, value) VALUES (?, ?)
                     ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (k, v))

def get_setting(k, default=None):
    with _conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
        return r["value"] if r else default

def add_history(u, a, n, d=""):
    with _conn() as c:
        c.execute("""INSERT INTO history (user_id, action, item_name, detail, created_at)
                     VALUES (?, ?, ?, ?, ?)""", (u, a, n, d, time.time()))

def check_rate_limit(u, max_per_hour=20):
    now = time.time()
    with _conn() as c:
        r = c.execute("SELECT * FROM rate_limit WHERE user_id=?", (u,)).fetchone()
        if not r or now - r["hour_start"] > 3600:
            c.execute("""INSERT INTO rate_limit (user_id, hour_start, count)
                         VALUES (?, ?, 1) ON CONFLICT(user_id) DO UPDATE SET
                         hour_start=excluded.hour_start, count=1""", (u, now))
            return True, max_per_hour - 1
        if r["count"] >= max_per_hour: return False, 0
        c.execute("UPDATE rate_limit SET count=count+1 WHERE user_id=?", (u,))
        return True, max_per_hour - r["count"] - 1

def add_donation(u, name, amt, note=""):
    with _conn() as c:
        c.execute("""INSERT INTO donations (user_id, name, amount, txn_note, created_at)
                     VALUES (?, ?, ?, ?, ?)""", (u, name, amt, note, time.time()))

def get_total_donated():
    with _conn() as c:
        return c.execute("SELECT COALESCE(SUM(amount),0) s FROM donations").fetchone()["s"]

def get_donor_count():
    with _conn() as c:
        return c.execute("SELECT COUNT(*) c FROM donations").fetchone()["c"]

def get_user_donated(u):
    with _conn() as c:
        return c.execute("SELECT COALESCE(SUM(amount),0) s FROM donations WHERE user_id=?",
                         (u,)).fetchone()["s"]

def get_donor_badge(total):
    if total >= 1000: return "💎 Diamond Donor"
    if total >= 500: return "🥇 Gold Donor"
    if total >= 200: return "🥈 Silver Donor"
    if total >= 100: return "🥉 Bronze Donor"
    if total > 0: return "💝 Supporter"
    return ""

init_db()
print(f"✅ DB ready: {DB_PATH}")
