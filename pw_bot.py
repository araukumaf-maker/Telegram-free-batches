import logging, os, sys, uuid, base64, json, time, asyncio, zipfile
import urllib.request, html as htmllib, subprocess
import telegram.error
from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
                      ReplyKeyboardMarkup, KeyboardButton)
from telegram.ext import (ApplicationBuilder, CommandHandler,
                          CallbackQueryHandler, MessageHandler, filters,
                          ContextTypes)
import requests

import config
BOT_TOKEN = config.BOT_TOKEN
ADMIN_USER_ID = config.ADMIN_USER_ID

import api, payments

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    level=logging.INFO)
log = logging.getLogger("pwbot")

RATE_LIMIT = 20
MIN_DONATION = 1
MAX_DONATION = 10000

BASE_URL = "https://api.penpencil.co"
ORG_ID = "5eb393ee95fab7468a79d189"
REFERER = "https://www.pw.live/"

PEAK_API_KEY = "pk_1ce9baea2733717dff2c503bd13a7ae53b7ec28450ee7a08"
PEAK_APP_ID = "app_53d38bce7aaf4533e5f59873"
PW_SITEKEY = "0x4AAAAAABmMfnuMzLn8_C02"


# ============================================================
# PW API
# ============================================================
def get_auth_headers(token, random_id=None):
    return {
        "Content-Type": "application/json", "Accept": "application/json",
        "Referer": REFERER, "Randomid": random_id or str(uuid.uuid4()),
        "Authorization": f"Bearer {token}",
    }


def solve_turnstile_peak():
    url = "https://api.peak.fo/solve"
    headers = {"X-API-Key": PEAK_API_KEY, "Content-Type": "application/json"}
    payload = {"task_type": "TurnstileTaskProxyLess",
               "url": "https://www.pw.live/",
               "sitekey": PW_SITEKEY, "appId": PEAK_APP_ID}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=45)
        d = r.json()
        if not d.get("success"):
            return {"success": False,
                    "error": d.get("error") or d.get("message") or "Unknown"}
        token = (d.get("data") or {}).get("token")
        if not token:
            return {"success": False, "error": f"No token: {str(d)[:200]}"}
        return {"success": True, "token": token}
    except Exception as e:
        return {"success": False, "error": str(e)}


def send_otp(phone, country_code="+91", random_id=None):
    captcha = solve_turnstile_peak()
    if not captcha.get("success"):
        return {"success": False,
                "error_message": f"Captcha: {captcha.get('error')}"}
    url = f"{BASE_URL}/v1/users/get-otp-secure?smsType=0&fallback=true"
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "Referer": REFERER, "Randomid": random_id or str(uuid.uuid4())}
    payload = {"username": phone, "countryCode": country_code,
               "organizationId": ORG_ID,
               "captchaToken": captcha["token"],
               "captchaSiteKey": PW_SITEKEY}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=20)
        d = r.json()
        if d.get("success"): return {"success": True}
        e = d.get("error", {})
        return {"success": False, "error_message": e.get("message", "Error")}
    except Exception as e:
        return {"success": False, "error_message": str(e)}


def resend_otp(phone, country_code="+91", random_id=None):
    return send_otp(phone, country_code, random_id)


def pw_get_token(phone, otp, random_id=None):
    url = f"{BASE_URL}/v3/oauth/token"
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "Referer": REFERER, "Randomid": random_id or str(uuid.uuid4())}
    payload = {"username": phone, "otp": otp,
               "client_id": "system-admin",
               "client_secret": "KjPXuAVfC5xbmgreETNMaL7z",
               "grant_type": "password", "latitude": 0, "longitude": 0,
               "organizationId": ORG_ID}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        d = r.json()
        if d.get("success") and "data" in d:
            return {"success": True, "access_token": d["data"]["access_token"]}
        e = d.get("error", {})
        return {"success": False, "error_message": e.get("message", "Error")}
    except Exception as e:
        return {"success": False, "error_message": str(e)}


