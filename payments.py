import time
import urllib.parse
import re
import api


def make_upi_link(upi_id, name, amount, note="Donation"):
    params = {"pa": upi_id, "pn": name, "am": f"{amount:.2f}",
              "cu": "INR", "tn": note}
    return "upi://pay?" + urllib.parse.urlencode(params)


def make_qr_url(data):
    return ("https://api.qrserver.com/v1/create-qr-code/"
            f"?size=400x400&data={urllib.parse.quote(data, safe='')}")


def create_pending(user_id, name, base_amount):
    unique_amount = round(float(base_amount), 2)
    with api._conn() as c:
        c.execute("DELETE FROM pending_payments WHERE user_id=?", (user_id,))
        c.execute("DELETE FROM relay_notifications")
        c.execute("""INSERT INTO pending_payments
                     (user_id, name, base_amount, unique_amount, created_at)
                     VALUES (?, ?, ?, ?, ?)""",
                  (user_id, name, base_amount, unique_amount, time.time()))
    return unique_amount


def get_pending(user_id):
    with api._conn() as c:
        r = c.execute("SELECT * FROM pending_payments WHERE user_id=?",
                      (user_id,)).fetchone()
        return dict(r) if r else None


def confirm_payment(user_id):
    with api._conn() as c:
        r = c.execute("SELECT * FROM pending_payments WHERE user_id=?",
                      (user_id,)).fetchone()
        if r:
            c.execute("""INSERT INTO donations
                         (user_id, name, amount, txn_note, created_at)
                         VALUES (?, ?, ?, ?, ?)""",
                      (r["user_id"], r["name"], r["base_amount"],
                       "Auto-verified", time.time()))
            c.execute("DELETE FROM pending_payments WHERE user_id=?", (user_id,))
            return dict(r)
    return None


def get_recent_notifications():
    try:
        with api._conn() as c:
            rows = c.execute("""
                SELECT when_str AS "when", title, content,
                       package_name AS packageName
                FROM relay_notifications ORDER BY id DESC LIMIT 50
            """).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"relay notif fetch err: {e}")
        return None


def match_notif_amount(notifs, expected_amount):
    if not isinstance(notifs, list):
        return None
    for n in notifs:
        pkg = (n.get("packageName") or "").lower()
        # Sirf Paytm Business
        if pkg != "com.paytm.business":
            continue
        text = f"{n.get('title') or ''} {n.get('content') or ''}"
        for a in re.findall(r"(?:Rs\.?|INR|₹)\s*([\d,]+\.?\d{0,2})",
                            text, re.I):
            try:
                amt = float(a.replace(",", ""))
                if abs(amt - expected_amount) < 0.02:
                    return {"amount": amt, "raw": text[:300],
                            "source": "paytm_business"}
            except ValueError:
                continue
    return None


def check_notification_for_pending(user_id):
    p = get_pending(user_id)
    if not p: return None
    notifs = get_recent_notifications()
    if not notifs: return None
    return match_notif_amount(notifs, p["unique_amount"])


def auto_verify(user_id):
    return check_notification_for_pending(user_id)


def find_recent_payment(amount, minutes=180):
    notifs = get_recent_notifications()
    if not notifs: return None
    for n in notifs:
        pkg = (n.get("packageName") or "").lower()
        # Sirf Paytm Business
        if pkg != "com.paytm.business": continue
        text = f"{n.get('title') or ''} {n.get('content') or ''}"
        for a in re.findall(r"(?:Rs\.?|INR|₹)\s*([\d,]+\.?\d{0,2})",
                            text, re.I):
            try:
                v = float(a.replace(",", ""))
                if abs(v - amount) < 0.02:
                    return {"amount": v, "raw": text[:300],
                            "source": "paytm_business"}
            except ValueError:
                continue
    return None