def fetch_batches_all(token, page=1):
    out, seen = [], set()
    for amt in ["paid", "free"]:
        url = (f"{BASE_URL}/batch-service/v1/batches/purchased-batches"
               f"?amount={amt}&page={page}&type=ALL")
        try:
            d = requests.get(url, headers=get_auth_headers(token), timeout=15).json()
            for b in d.get("data", []) or []:
                s = b.get("slug")
                if s and s not in seen:
                    seen.add(s)
                    out.append({"name": b.get("name") or "Unnamed",
                                "slug": s, "type": amt})
        except Exception as e:
            log.error(f"batches({amt}): {e}")
    return out


def fetch_subjects_safe(token, batch_slug):
    for ep in (f"{BASE_URL}/v3/batches/{batch_slug}/details",
               f"{BASE_URL}/v2/batches/{batch_slug}/details",
               f"{BASE_URL}/v1/batches/{batch_slug}/details"):
        try:
            r = requests.get(ep, headers=get_auth_headers(token), timeout=15)
            if r.status_code != 200: continue
            d = r.json().get("data", {})
            if isinstance(d, dict) and d.get("subjects"):
                return [{"subject": s.get("subject"), "slug": s.get("slug")}
                        for s in d["subjects"]]
        except Exception: pass
    return []


def fetch_topics_safe(token, batch_slug, subject_slug):
    for ep in (f"{BASE_URL}/v2/batches/{batch_slug}/subject/{subject_slug}/topics?page=1",
               f"{BASE_URL}/v1/batches/{batch_slug}/subject/{subject_slug}/topics?page=1"):
        try:
            r = requests.get(ep, headers=get_auth_headers(token), timeout=15)
            if r.status_code != 200: continue
            t = r.json().get("data", [])
            if t:
                return [{"name": x.get("name"), "slug": x.get("slug"),
                         "notes": x.get("notes"), "exercises": x.get("exercises"),
                         "videos": x.get("videos"), "lectureVideos": x.get("lectureVideos")}
                        for x in t]
        except Exception: pass
    return []


def fetch_notes_pw(token, batch_slug, subject_slug, topic_slug, page=1):
    url = (f"{BASE_URL}/v2/batches/{batch_slug}/subject/{subject_slug}"
           f"/contents?page={page}&contentType=notes&tag={topic_slug}")
    try:
        d = requests.get(url, headers=get_auth_headers(token), timeout=15).json()
        out = []
        for e in d.get("data", []):
            for hw in e.get("homeworkIds", []):
                out.append({"topic": hw.get("topic"),
                            "attachments": [{"baseUrl": a.get("baseUrl"),
                                             "key": a.get("key"),
                                             "name": a.get("name")}
                                            for a in hw.get("attachmentIds", [])]})
        return out
    except Exception: return []


def fetch_dpp_pw(token, batch_slug, subject_slug, topic_slug, page=1):
    url = (f"{BASE_URL}/v2/batches/{batch_slug}/subject/{subject_slug}"
           f"/contents?page={page}&contentType=dpp&tag={topic_slug}")
    try:
        d = requests.get(url, headers=get_auth_headers(token), timeout=15).json()
        out = []
        for e in d.get("data", []):
            for hw in e.get("homeworkIds", []):
                out.append({"topic": hw.get("topic"),
                            "attachments": [{"baseUrl": a.get("baseUrl"),
                                             "key": a.get("key"),
                                             "name": a.get("name")}
                                            for a in hw.get("attachmentIds", [])]})
        return out
    except Exception: return []


# ============================================================
# TERMUX:API NOTIFICATION READER
# ============================================================
def _read_termux_notifications():
    try:
        out = subprocess.check_output(
            ["termux-notification-list"],
            timeout=10, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore")
        return json.loads(out)
    except FileNotFoundError:
        return None
    except Exception as e:
        log.error(f"notif read err: {e}")
        return None


def _save_notification(n):
    title = n.get("title") or ""
    content = n.get("content") or ""
    when = n.get("when") or ""
    pkg = n.get("packageName") or ""
    with api._conn() as c:
        c.execute("""INSERT INTO relay_notifications
                     (when_str, title, content, package_name, received_at)
                     VALUES (?, ?, ?, ?, ?)""",
                  (str(when), title, content, pkg, time.time()))
    log.info(f"📩 Notif: {title[:60]}")


# ============================================================
# WATCHERS
# ============================================================
async def notification_watcher():
    await asyncio.sleep(5)
    log.info("👀 Notification watcher started")
    last_seen = set()
    while True:
        try:
            notifs = await asyncio.to_thread(_read_termux_notifications)
            if notifs:
                for n in notifs:
                    pkg = (n.get("packageName") or "").lower()
                    # Sirf Paytm Business app
                    if pkg != "com.paytm.business": continue
                    key = f"{n.get('when')}|{n.get('title')}|{n.get('content')}"
                    if key in last_seen: continue
                    last_seen.add(key)
                    await asyncio.to_thread(_save_notification, n)
                if len(last_seen) > 500:
                    last_seen = set(list(last_seen)[-250:])
        except Exception as e:
            log.error(f"notif watcher err: {e}")
        await asyncio.sleep(2)


async def payment_watcher(app):
    await asyncio.sleep(3)
    log.info("💰 Payment watcher started")
    while True:
        try:
            with api._conn() as c:
                rows = c.execute("SELECT * FROM pending_payments").fetchall()
            if rows:
                notifs = payments.get_recent_notifications()
                for row in rows:
                    uid = row["user_id"]; amt = row["unique_amount"]
                    if time.time() - row["created_at"] > 1800:
                        with api._conn() as c:
                            c.execute("DELETE FROM pending_payments WHERE user_id=?", (uid,))
                        continue
                    hit = None
                    if notifs:
                        hit = payments.match_notif_amount(notifs, amt)
                    if hit:
                        donated = payments.confirm_payment(uid)
                        if donated:
                            bg = api.get_donor_badge(api.get_user_donated(uid))
                            try:
                                await app.bot.send_message(
                                    chat_id=uid,
                                    text=(f"✅ <b>Payment Received!</b>\n\n"
                                          f"💝 <b>₹{donated['base_amount']:.2f}</b>"
                                          + (f"\n{bg}" if bg else "")
                                          + "\n\nThank you so much! 🙏"),
                                    parse_mode="HTML")
                            except Exception as e:
                                log.error(f"notify: {e}")
                            log.info(f"✅ Verified {uid} ₹{amt}")
        except Exception as e:
            log.error(f"payment watcher err: {e}")
        await asyncio.sleep(1)


# ============================================================
# UI
# ============================================================
async def safe_edit(q, t, **kw):
    try:
        await q.edit_message_text(t, **kw)
    except telegram.error.BadRequest as e:
        if "Message is not modified" not in str(e): raise


def batches_kb():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📚 My Batches")]],
        resize_keyboard=True, one_time_keyboard=False, is_persistent=True)


def main_menu_kb(is_admin=False):
    rows = [
        [InlineKeyboardButton("🔐 Login with OTP", callback_data="otp_start"),
         InlineKeyboardButton("🔑 Set Token", callback_data="set_token")],
        [InlineKeyboardButton("📚 My Batches", callback_data="batches")],
        [InlineKeyboardButton("💝 Donate", callback_data="donate")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("📊 Admin Stats", callback_data="admin_stats")])
    return InlineKeyboardMarkup(rows)


def list_kb(items, prefix, back):
    rows = [[InlineKeyboardButton(it["label"][:60],
             callback_data=f"{prefix}:{it['value']}")] for it in items]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data=back)])
    return InlineKeyboardMarkup(rows)


async def show_batches(ctx, chat_id, uid, ia):
    token = api.get_token(uid)
    if not token:
        await ctx.bot.send_message(chat_id=chat_id,
            text="❌ Pehle login karo! /start → 🔐 Login with OTP.",
            reply_markup=main_menu_kb(ia)); return
    msg = await ctx.bot.send_message(chat_id=chat_id, text="⏳ Batches load...")
    batches = fetch_batches_all(token)
    if not batches:
        await msg.edit_text("❌ Koi batch nahi mila.",
                            reply_markup=main_menu_kb(ia)); return
    api.cache_set(f"batches:{uid}", batches, ttl=1800)
    items = [{"label": f"{'🆓' if b['type']=='free' else '💰'} {b['name']}",
              "value": b["slug"]} for b in batches]
    paid = sum(1 for b in batches if b["type"] == "paid")
    free = len(batches) - paid
    await msg.edit_text(
        f"📚 <b>Tumhare Batches ({len(batches)})</b>\n💰 Paid: {paid}  |  🆓 Free: {free}",
        reply_markup=list_kb(items, "batch", "main"), parse_mode="HTML")


# ============================================================
# COMMANDS
# ============================================================
async def start(update, ctx):
    u = update.effective_user; ctx.user_data.clear()
    ia = (u.id == ADMIN_USER_ID)
    txt = (f"👋 <b>Namaste, {u.first_name}!</b>\n\n"
           "🤖 <b>PW Extractor Bot</b>\n\n"
           "🔐 OTP Login | 🔑 Token Login\n📚 My Batches | 💝 Donate")
    await update.message.reply_text(txt, reply_markup=main_menu_kb(ia), parse_mode="HTML")
    await ctx.bot.send_message(
        chat_id=update.effective_chat.id,
        text="⌨️ Neeche <b>📚 My Batches</b> button:",
        reply_markup=batches_kb(), parse_mode="HTML")


async def notifdebug_cmd(update, ctx):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ Sirf admin."); return
    raw = await asyncio.to_thread(_read_termux_notifications)
    if raw is None:
        await update.message.reply_text(
            "❌ Termux:API error. Fix:\n\n"
            "1. Settings → Apps → Termux:API → Force Stop\n"
            "2. Settings → Notification Access → Termux:API OFF → ON\n"
            "3. Test: termux-notification-list | head -5")
        return
    paytm = [n for n in raw if (n.get("packageName") or "").lower() == "com.paytm.business"]
    txt = f"📱 <b>Total: {len(raw)} | Paytm: {len(paytm)}</b>\n\n"
    for n in paytm[:5]:
        txt += f"<b>⏰ {n.get('when','?')}</b>\n"
        txt += f"{htmllib.escape((n.get('title') or '')[:60])}\n"
        txt += f"{htmllib.escape((n.get('content') or '')[:100])}\n\n"
    await update.message.reply_text(txt[:4000], parse_mode="HTML")


async def verify_cmd(update, ctx):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ Sirf admin."); return
    a = ctx.args
    if len(a) < 1:
        await update.message.reply_text("Usage: /verify 1 [user_id] [name]"); return
    try: amt = float(a[0])
    except ValueError:
        await update.message.reply_text("❌ Number daalo."); return
    target_uid = int(a[1]) if len(a) > 1 else ADMIN_USER_ID
    name = " ".join(a[2:]) if len(a) > 2 else "Aryan"
    hit = payments.find_recent_payment(amt, 180)
    if not hit:
        await update.message.reply_text(f"❌ ₹{amt} ki notification nahi mili."); return
    with api._conn() as c:
        c.execute("""INSERT INTO donations (user_id, name, amount, txn_note, created_at)
                     VALUES (?, ?, ?, ?, ?)""",
                  (target_uid, name, amt, "Manual", time.time()))
    await update.message.reply_text(f"✅ Manual verify! {name} — ₹{amt}")


async def setupi_cmd(update, ctx):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ Sirf admin."); return
    a = ctx.args
    if len(a) < 1:
        await update.message.reply_text("/setupi paytm.s2shtqw@pty Aryan Kumar"); return
    api.set_setting("upi_id", a[0])
    api.set_setting("upi_name", " ".join(a[1:]) if len(a) > 1 else "PW Bot")
    await update.message.reply_text(f"✅ UPI: {a[0]}")


async def stats_cmd(update, ctx):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ Sirf admin."); return
    with api._conn() as c:
        users = c.execute("SELECT COUNT(*) c FROM user_token").fetchone()["c"]
        pending = c.execute("SELECT COUNT(*) c FROM pending_payments").fetchone()["c"]
        notifs = c.execute("SELECT COUNT(*) c FROM relay_notifications").fetchone()["c"]
    await update.message.reply_text(
        f"📊 <b>Stats</b>\n\n👥 Users: {users}\n⏳ Pending: {pending}\n"
        f"🔔 Notifs: {notifs}\n💰 Donations: ₹{api.get_total_donated():.0f}",
        parse_mode="HTML")


# ============================================================
# BUTTONS
# ============================================================
async def button_handler(update, ctx):
    q = update.callback_query; await q.answer()
    data = q.data; uid = q.from_user.id; chat_id = q.message.chat_id
    ia = (uid == ADMIN_USER_ID)

    if data == "otp_start":
        ctx.user_data.clear(); ctx.user_data["awaiting_phone"] = True
        await safe_edit(q, "📱 <b>Phone Number Daalo</b>\n\n10 digit:", parse_mode="HTML"); return

    if data == "set_token":
        ctx.user_data.clear(); ctx.user_data["awaiting_token"] = True
        await safe_edit(q, "🔑 <b>Manual Token</b>\n\nF12 → Console:\n"
            "<code>copy(Object.values(localStorage).find(v=&gt;v&amp;&amp;v.includes('eyJ')))</code>",
            parse_mode="HTML"); return

    if data == "resend_otp":
        p = api.get_otp_pending(uid)
        if not p: await safe_edit(q, "❌ Expire."); return
        await safe_edit(q, "⏳...")
        rid = str(uuid.uuid4()); r = resend_otp(p["phone"], "+91", rid)
        if r.get("success"):
            api.save_otp_pending(uid, p["phone"], rid)
            await safe_edit(q, f"✅ Sent to {p['phone']}")
        else: await safe_edit(q, f"❌ {r.get('error_message')}")
        return

    if data == "batches":
        token = api.get_token(uid)
        if not token: await safe_edit(q, "❌ Login karo!"); return
        await safe_edit(q, "⏳ Batches load...")
        batches = fetch_batches_all(token)
        if not batches:
            await safe_edit(q, "❌ None.", reply_markup=main_menu_kb(ia)); return
        api.cache_set(f"batches:{uid}", batches, ttl=1800)
        items = [{"label": f"{'🆓' if b['type']=='free' else '💰'} {b['name']}",
                  "value": b["slug"]} for b in batches]
        paid = sum(1 for b in batches if b["type"] == "paid")
        free = len(batches) - paid
        await safe_edit(q, f"📚 <b>Batches ({len(batches)})</b>\n💰 {paid} | 🆓 {free}",
                        reply_markup=list_kb(items, "batch", "main"), parse_mode="HTML"); return

    if data.startswith("batch:"):
        bs = data.split(":", 1)[1]; api.save_session(uid, batch_slug=bs)
        token = api.get_token(uid)
        await safe_edit(q, "⏳ Subjects...")
        subs = fetch_subjects_safe(token, bs)
        if not subs:
            await safe_edit(q, "❌ None.", reply_markup=main_menu_kb(ia)); return
        items = [{"label": s["subject"], "value": s["slug"]} for s in subs]
        api.cache_set(f"subjects:{uid}:{bs}", subs, ttl=1800)
        await safe_edit(q, f"📖 <b>Subjects ({len(subs)})</b>",
                        reply_markup=list_kb(items, "subject", "batch_back"), parse_mode="HTML"); return

    if data.startswith("subject:"):
        ss = data.split(":", 1)[1]; sess = api.get_session(uid)
        api.save_session(uid, batch_slug=sess.get("batch_slug"), subject_slug=ss)
        token = api.get_token(uid)
        await safe_edit(q, "⏳ Topics...")
        topics = fetch_topics_safe(token, sess.get("batch_slug"), ss)
        if not topics:
            await safe_edit(q, "❌ None.", reply_markup=main_menu_kb(ia)); return
        api.cache_set(f"topics:{uid}:{sess.get('batch_slug')}:{ss}", topics, ttl=1800)
        rows = []
        for t in topics:
            badge = ""
            if t.get("notes"): badge += "📚"
            if t.get("exercises"): badge += "📝"
            if t.get("videos") or t.get("lectureVideos"): badge += "🎬"
            rows.append([InlineKeyboardButton(f"{badge} {t['name']}"[:60],
                        callback_data=f"topic:{t['slug']}")])
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data="batch_back")])
        await safe_edit(q, f"📑 <b>Topics ({len(topics)})</b>",
                        reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML"); return

    if data.startswith("topic:"):
        ts = data.split(":", 1)[1]; sess = api.get_session(uid)
        api.save_session(uid, batch_slug=sess.get("batch_slug"),
                        subject_slug=sess.get("subject_slug"), topic_slug=ts)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📚 Notes", callback_data="notes"),
             InlineKeyboardButton("📝 DPPs", callback_data="dpps")],
            [InlineKeyboardButton("⬅️ Back", callback_data="subject_back")]])
        await safe_edit(q, "Kya bhejun?", reply_markup=kb); return

    if data in ("notes", "dpps"):
        allowed, rem = api.check_rate_limit(uid, RATE_LIMIT)
        if not allowed:
            await safe_edit(q, "🚦 Rate limit.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="main")]])); return
        sess = api.get_session(uid); token = api.get_token(uid)
        if not all([sess.get("batch_slug"), sess.get("subject_slug"), sess.get("topic_slug")]):
            await safe_edit(q, "❌ Incomplete"); return
        lbl = "Notes" if data == "notes" else "DPPs"
        await safe_edit(q, f"⏳ {lbl}...")
        try:
            fn = fetch_notes_pw if data == "notes" else fetch_dpp_pw
            entries = fn(token, sess["batch_slug"], sess["subject_slug"], sess["topic_slug"])
            files = _flat(entries)
            await send_files(q, files, lbl)
        except Exception as e:
            await safe_edit(q, f"❌ {e}")
        return

    if data == "subject_back":
        sess = api.get_session(uid)
        sl = api.cache_get(f"subjects:{uid}:{sess.get('batch_slug')}") or []
        items = [{"label": s["subject"], "value": s["slug"]} for s in sl]
        await safe_edit(q, f"📖 <b>Subjects ({len(sl)})</b>",
                        reply_markup=list_kb(items, "subject", "batch_back"), parse_mode="HTML"); return

    if data == "batch_back":
        batches = api.cache_get(f"batches:{uid}") or []
        if not batches:
            await safe_edit(q, "❌ Cache khali.", reply_markup=main_menu_kb(ia)); return
        items = [{"label": f"{'🆓' if b['type']=='free' else '💰'} {b['name']}",
                  "value": b["slug"]} for b in batches]
        paid = sum(1 for b in batches if b["type"] == "paid")
        free = len(batches) - paid
        await safe_edit(q, f"📚 <b>Batches ({len(batches)})</b>\n💰 {paid} | 🆓 {free}",
                        reply_markup=list_kb(items, "batch", "main"), parse_mode="HTML"); return

    if data == "donate":
        upi = api.get_setting("upi_id", config.UPI_ID)
        nm = api.get_setting("upi_name", config.UPI_NAME)
        tot = api.get_total_donated(); cnt = api.get_donor_count()
        txt = (f"💝 <b>Support This Bot</b>\n\n"
               f"Received: <b>₹{tot:.0f}</b> from {cnt} supporters\n\n"
               f"<b>UPI:</b> <code>{upi}</code>\n<b>Name:</b> {nm}\n\n"
               "⚡ Auto-verify: 1-2 sec me confirm!")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💌 Donate Now", callback_data="donate_start")],
            [InlineKeyboardButton("⬅️ Back", callback_data="main")]])
        await safe_edit(q, txt, reply_markup=kb, parse_mode="HTML"); return

    if data == "donate_start":
        ctx.user_data["awaiting_donation_amount"] = True
        await safe_edit(q, f"💰 <b>Kitna donate karna hai?</b>\n\n"
                        f"Amount bhejo (₹{MIN_DONATION} - ₹{MAX_DONATION}):", parse_mode="HTML"); return

    if data == "check_payment":
        await safe_edit(q, "🔍 Manual check...")
        result = payments.auto_verify(uid)
        if result:
            donated = payments.confirm_payment(uid)
            bg = api.get_donor_badge(api.get_user_donated(uid))
            await safe_edit(q,
                f"✅ <b>Payment Received!</b>\n\n💝 <b>₹{donated['base_amount']:.2f}</b>"
                + (f"\n{bg}" if bg else "") + "\n\nThank you so much! 🙏",
                reply_markup=main_menu_kb(ia), parse_mode="HTML")
        else:
            await safe_edit(q, "⏳ <b>Abhi tak nahi mila</b>\n\nAuto-verify chal raha hai.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Check Again", callback_data="check_payment")],
                    [InlineKeyboardButton("⬅️ Back", callback_data="donate")]]), parse_mode="HTML")
        return

    if data == "copy_upi":
        await q.answer(f"UPI: {api.get_setting('upi_id', config.UPI_ID)}", show_alert=True); return

    if data == "admin_stats":
        if not ia: await q.answer("Admin only", show_alert=True); return
        with api._conn() as c:
            users = c.execute("SELECT COUNT(*) c FROM user_token").fetchone()["c"]
            notifs = c.execute("SELECT COUNT(*) c FROM relay_notifications").fetchone()["c"]
        txt = (f"📊 <b>Admin Stats</b>\n\n👥 Users: {users}\n"
               f"🔔 Notifications: {notifs}\n💰 Donations: ₹{api.get_total_donated():.0f}")
        await safe_edit(q, txt, reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⬅️ Back", callback_data="main")]]), parse_mode="HTML"); return

    if data == "main":
        await safe_edit(q, "🏠 <b>Main Menu</b>",
                        reply_markup=main_menu_kb(ia), parse_mode="HTML"); return


# ============================================================
# MESSAGES
# ============================================================
async def handle_message(update, ctx):
    uid = update.effective_user.id; chat_id = update.effective_chat.id
    txt = update.message.text.strip() if update.message.text else ""
    ia = (uid == ADMIN_USER_ID)

    if txt == "📚 My Batches":
        await show_batches(ctx, chat_id, uid, ia); return

    if ctx.user_data.get("awaiting_donation_amount"):
        try:
            amt = float(txt.replace("₹", "").replace(",", "").strip())
            if amt < MIN_DONATION or amt > MAX_DONATION:
                await ctx.bot.send_message(chat_id=chat_id,
                    text=f"❌ Amount ₹{MIN_DONATION} - ₹{MAX_DONATION}"); return
        except ValueError:
            await ctx.bot.send_message(chat_id=chat_id, text="❌ Number daalo"); return
        unique_amt = payments.create_pending(
            uid, update.effective_user.first_name or "User", amt)
        upi = api.get_setting("upi_id", config.UPI_ID)
        nm = api.get_setting("upi_name", config.UPI_NAME)
        link = payments.make_upi_link(upi, nm, unique_amt, f"Donation by {uid}")
        qr_url = payments.make_qr_url(link)
        ctx.user_data.clear()
        await ctx.bot.send_photo(
            chat_id=chat_id, photo=qr_url,
            caption=(f"💝 <b>Donation QR</b>\n\n<b>Amount:</b> ₹{unique_amt:.2f}\n"
                     f"<i>(Exact amount pay karo)</i>\n\n<b>UPI:</b> <code>{upi}</code>\n\n"
                     f"⚡ Payment ke 1-2 sec baad auto thank-you!"),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Copy UPI", callback_data="copy_upi")],
                [InlineKeyboardButton("🔄 Check Now", callback_data="check_payment")]]))
        return

    if ctx.user_data.get("awaiting_phone"):
        phone = txt.replace(" ", "").replace("+91", "")
        if not phone.isdigit() or len(phone) != 10:
            await ctx.bot.send_message(chat_id=chat_id, text="❌ 10 digit."); return
        try: await update.message.delete()
        except Exception: pass
        m = await ctx.bot.send_message(chat_id=chat_id,
            text=f"⏳ Captcha solve + OTP...\n📱 <code>{phone}</code>\n\n"
                 "15-30 sec wait karo.", parse_mode="HTML")
        rid = str(uuid.uuid4()); r = send_otp(phone, "+91", rid)
        if not r.get("success"):
            await m.edit_text(f"❌ {r.get('error_message')}")
            ctx.user_data.clear(); return
        api.save_otp_pending(uid, phone, rid)
        ctx.user_data.clear(); ctx.user_data["awaiting_otp"] = True
        await m.edit_text(f"✅ OTP sent {phone}\n\nOTP type karo:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Resend OTP", callback_data="resend_otp")]]))
        return

    if ctx.user_data.get("awaiting_otp"):
        otp = txt
        try: await update.message.delete()
        except Exception: pass
        p = api.get_otp_pending(uid)
        if not p:
            await ctx.bot.send_message(chat_id=chat_id, text="❌ /start")
            ctx.user_data.clear(); return
        m = await ctx.bot.send_message(chat_id=chat_id, text="⏳ Verify...")
        r = pw_get_token(p["phone"], otp, p["random_id"])
        if not r.get("success"):
            await m.edit_text(f"❌ {r.get('error_message')}\n\nDobara try karo.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Resend", callback_data="resend_otp"),
                    InlineKeyboardButton("🏠 Main", callback_data="main")]]))
            return
        token = r.get("access_token")
        if not token:
            await m.edit_text("❌ No token."); ctx.user_data.clear(); return
        api.save_token(uid, token); api.clear_otp_pending(uid)
        ctx.user_data.clear()
        bl = fetch_batches_all(token)
        if not bl:
            await m.edit_text("✅ Login!\n\n❌ Koi batch nahi mila.",
                reply_markup=main_menu_kb(ia), parse_mode="HTML"); return
        api.cache_set(f"batches:{uid}", bl, ttl=1800)
        items = [{"label": f"{'🆓' if b['type']=='free' else '💰'} {b['name']}",
                  "value": b["slug"]} for b in bl]
        paid = sum(1 for b in bl if b["type"] == "paid")
        free = len(bl) - paid
        await m.edit_text(
            f"🎉 <b>Login Successful!</b>\n\n📚 <b>Tumhare Batches ({len(bl)})</b>\n"
            f"💰 Paid: {paid}  |  🆓 Free: {free}",
            reply_markup=list_kb(items, "batch", "main"), parse_mode="HTML")
        return

    if ctx.user_data.get("awaiting_token"):
        token = txt
        if token.startswith("Bearer "): token = token[7:].strip()
        api.save_token(uid, token); ctx.user_data.clear()
        try: await update.message.delete()
        except Exception: pass
        await ctx.bot.send_message(chat_id=chat_id,
            text="✅ Token saved!\n\nAb 📚 My Batches dabao.",
            reply_markup=main_menu_kb(ia), parse_mode="HTML")
        return

    await ctx.bot.send_message(chat_id=chat_id, text="/start dabao.",
                               reply_markup=main_menu_kb(ia))


# ============================================================
# HELPERS
# ============================================================
def _flat(entries):
    files = []
    for e in entries or []:
        tp = e.get("topic") or ""
        for a in e.get("attachments", []):
            b = a.get("baseUrl") or ""; k = a.get("key") or ""
            n = a.get("name") or f"{tp}.pdf"
            if b and k: files.append({"name": n, "url": b + k})
    return files


async def send_files(q, files, lbl):
    if not files:
        await q.message.reply_text(f"❌ Koi {lbl} nahi."); return
    sent = 0
    for it in files[:10]:
        try:
            nm = "".join(c for c in it["name"] if c.isalnum() or c in " ._-()").strip()
            if not nm.lower().endswith(".pdf"): nm += ".pdf"
            p = os.path.expanduser("~/temp_dl.pdf")
            req = urllib.request.Request(it["url"], headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r, open(p, "wb") as f:
                f.write(r.read())
            with open(p, "rb") as f:
                await q.message.reply_document(document=f, filename=nm)
            sent += 1
        except Exception as e:
            await q.message.reply_text(f"❌ {it['name']}: {e}")
    await q.message.reply_text(f"✅ {sent}/{len(files[:10])} {lbl}.")


async def error_handler(update, ctx):
    log.error(f"Error: {ctx.error}")


async def post_init(app):
    asyncio.create_task(notification_watcher())
    asyncio.create_task(payment_watcher(app))


if __name__ == "__main__":
    app = (ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build())
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("notifdebug", notifdebug_cmd))
    app.add_handler(CommandHandler("verify", verify_cmd))
    app.add_handler(CommandHandler("setupi", setupi_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(
        filters.Document.PDF | (filters.TEXT & ~filters.COMMAND), handle_message))
    app.add_error_handler(error_handler)
    print("🤖 Bot chalu hai...")
    print(f"👤 Admin ID: {ADMIN_USER_ID}")
    print("👀 Notification reader: Termux:API direct")
    app.run_polling()
