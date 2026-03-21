#!/usr/bin/env python3
"""
📰 NewsBot PRO - Registration & Management System
Version: 3.0.0 (Professional Commercial Grade)
Features:
  - Multi-tier pricing (Basic/Standard/Premium/Yearly)
  - Pembayaran GoPay & OVO: 081236072208 a/n Gede Dylan Pratama Wijaya
  - FIX: konfirmasi admin tidak error pada pesan foto (editMessageText bug fixed)
  - Broadcast admin: teks, gambar, file ke semua/individual member
  - Export berita per member (hanya berita yang diterima user tersebut)
  - Pilih/ubah kategori berita per member sesuai paket
  - Notifikasi otomatis langganan hampir habis (7/3/1 hari)
  - Auto-disable akun expired via scheduler
  - Statistik + estimasi pendapatan
"""

import json
import logging
import os
import asyncio
import sqlite3
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable, Table, TableStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    REPORTLAB_OK = True
except ImportError:
    REPORTLAB_OK = False
from datetime import datetime, timedelta
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)
from telegram.constants import ParseMode
from logging.handlers import RotatingFileHandler
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ==================== LOGGING ====================
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
fh = RotatingFileHandler("register.log", maxBytes=5*1024*1024, backupCount=3)
fh.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(fh)

# ==================== FILE PATHS ====================
ACCOUNTS_FILE     = "accounts.json"
PENDING_FILE      = "pending_users.json"
KICK_LOG_FILE     = "kick_log.json"
PAYMENT_FILE      = "payment_pending.json"
SUBSCRIPTION_FILE = "subscriptions.json"

# ==================== KATEGORI ====================
ALL_CATEGORIES = [
    "technology","business","sports","entertainment",
    "science","health","politik","militer","general","crypto"
]
CATEGORY_LABELS = {
    "technology":"💻 Teknologi","business":"📈 Bisnis & Ekonomi",
    "sports":"⚽ Olahraga","entertainment":"🎬 Hiburan",
    "science":"🔬 Sains","health":"🏥 Kesehatan",
    "politik":"🏛️ Politik","militer":"🎖️ Militer",
    "general":"📰 Umum","crypto":"₿ Kripto & Blockchain",
}

# ==================== PAKET HARGA ====================
PRICING_PLANS = {
    "basic": {
        "label":"🥉 Basic","harga":"Rp 35.000","harga_int":35000,
        "periode":"1 bulan","periode_days":30,"max_per_hour":10,
        "kategori":["general","politik","business"],
        "desc":"3 kategori pilihan"
    },
    "standard": {
        "label":"🥈 Standard","harga":"Rp 45.000","harga_int":45000,
        "periode":"1 bulan","periode_days":30,"max_per_hour":30,
        "kategori":["general","politik","business","technology","health","sports","entertainment"],
        "desc":"7 kategori + update prioritas"
    },
    "premium": {
        "label":"🥇 Premium","harga":"Rp 65.000","harga_int":65000,
        "periode":"1 bulan","periode_days":30,"max_per_hour":100,
        "kategori":ALL_CATEGORIES,
        "desc":"SEMUA 10 kategori termasuk Kripto & Militer"
    },
    "yearly": {
        "label":"👑 Premium Tahunan","harga":"Rp 550.000","harga_int":550000,
        "periode":"1 tahun","periode_days":365,"max_per_hour":100,
        "kategori":ALL_CATEGORIES,
        "desc":"HEMAT 44%! Semua fitur Premium 1 tahun penuh"
    }
}

# ==================== PEMBAYARAN ====================
PAYMENT_CONFIG = {
    "metode": [
        {"nama":"GoPay","nomor":"081236072208","atas_nama":"Gede Dylan Pratama Wijaya","emoji":"💚"},
        {"nama":"OVO",  "nomor":"081236072208","atas_nama":"Gede Dylan Pratama Wijaya","emoji":"💜"},
    ],
    "catatan":"Setelah transfer, kirim screenshot bukti pembayaran ke bot ini. Admin konfirmasi 1x24 jam."
}

# ==================== JSON HELPERS ====================

def load_json_file(filename, default):
    try:
        if not os.path.exists(filename):
            with open(filename,'w') as f: json.dump(default,f,indent=2)
            return default
        with open(filename,'r') as f:
            c = f.read().strip()
            return json.loads(c) if c else default
    except Exception as e:
        logger.error(f"Error {filename}: {e}")
        return default

def save_json_file(filename, data):
    try:
        tmp = filename+".tmp"
        with open(tmp,'w') as f: json.dump(data,f,indent=2,ensure_ascii=False)
        os.replace(tmp, filename)
    except Exception as e:
        logger.error(f"Save {filename}: {e}")

def load_accounts():         return load_json_file(ACCOUNTS_FILE, [])
def save_accounts(d):        save_json_file(ACCOUNTS_FILE, d)
def load_pending():          return load_json_file(PENDING_FILE, [])
def save_pending(d):         save_json_file(PENDING_FILE, d)
def load_kick_log():         return load_json_file(KICK_LOG_FILE, [])
def save_kick_log(d):        save_json_file(KICK_LOG_FILE, d)
def load_payment_pending():  return load_json_file(PAYMENT_FILE, [])
def save_payment_pending(d): save_json_file(PAYMENT_FILE, d)
def load_subscriptions():    return load_json_file(SUBSCRIPTION_FILE, {})
def save_subscriptions(d):   save_json_file(SUBSCRIPTION_FILE, d)

# ==================== HELPER ====================

def get_admin_id():
    a = load_accounts()
    return str(a[0]['chat_id']) if a else None

def get_admin_token():
    a = load_accounts()
    return a[0].get('token') if a else None

def is_admin(chat_id):
    return str(chat_id) == str(get_admin_id())

def is_user_registered(chat_id):
    for acc in load_accounts():
        if str(acc.get('chat_id'))==str(chat_id) and acc.get('is_active',True) and not acc.get('banned',False):
            return True
    return False

def get_user_data(chat_id):
    for acc in load_accounts():
        if str(acc.get('chat_id'))==str(chat_id):
            return acc
    return None

def is_user_pending(chat_id):
    return any(str(p.get('chat_id'))==str(chat_id) for p in load_pending())

def is_payment_pending(chat_id):
    return any(str(p.get('chat_id'))==str(chat_id) for p in load_payment_pending())

def was_user_kicked(chat_id):
    return any(str(k.get('chat_id'))==str(chat_id) for k in load_kick_log())

def get_user_subscription(chat_id):
    return load_subscriptions().get(str(chat_id))

def set_user_subscription(chat_id, plan_key):
    subs  = load_subscriptions()
    plan  = PRICING_PLANS[plan_key]
    expiry = datetime.now() + timedelta(days=plan['periode_days'])
    subs[str(chat_id)] = {
        "plan":plan_key, "plan_label":plan['label'],
        "start":datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "expiry":expiry.strftime("%Y-%m-%d %H:%M:%S")
    }
    save_subscriptions(subs)
    return expiry

def days_until_expiry(chat_id):
    sub = get_user_subscription(chat_id)
    if not sub: return 0
    expiry = datetime.strptime(sub['expiry'],"%Y-%m-%d %H:%M:%S")
    return max(0,(expiry-datetime.now()).days)

def get_payment_text(plan_key=None):
    p = PRICING_PLANS.get(plan_key) if plan_key else None
    header = ""
    if p:
        header = f"📦 Paket: <b>{p['label']}</b>\n💰 Harga: <b>{p['harga']}</b> / {p['periode']}\n\n"
    t = header + "💳 <b>METODE PEMBAYARAN</b>\n\n"
    for m in PAYMENT_CONFIG['metode']:
        t += f"{m['emoji']} <b>{m['nama']}</b>\n   Nomor  : <code>{m['nomor']}</code>\n   A/N    : {m['atas_nama']}\n\n"
    t += f"📌 {PAYMENT_CONFIG['catatan']}"
    return t

# ==================== SAFE EDIT (FIX BUG) ====================

async def safe_edit_or_reply(query, text, reply_markup=None, parse_mode=ParseMode.HTML):
    """FIX: editMessageText gagal jika pesan berisi foto/media — fallback ke reply_text"""
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:
        try:
            await query.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception as e:
            logger.error(f"safe_edit_or_reply: {e}")

# ==================== USER COMMANDS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user    = update.effective_user
        chat_id = str(update.effective_chat.id)
        name    = user.first_name or "Pengguna"

        if is_user_registered(chat_id):
            sub = get_user_subscription(chat_id)
            ud  = get_user_data(chat_id)
            days = days_until_expiry(chat_id)
            plan_label = sub.get('plan_label','?') if sub else '?'
            warn = "\n⚠️ <b>Segera perpanjang!</b>" if days <= 7 else ""
            cats = ud.get('categories', ALL_CATEGORIES) if ud else ALL_CATEGORIES
            cat_str = " | ".join(CATEGORY_LABELS.get(c,'') for c in cats[:4])
            if len(cats) > 4: cat_str += f" +{len(cats)-4}"
            kb = [
                [InlineKeyboardButton("📊 Status Langganan",   callback_data="my_status")],
                [InlineKeyboardButton("🗂️ Ubah Kategori",      callback_data="my_categories")],
                [InlineKeyboardButton("📥 Export Berita Saya", callback_data="export_my_news")],
                [InlineKeyboardButton("🔄 Perpanjang",         callback_data="show_plans")],
                [InlineKeyboardButton("❓ Panduan / Help",     callback_data="show_help")],
            ]
            await update.message.reply_text(
                f"👋 Halo <b>{name}</b>!\n\n"
                f"✅ Akun kamu <b>AKTIF</b>\n"
                f"📦 Paket: {plan_label}\n"
                f"⏳ Sisa: <b>{days} hari</b>{warn}\n\n"
                f"📰 Kategori: {cat_str}",
                reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML
            )
            return

        if was_user_kicked(chat_id):
            kb = [[InlineKeyboardButton("💳 Daftar Ulang", callback_data="show_plans")]]
            await update.message.reply_text(
                f"👋 Halo {name}!\n⚠️ Kamu pernah dikeluarkan.\nDaftar ulang dengan berlangganan.",
                reply_markup=InlineKeyboardMarkup(kb)
            )
            return

        if is_payment_pending(chat_id):
            await update.message.reply_text(
                f"⏳ Halo {name}!\n📸 Bukti bayarmu sudah diterima.\nMenunggu konfirmasi admin 🙏"
            )
            return

        if is_user_pending(chat_id):
            await update.message.reply_text(
                f"⏳ Halo {name}!\nPendaftaranmu sedang diverifikasi admin 🙏"
            )
            return

        kb = [
            [InlineKeyboardButton("📦 Lihat Paket & Harga", callback_data="show_plans")],
            [InlineKeyboardButton("❌ Batal", callback_data="cancel")],
        ]
        await update.message.reply_text(
            f"👋 Selamat datang, <b>{name}</b>!\n\n"
            f"📰 <b>NewsBot PRO</b> — Berita Terkini Auto ke Telegram\n\n"
            f"✅ 10 kategori: Politik, Militer, Kripto, Teknologi, dll\n"
            f"✅ 50+ sumber berita terpercaya\n"
            f"✅ Filter cerdas & akurat per kategori\n"
            f"✅ Update otomatis setiap jam\n\n"
            f"Pilih paket untuk mulai berlangganan 👇",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"start: {e}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        msg     = update.message or (update.callback_query.message if update.callback_query else None)
        chat_id = str(msg.chat.id) if msg else str(update.effective_chat.id)

        if not is_user_registered(chat_id):
            await (msg or update.message).reply_text("❌ Belum terdaftar. /start untuk daftar.")
            return

        ud   = get_user_data(chat_id)
        sub  = get_user_subscription(chat_id)
        days = days_until_expiry(chat_id)

        sent_count = 0
        try:
            if os.path.exists('news_bot.db'):
                conn = sqlite3.connect('news_bot.db')
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM sent_news WHERE account_id=?", (chat_id,))
                sent_count = cursor.fetchone()[0]
                conn.close()
        except Exception: pass

        cats    = ud.get('categories',[]) if ud else []
        cat_str = "\n".join(f"  ✅ {CATEGORY_LABELS.get(c,c)}" for c in cats)
        plan_lbl= sub.get('plan_label','?') if sub else '?'
        expiry  = sub.get('expiry','-') if sub else '-'
        warn    = "\n⚠️ <b>Segera perpanjang!</b>" if days<=7 else ""

        await (msg or update.message).reply_text(
            f"📊 <b>Status Langganan</b>\n\n"
            f"👤 {ud.get('name','?') if ud else '?'} | ID: {chat_id}\n"
            f"📦 Paket: {plan_lbl}\n"
            f"📅 Aktif hingga: {expiry}\n"
            f"⏳ Sisa: <b>{days} hari</b>{warn}\n"
            f"📰 Berita diterima: {sent_count}\n\n"
            f"📂 Kategori:\n{cat_str}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"status: {e}")

async def unregister(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not is_user_registered(str(update.effective_chat.id)):
            await update.message.reply_text("❌ Kamu belum terdaftar.")
            return
        kb = [
            [InlineKeyboardButton("✅ Ya, Berhenti", callback_data="unregister_confirm")],
            [InlineKeyboardButton("❌ Batal",        callback_data="cancel")],
        ]
        await update.message.reply_text(
            "⚠️ Yakin ingin berhenti berlangganan?\nAkses berita langsung dihentikan.",
            reply_markup=InlineKeyboardMarkup(kb)
        )
    except Exception as e:
        logger.error(f"unregister: {e}")

async def categories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = str(update.effective_chat.id)
        if not is_user_registered(chat_id):
            await update.message.reply_text("❌ Belum terdaftar. /start")
            return
        ud        = get_user_data(chat_id)
        user_cats = ud.get('categories', ALL_CATEGORIES) if ud else ALL_CATEGORIES
        sub       = get_user_subscription(chat_id)
        plan_key  = sub.get('plan','standard') if sub else 'standard'
        plan_cats = PRICING_PLANS.get(plan_key,{}).get('kategori', ALL_CATEGORIES)
        context.user_data['editing_categories'] = list(user_cats)
        kb = _build_cat_kb(user_cats, plan_cats)
        await update.message.reply_text(
            "🗂️ <b>Pilih Kategori Berita</b>\n\nTap untuk aktifkan/nonaktifkan, lalu 💾 Simpan.",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"categories_command: {e}")

async def export_my_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tampilkan pilihan format export: TXT atau PDF"""
    try:
        msg     = update.message or (update.callback_query.message if update.callback_query else None)
        chat_id = str(msg.chat.id) if msg else str(update.effective_chat.id)

        if not is_user_registered(chat_id):
            await (msg or update.message).reply_text("❌ Belum terdaftar.")
            return
        if not os.path.exists('news_bot.db'):
            await (msg or update.message).reply_text("❌ Database berita belum tersedia.")
            return

        # Cek ada berita dulu
        conn = sqlite3.connect('news_bot.db')
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM sent_news sn
            JOIN news n ON sn.news_hash = n.hash_id
            WHERE sn.account_id = ?
        """, (chat_id,))
        total = cursor.fetchone()[0]
        conn.close()

        if total == 0:
            await (msg or update.message).reply_text("📭 Belum ada berita yang diterima.")
            return

        # Tampilkan pilihan format
        kb = [
            [InlineKeyboardButton("📄 Export TXT (Semua)", callback_data="export_fmt_txt")],
            [InlineKeyboardButton("📕 Export PDF per Kategori", callback_data="export_pdf_choose_cat")],
        ]
        txt = (
            f"📥 <b>Export Berita Kamu</b>\n\n"
            f"Total <b>{total}</b> berita tersedia.\n\n"
            f"📄 <b>TXT</b> — semua berita sekaligus (maks 200)\n"
            f"📕 <b>PDF</b> — pilih 1 kategori, isi diterjemahkan ke Bahasa Indonesia\n\n"
            f"Pilih format:"
        )
        target = msg or update.message
        await target.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"export_my_news: {e}")

async def _do_export_txt(chat_id: str, bot):
    """Kirim export TXT ke user"""
    conn = sqlite3.connect('news_bot.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT n.title, n.link, n.category, n.summary, sn.sent_at
        FROM sent_news sn
        JOIN news n ON sn.news_hash = n.hash_id
        WHERE sn.account_id = ?
        ORDER BY sn.sent_at DESC LIMIT 200
    """, (chat_id,))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await bot.send_message(chat_id=chat_id, text="📭 Tidak ada berita.")
        return

    lines = [f"EXPORT BERITA — {datetime.now().strftime('%d %b %Y %H:%M')}", "="*60]
    for i,(title,link,cat,summary,sent_at) in enumerate(rows, 1):
        lines.append(f"\n[{i}] {CATEGORY_LABELS.get(cat, cat)}")
        lines.append(f"📰 {title}")
        if summary: lines.append(f"📝 {summary[:150]}...")
        lines.append(f"🔗 {link}")
        lines.append(f"📅 Diterima: {sent_at}")

    fname = f"/tmp/berita_{chat_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.txt"
    with open(fname, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))

    with open(fname, 'rb') as f:
        await bot.send_document(
            chat_id=chat_id, document=f,
            filename=f"berita_{datetime.now().strftime('%Y%m%d')}.txt",
            caption=f"📄 <b>Export TXT</b>\n{len(rows)} berita | {datetime.now().strftime('%d %b %Y')}",
            parse_mode=ParseMode.HTML
        )
    os.remove(fname)


async def _do_export_pdf(chat_id: str, bot, category: str = "all"):
    """Kirim export PDF ke user — judul & ringkasan otomatis diterjemahkan ke Bahasa Indonesia"""
    if not REPORTLAB_OK:
        await bot.send_message(chat_id=chat_id,
            text="❌ Modul PDF tidak tersedia di server. Gunakan export TXT.")
        return

    conn = sqlite3.connect('news_bot.db')
    cursor = conn.cursor()

    CATEGORY_LABELS_PLAIN = {
        "technology":"Teknologi","business":"Bisnis & Ekonomi",
        "sports":"Olahraga","entertainment":"Hiburan",
        "science":"Sains","health":"Kesehatan",
        "politik":"Politik","militer":"Militer",
        "general":"Umum","crypto":"Kripto & Blockchain",
    }

    # Filter default: hanya berita hari ini & kemarin (hasil scan harian)
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    today     = datetime.now().strftime("%Y-%m-%d")

    if category and category != "all":
        cursor.execute("""
            SELECT n.title, n.link, n.category, n.summary, sn.sent_at
            FROM sent_news sn
            JOIN news n ON sn.news_hash = n.hash_id
            WHERE sn.account_id = ?
              AND n.category = ?
              AND date(n.published) >= ?
              AND date(n.published) <= ?
            ORDER BY sn.sent_at DESC LIMIT 50
        """, (chat_id, category, yesterday, today))
        cat_label_display = CATEGORY_LABELS_PLAIN.get(category, category.capitalize())
    else:
        cursor.execute("""
            SELECT n.title, n.link, n.category, n.summary, sn.sent_at
            FROM sent_news sn
            JOIN news n ON sn.news_hash = n.hash_id
            WHERE sn.account_id = ?
              AND date(n.published) >= ?
              AND date(n.published) <= ?
            ORDER BY sn.sent_at DESC LIMIT 50
        """, (chat_id, yesterday, today))
        cat_label_display = "Semua Kategori"
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"📭 Tidak ada berita <b>{cat_label_display}</b>\n"
                f"untuk hari ini / kemarin ({yesterday} s/d {today})."
            ),
            parse_mode=ParseMode.HTML)
        return

    # Notif progress ke user
    await bot.send_message(
        chat_id=chat_id,
        text=(
            f"⏳ <b>Sedang menyiapkan PDF...</b>\n\n"
            f"Kategori  : <b>{cat_label_display}</b>\n"
            f"Periode   : <b>{yesterday} s/d {today}</b>\n"
            f"Jumlah    : <b>{len(rows)} berita</b>\n\n"
            f"Sedang diterjemahkan ke Bahasa Indonesia...\n"
            f"Mohon tunggu sebentar 🙏"
        ),
        parse_mode=ParseMode.HTML
    )

    # ---- Fungsi helper ----
    import re as _re

    def strip_emoji(text):
        """Hapus emoji/karakter non-ASCII agar aman di reportlab"""
        return _re.sub(r'[^\x00-\x7F\u00C0-\u024F\u1E00-\u1EFF]', '', str(text or '')).strip()

    def is_indonesian(text):
        """Cek kasar apakah teks sudah Bahasa Indonesia berdasarkan kata umum"""
        id_words = {
            'yang','dan','di','dengan','ini','itu','untuk','dari','ke','pada',
            'adalah','akan','telah','sudah','tidak','bisa','dalam','ada','atau',
            'juga','karena','saat','setelah','bahwa','sehingga','namun','lebih',
            'pemerintah','menteri','presiden','Indonesia','nasional','warga','rakyat',
        }
        words = set(_re.findall(r'\b[a-zA-Z]+\b', text or ''))
        matches = words & id_words
        return len(matches) >= 2

    def translate_to_id(text):
        """Terjemahkan teks ke Bahasa Indonesia. Jika sudah ID, skip."""
        if not text or len(text.strip()) < 5:
            return text
        if is_indonesian(text):
            return text
        try:
            from deep_translator import GoogleTranslator
            result = GoogleTranslator(source='auto', target='id').translate(text[:500])
            return result if result else text
        except Exception:
            return text  # Fallback: pakai teks asli jika terjemahan gagal

    fname = f"/tmp/berita_{chat_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"

    doc = SimpleDocTemplate(fname, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()

    # Style custom
    title_style = ParagraphStyle('TitleS', parent=styles['Heading1'],
                                 fontSize=16, spaceAfter=4, textColor=colors.HexColor('#1a1a2e'))
    header_style = ParagraphStyle('HeaderS', parent=styles['Normal'],
                                  fontSize=9, textColor=colors.grey, spaceAfter=12)
    cat_style = ParagraphStyle('CatS', parent=styles['Normal'],
                               fontSize=8, textColor=colors.HexColor('#2563eb'),
                               spaceBefore=10, spaceAfter=2)
    news_title_style = ParagraphStyle('NewsTitleS', parent=styles['Normal'],
                                      fontSize=10, fontName='Helvetica-Bold',
                                      spaceAfter=3, leading=14)
    summary_style = ParagraphStyle('SummaryS', parent=styles['Normal'],
                                   fontSize=9, textColor=colors.HexColor('#555555'),
                                   spaceAfter=2, leading=12)
    link_style = ParagraphStyle('LinkS', parent=styles['Normal'],
                                fontSize=8, textColor=colors.HexColor('#2563eb'),
                                spaceAfter=2)
    date_style = ParagraphStyle('DateS', parent=styles['Normal'],
                                fontSize=8, textColor=colors.grey, spaceAfter=6)

    CATEGORY_LABELS_PLAIN = {
        "technology":"Teknologi","business":"Bisnis & Ekonomi",
        "sports":"Olahraga","entertainment":"Hiburan",
        "science":"Sains","health":"Kesehatan",
        "politik":"Politik","militer":"Militer",
        "general":"Umum","crypto":"Kripto & Blockchain",
    }

    story = []

    # Header halaman
    story.append(Paragraph("Kumpulan Berita Saya - NewsBot PRO", title_style))
    story.append(Paragraph(
        f"Dibuat: {datetime.now().strftime('%d %B %Y, %H:%M')} WIB  |  "
        f"Kategori: {cat_label_display}  |  "
        f"Periode: {yesterday} s/d {today}  |  "
        f"Total: {len(rows)} berita  |  Bahasa: Indonesia",
        header_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#e5e7eb')))
    story.append(Spacer(1, 0.3*cm))

    for i, (title, link, cat, summary, sent_at) in enumerate(rows, 1):
        cat_label = CATEGORY_LABELS_PLAIN.get(cat, cat or 'Umum')

        # Terjemahkan ke Bahasa Indonesia
        title_id   = translate_to_id(title)
        summary_id = translate_to_id((summary or '')[:400]) if summary else ''

        # Sanitize untuk reportlab
        safe_title   = strip_emoji(title_id).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
        safe_summary = strip_emoji(summary_id[:200]).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
        safe_link    = (link or '').replace('&','&amp;')
        safe_cat     = strip_emoji(cat_label)

        story.append(Paragraph(f"[{safe_cat}]  <font size='7' color='grey'>#{i}</font>", cat_style))
        story.append(Paragraph(safe_title or '(Judul tidak tersedia)', news_title_style))
        if safe_summary:
            story.append(Paragraph(safe_summary + ("..." if len(summary_id) > 200 else ""), summary_style))
        story.append(Paragraph(
            f"<a href='{safe_link}' color='#2563eb'>{safe_link[:80]}{'...' if len(safe_link)>80 else ''}</a>",
            link_style))
        story.append(Paragraph(f"Diterima: {sent_at}", date_style))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#f0f0f0')))

    doc.build(story)

    with open(fname, 'rb') as f:
        await bot.send_document(
            chat_id=chat_id, document=f,
            filename=f"berita_{datetime.now().strftime('%Y%m%d')}.pdf",
            caption=f"📕 <b>Export PDF</b>\n{len(rows)} berita | {datetime.now().strftime('%d %b %Y')}",
            parse_mode=ParseMode.HTML
        )
    os.remove(fname)

# ==================== PAYMENT PROOF ====================

async def handle_payment_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user    = update.effective_user
        chat_id = str(update.effective_chat.id)
        admin_id= get_admin_id()

        # Admin mengirim foto/file untuk /msg atau /broadcast — skip
        if is_admin(chat_id):
            return

        if is_user_registered(chat_id):
            await update.message.reply_text("✅ Akun kamu sudah aktif!")
            return
        if is_payment_pending(chat_id):
            await update.message.reply_text("⏳ Bukti bayarmu sudah diproses, tunggu konfirmasi admin.")
            return

        plan_key = context.user_data.get('selected_plan','standard')
        plan     = PRICING_PLANS[plan_key]

        plist = load_payment_pending()
        plist = [p for p in plist if str(p.get('chat_id'))!=chat_id]
        plist.append({
            "chat_id":chat_id, "name":f"{user.first_name} {user.last_name or ''}".strip(),
            "username":user.username or "", "plan":plan_key,
            "submitted_at":datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
        save_payment_pending(plist)

        await update.message.reply_text(
            f"✅ <b>Bukti pembayaran diterima!</b>\n\n"
            f"📦 Paket: {plan['label']} ({plan['harga']})\n"
            f"Admin akan memverifikasi dalam 1x24 jam.\n\n"
            f"Notifikasi otomatis setelah dikonfirmasi 🙏",
            parse_mode=ParseMode.HTML
        )

        if admin_id and get_admin_token():
            bot = Bot(token=get_admin_token())
            kb  = [[
                InlineKeyboardButton("✅ Konfirmasi", callback_data=f"pay_confirm_{chat_id}_{plan_key}"),
                InlineKeyboardButton("❌ Tolak",      callback_data=f"pay_reject_{chat_id}"),
            ]]
            caption = (
                f"💳 <b>BUKTI PEMBAYARAN BARU</b>\n\n"
                f"👤 {user.first_name} (@{user.username or 'None'})\n"
                f"🆔 ID: {chat_id}\n"
                f"📦 Paket: {plan['label']} — {plan['harga']}\n"
                f"🕒 {datetime.now().strftime('%d %b %Y, %H:%M')}"
            )
            try:
                if update.message.photo:
                    await bot.send_photo(chat_id=admin_id, photo=update.message.photo[-1].file_id,
                                         caption=caption, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
                elif update.message.document:
                    await bot.send_document(chat_id=admin_id, document=update.message.document.file_id,
                                            caption=caption, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
            except Exception:
                await bot.send_message(chat_id=admin_id, text=caption+"\n\n⚠️ Bukti gagal diforward.",
                                       reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"handle_payment_proof: {e}")

# ==================== ADMIN BROADCAST ====================

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = str(update.effective_chat.id)
        if not is_admin(chat_id):
            await update.message.reply_text("❌ Hanya admin.")
            return

        if not context.args:
            await update.message.reply_text(
                "❌ Format: /broadcast <teks pesan>\n\n"
                "Contoh: /broadcast Halo semua member!"
            )
            return

        text = " ".join(context.args)
        kb = [[
            InlineKeyboardButton("📤 Kirim Sekarang",    callback_data="bc_send_text"),
            InlineKeyboardButton("📎 Tambah File/Foto",  callback_data="bc_add_file"),
        ], [
            InlineKeyboardButton("❌ Batal", callback_data="cancel"),
        ]]
        context.user_data['bc_text'] = text
        context.user_data['admin_state'] = None
        await update.message.reply_text(
            f"📢 <b>BROADCAST</b>\n\n"
            f"Pesan: {text}\n\n"
            f"Mau kirim teks saja atau tambah file/foto?",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"admin_broadcast: {e}")

async def _do_broadcast_send(context, update_or_message, text, file_id=None, file_type=None):
    """Kirim broadcast ke semua member aktif."""
    accounts = load_accounts()
    admin_id = get_admin_id()
    members  = [a for a in accounts if str(a.get('chat_id')) != admin_id
                and a.get('is_active', True) and not a.get('banned', False)]
    token = get_admin_token()
    if not token:
        return
    bot  = Bot(token=token)
    ok   = fail = 0
    for acc in members:
        try:
            cid = acc['chat_id']
            if file_id and file_type == 'photo':
                await bot.send_photo(chat_id=cid, photo=file_id, caption=text, parse_mode=ParseMode.HTML)
            elif file_id and file_type == 'document':
                await bot.send_document(chat_id=cid, document=file_id, caption=text, parse_mode=ParseMode.HTML)
            else:
                await bot.send_message(chat_id=cid, text=text, parse_mode=ParseMode.HTML)
            ok += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.error(f"Broadcast ke {acc['chat_id']}: {e}")
            fail += 1

    msg = update_or_message if hasattr(update_or_message, 'reply_text') else update_or_message.message
    await msg.reply_text(
        f"📢 <b>Broadcast Selesai</b>\n✅ {ok} berhasil | ❌ {fail} gagal | 📊 Total: {len(members)}",
        parse_mode=ParseMode.HTML
    )

# ==================== ADMIN MSG ====================

async def admin_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = str(update.effective_chat.id)
        if not is_admin(chat_id):
            await update.message.reply_text("❌ Hanya admin.")
            return

        args = context.args
        if len(args) < 2:
            await update.message.reply_text(
                "❌ Format: /msg <id> <pesan>\n\n"
                "Contoh: /msg 123456789 Halo kak, langganan sudah aktif!"
            )
            return

        target_id = args[0]
        msg_text  = " ".join(args[1:])

        # Cek target valid
        ud = get_user_data(target_id)
        nama = ud.get('name', target_id) if ud else target_id

        kb = [[
            InlineKeyboardButton("📤 Kirim Pesan",      callback_data=f"msg_send_{target_id}"),
            InlineKeyboardButton("📎 Tambah File/Foto",  callback_data=f"msg_add_file_{target_id}"),
        ], [
            InlineKeyboardButton("❌ Batal", callback_data="cancel"),
        ]]

        context.user_data['msg_text']    = msg_text
        context.user_data['msg_target']  = target_id
        context.user_data['admin_state'] = None

        await update.message.reply_text(
            f"📩 <b>PESAN KE:</b> {nama} (<code>{target_id}</code>)\n\n"
            f"Pesan: {msg_text}\n\n"
            f"Mau kirim teks saja atau tambah file/foto?",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"admin_msg: {e}")
        await update.message.reply_text(f"❌ Gagal: {e}")

# ==================== HANDLE MEDIA UPLOAD DARI ADMIN ====================

async def handle_admin_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Terima foto/file dari admin saat state menunggu upload."""
    try:
        chat_id = str(update.effective_chat.id)
        if not is_admin(chat_id):
            return

        state = context.user_data.get('admin_state')

        # State: menunggu file untuk /msg
        if state == 'waiting_msg_file':
            target_id = context.user_data.get('msg_target')
            msg_text  = context.user_data.get('msg_text', '')
            token     = get_admin_token()
            bot       = Bot(token=token)

            if update.message.photo:
                file_id = update.message.photo[-1].file_id
                await bot.send_photo(
                    chat_id=target_id, photo=file_id,
                    caption=f"📩 <b>Pesan dari Admin:</b>\n\n{msg_text}",
                    parse_mode=ParseMode.HTML
                )
            elif update.message.document:
                file_id = update.message.document.file_id
                await bot.send_document(
                    chat_id=target_id, document=file_id,
                    caption=f"📩 <b>Pesan dari Admin:</b>\n\n{msg_text}",
                    parse_mode=ParseMode.HTML
                )
            else:
                await update.message.reply_text("❌ Kirim foto atau file saja ya.")
                return

            context.user_data['admin_state'] = None
            ud   = get_user_data(target_id)
            nama = ud.get('name', target_id) if ud else target_id
            await update.message.reply_text(f"✅ Pesan + file terkirim ke {nama} ({target_id})")

        # State: menunggu file untuk /broadcast
        elif state == 'waiting_bc_file':
            bc_text = context.user_data.get('bc_text', '')

            if update.message.photo:
                file_id   = update.message.photo[-1].file_id
                file_type = 'photo'
            elif update.message.document:
                file_id   = update.message.document.file_id
                file_type = 'document'
            else:
                await update.message.reply_text("❌ Kirim foto atau file saja ya.")
                return

            context.user_data['admin_state'] = None
            await update.message.reply_text("⏳ Sedang broadcast ke semua member...")
            await _do_broadcast_send(context, update.message, bc_text, file_id, file_type)

        # Bukan state admin — abaikan (jangan proses sebagai bukti bayar)
        else:
            return

    except Exception as e:
        logger.error(f"handle_admin_media: {e}")

# ==================== BUTTON HANDLER ====================

def _build_cat_kb(user_cats, plan_cats):
    kb = []
    for cat in ALL_CATEGORIES:
        if cat not in plan_cats:
            lbl = f"🔒 {CATEGORY_LABELS.get(cat,cat)}"
        elif cat in user_cats:
            lbl = f"✅ {CATEGORY_LABELS.get(cat,cat)}"
        else:
            lbl = f"☐ {CATEGORY_LABELS.get(cat,cat)}"
        kb.append([InlineKeyboardButton(lbl, callback_data=f"toggle_cat_{cat}")])
    kb.append([InlineKeyboardButton("💾 Simpan Kategori", callback_data="save_categories")])
    return kb

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query    = update.callback_query
        await query.answer()
        chat_id  = str(query.message.chat.id)
        user     = query.from_user
        admin_id = get_admin_id()
        data     = query.data

        # ---- SHOW PLANS ----
        if data == "show_plans":
            kb = []
            for pk, plan in PRICING_PLANS.items():
                kb.append([InlineKeyboardButton(
                    f"{plan['label']} — {plan['harga']}/{plan['periode']}",
                    callback_data=f"select_plan_{pk}"
                )])
            kb.append([InlineKeyboardButton("❌ Batal", callback_data="cancel")])
            await safe_edit_or_reply(query,
                "📦 <b>PAKET BERLANGGANAN</b>\n\n"
                + "\n".join(f"• {p['label']} <b>{p['harga']}</b> — {p['desc']}" for p in PRICING_PLANS.values()),
                reply_markup=InlineKeyboardMarkup(kb)
            )

        # ---- SELECT PLAN ----
        elif data.startswith("select_plan_"):
            pk = data.replace("select_plan_","")
            if pk not in PRICING_PLANS: return
            context.user_data['selected_plan'] = pk
            txt = get_payment_text(pk)
            kb  = [[InlineKeyboardButton("✅ Saya Sudah Transfer", callback_data="already_paid")],
                   [InlineKeyboardButton("◀️ Kembali", callback_data="show_plans")]]
            await safe_edit_or_reply(query, txt, reply_markup=InlineKeyboardMarkup(kb))

        # ---- ALREADY PAID ----
        elif data == "already_paid":
            pk   = context.user_data.get('selected_plan','standard')
            plan = PRICING_PLANS[pk]
            await safe_edit_or_reply(query,
                f"📸 <b>KIRIM BUKTI PEMBAYARAN</b>\n\n"
                f"Paket dipilih: <b>{plan['label']} ({plan['harga']})</b>\n\n"
                f"Kirim foto/screenshot bukti transfer ke chat ini.\n\n"
                f"✅ Pastikan terlihat:\n"
                f"• Nama pengirim\n"
                f"• Nominal transfer\n"
                f"• Nomor tujuan: <code>081236072208</code>\n"
                f"• Tanggal & jam\n\n"
                f"Admin konfirmasi maks 1x24 jam 🙏"
            )

        # ---- MY STATUS ----
        elif data == "my_status":
            await status(update, context)

        # ---- MY CATEGORIES ----
        elif data == "my_categories":
            if not is_user_registered(chat_id): return
            ud        = get_user_data(chat_id)
            user_cats = ud.get('categories', ALL_CATEGORIES) if ud else ALL_CATEGORIES
            sub       = get_user_subscription(chat_id)
            plan_key  = sub.get('plan','standard') if sub else 'standard'
            plan_cats = PRICING_PLANS.get(plan_key,{}).get('kategori', ALL_CATEGORIES)
            context.user_data['editing_categories'] = list(user_cats)
            kb = _build_cat_kb(user_cats, plan_cats)
            await safe_edit_or_reply(query,
                "🗂️ <b>Pilih Kategori Berita</b>\n\nTap kategori → 💾 Simpan",
                reply_markup=InlineKeyboardMarkup(kb)
            )

        # ---- TOGGLE CAT ----
        elif data.startswith("toggle_cat_"):
            cat      = data.replace("toggle_cat_","")
            sub      = get_user_subscription(chat_id)
            plan_key = sub.get('plan','standard') if sub else 'standard'
            plan_cats= PRICING_PLANS.get(plan_key,{}).get('kategori', ALL_CATEGORIES)
            if cat not in plan_cats:
                await query.answer("🔒 Upgrade paket untuk kategori ini!", show_alert=True)
                return
            editing = context.user_data.get('editing_categories', list(ALL_CATEGORIES))
            if cat in editing: editing.remove(cat)
            else: editing.append(cat)
            context.user_data['editing_categories'] = editing
            kb = _build_cat_kb(editing, plan_cats)
            try:
                await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))
            except Exception: pass

        # ---- SAVE CATEGORIES ----
        elif data == "save_categories":
            editing = context.user_data.get('editing_categories', ALL_CATEGORIES)
            accounts = load_accounts()
            for acc in accounts:
                if str(acc.get('chat_id'))==chat_id:
                    acc['categories'] = editing
            save_accounts(accounts)
            cat_str = "\n".join(f"✅ {CATEGORY_LABELS.get(c,c)}" for c in editing)
            await safe_edit_or_reply(query, f"✅ <b>Kategori disimpan!</b>\n\n{cat_str}")

        # ---- EXPORT MY NEWS ----
        elif data == "export_my_news":
            await query.answer("Pilih format export...")
            await export_my_news(update, context)

        # ---- EXPORT TXT langsung ----
        elif data == "export_fmt_txt":
            await query.answer("Menyiapkan TXT...")
            cid = str(query.from_user.id)
            token = get_admin_token()
            bot_obj = Bot(token=token)
            try:
                await query.edit_message_text(
                    "⏳ Sedang menyiapkan file <b>TXT</b>...\nMohon tunggu.",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
            await _do_export_txt(cid, bot_obj)

        # ---- EXPORT PDF — tampilkan pilih kategori ----
        elif data == "export_pdf_choose_cat":
            if not is_user_registered(chat_id): return
            # Ambil kategori yang diterima user ini — hanya hari ini & kemarin
            cats_available = []
            try:
                yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
                today_str     = datetime.now().strftime("%Y-%m-%d")
                conn = sqlite3.connect('news_bot.db')
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT n.category, COUNT(*) as cnt
                    FROM sent_news sn
                    JOIN news n ON sn.news_hash = n.hash_id
                    WHERE sn.account_id = ?
                      AND n.category IS NOT NULL
                      AND date(n.published) >= ?
                      AND date(n.published) <= ?
                    GROUP BY n.category
                    ORDER BY cnt DESC
                """, (chat_id, yesterday_str, today_str))
                cats_available = cursor.fetchall()
                conn.close()
            except Exception as e:
                logger.error(f"export_pdf_choose_cat: {e}")

            if not cats_available:
                await safe_edit_or_reply(query,
                    "❌ Belum ada berita hari ini / kemarin.\n\n"
                    "PDF hanya memuat berita 2 hari terakhir.")
                return

            tgl_info = f"{yesterday_str} s/d {today_str}"
            kb = []
            for cat_key, cnt in cats_available:
                lbl = CATEGORY_LABELS.get(cat_key, cat_key.capitalize())
                kb.append([InlineKeyboardButton(
                    f"{lbl} — {cnt} berita",
                    callback_data=f"export_pdf_cat_{cat_key}"
                )])
            kb.append([InlineKeyboardButton("◀️ Kembali", callback_data="export_my_news")])

            await safe_edit_or_reply(query,
                f"📕 <b>Export PDF — Pilih Kategori</b>\n\n"
                f"📅 Periode: <b>{tgl_info}</b>\n"
                f"Pilih kategori yang ingin di-export (maks 50 berita).\n"
                f"Isi akan diterjemahkan ke <b>Bahasa Indonesia</b>:",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML
            )

        # ---- EXPORT PDF — generate per kategori ----
        elif data.startswith("export_pdf_cat_"):
            cat_key = data.replace("export_pdf_cat_", "")
            cid     = str(query.from_user.id)
            token   = get_admin_token()
            bot_obj = Bot(token=token)
            cat_lbl = CATEGORY_LABELS.get(cat_key, cat_key.capitalize())
            try:
                await query.edit_message_text(
                    f"⏳ Memproses PDF kategori <b>{cat_lbl}</b>...\nMohon tunggu.",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
            await _do_export_pdf(cid, bot_obj, category=cat_key)

        # ---- UNREGISTER CONFIRM ----
        elif data == "unregister_confirm":
            accounts = load_accounts()
            accounts = [a for a in accounts if str(a.get('chat_id'))!=chat_id]
            save_accounts(accounts)
            subs = load_subscriptions(); subs.pop(chat_id, None); save_subscriptions(subs)
            kick_log = load_kick_log()
            kick_log.append({"chat_id":chat_id,"reason":"Self-unregistered","kicked_at":datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
            save_kick_log(kick_log)
            await safe_edit_or_reply(query, "✅ Berhasil berhenti berlangganan.\n\nKetik /start untuk daftar ulang kapan saja.")

        # ---- SHOW HELP (USER) ----
        elif data == "show_help":
            await query.answer()
            await admin_help(update, context)

        # ---- CANCEL ----
        elif data == "cancel":
            context.user_data['admin_state'] = None
            await safe_edit_or_reply(query, "❌ Dibatalkan.")

        # ---- ADMIN: MSG KIRIM TEKS ----
        elif data.startswith("msg_send_"):
            if not is_admin(chat_id): return
            target_id = data.replace("msg_send_", "")
            msg_text  = context.user_data.get('msg_text', '')
            token     = get_admin_token()
            bot       = Bot(token=token)
            await bot.send_message(
                chat_id=target_id,
                text=f"📩 <b>Pesan dari Admin:</b>\n\n{msg_text}",
                parse_mode=ParseMode.HTML
            )
            ud   = get_user_data(target_id)
            nama = ud.get('name', target_id) if ud else target_id
            await safe_edit_or_reply(query, f"✅ Pesan terkirim ke {nama} ({target_id})")

        # ---- ADMIN: MSG TAMBAH FILE ----
        elif data.startswith("msg_add_file_"):
            if not is_admin(chat_id): return
            target_id = data.replace("msg_add_file_", "")
            context.user_data['msg_target']  = target_id
            context.user_data['admin_state'] = 'waiting_msg_file'
            await safe_edit_or_reply(query,
                f"📎 Sekarang kirim foto atau file ke chat ini.\n"
                f"Akan diteruskan ke <code>{target_id}</code> beserta pesannya.",
                parse_mode=ParseMode.HTML
            )

        # ---- ADMIN: BROADCAST KIRIM TEKS ----
        elif data == "bc_send_text":
            if not is_admin(chat_id): return
            bc_text = context.user_data.get('bc_text', '')
            await safe_edit_or_reply(query, "⏳ Sedang broadcast ke semua member...")
            await _do_broadcast_send(context, query.message, bc_text)

        # ---- ADMIN: BROADCAST TAMBAH FILE ----
        elif data == "bc_add_file":
            if not is_admin(chat_id): return
            context.user_data['admin_state'] = 'waiting_bc_file'
            await safe_edit_or_reply(query,
                "📎 Sekarang kirim foto atau file ke chat ini.\n"
                "Akan di-broadcast ke semua member beserta pesannya."
            )

        # ---- ADMIN: DELETE NEWS — MENU KEMBALI ----
        elif data == "dn_back":
            if not is_admin(chat_id): return
            if not os.path.exists('news_bot.db'):
                await safe_edit_or_reply(query, "❌ Database tidak ditemukan.")
                return
            conn   = sqlite3.connect('news_bot.db')
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM news")
            total_news = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM sent_news")
            total_sent = cursor.fetchone()[0]
            cursor.execute("SELECT date(sent_at), COUNT(*) FROM sent_news GROUP BY date(sent_at) ORDER BY date(sent_at) DESC LIMIT 7")
            per_date = cursor.fetchall()
            conn.close()
            date_info = "\n".join(f"  📅 {d}: {c} terkirim" for d,c in per_date) or "  (kosong)"
            kb = [
                [InlineKeyboardButton("🗑️ Hapus SEMUA Berita",     callback_data="dn_confirm_all")],
                [InlineKeyboardButton("📅 Hapus Per Tanggal",       callback_data="dn_by_date")],
                [InlineKeyboardButton("🔍 Cari & Hapus Per Berita", callback_data="dn_by_item")],
                [InlineKeyboardButton("❌ Batal",                   callback_data="cancel")],
            ]
            await safe_edit_or_reply(query,
                f"🗑️ <b>HAPUS BERITA</b>\n\n"
                f"📰 Total berita di DB   : <b>{total_news}</b>\n"
                f"📤 Total history kirim  : <b>{total_sent}</b>\n\n"
                f"📊 Riwayat 7 hari terakhir:\n{date_info}\n\n"
                f"Pilih mode hapus:",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML
            )

        # ---- ADMIN: DELETE NEWS — KONFIRMASI HAPUS SEMUA ----
        elif data == "dn_confirm_all":
            if not is_admin(chat_id): return
            kb = [[
                InlineKeyboardButton("✅ YA, HAPUS SEMUA", callback_data="dn_do_all"),
                InlineKeyboardButton("❌ Batal",           callback_data="dn_back"),
            ]]
            await safe_edit_or_reply(query,
                "⚠️ <b>KONFIRMASI HAPUS SEMUA BERITA</b>\n\n"
                "Ini akan menghapus <b>semua berita</b> dan <b>semua riwayat pengiriman</b> dari database.\n\n"
                "Tindakan ini <b>tidak bisa dibatalkan!</b>\n\nYakin?",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML
            )

        # ---- ADMIN: DELETE NEWS — LAKUKAN HAPUS SEMUA ----
        elif data == "dn_do_all":
            if not is_admin(chat_id): return
            try:
                conn   = sqlite3.connect('news_bot.db')
                cursor = conn.cursor()
                cursor.execute("DELETE FROM sent_news")
                cursor.execute("DELETE FROM news")
                deleted = cursor.rowcount
                conn.commit()
                conn.close()
                await safe_edit_or_reply(query,
                    f"✅ <b>Semua berita berhasil dihapus!</b>\n\n"
                    f"🗑️ Database berita sekarang kosong.",
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                await safe_edit_or_reply(query, f"❌ Gagal hapus: {e}")

        # ---- ADMIN: DELETE NEWS — TAMPILKAN PILIH TANGGAL ----
        elif data == "dn_by_date":
            if not is_admin(chat_id): return
            await _deletenews_show_dates(query, context)

        # ---- ADMIN: DELETE NEWS — KONFIRMASI HAPUS PER TANGGAL ----
        elif data.startswith("dn_date_"):
            if not is_admin(chat_id): return
            tgl = data.replace("dn_date_", "")
            try:
                conn   = sqlite3.connect('news_bot.db')
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM news WHERE date(published)=?", (tgl,))
                cnt = cursor.fetchone()[0]
                conn.close()
            except Exception:
                cnt = 0
            kb = [[
                InlineKeyboardButton(f"✅ Hapus {cnt} berita", callback_data=f"dn_do_date_{tgl}"),
                InlineKeyboardButton("❌ Batal",               callback_data="dn_by_date"),
            ]]
            await safe_edit_or_reply(query,
                f"⚠️ <b>Konfirmasi Hapus Berita</b>\n\n"
                f"📅 Tanggal : <b>{tgl}</b>\n"
                f"📰 Jumlah  : <b>{cnt} berita</b>\n\n"
                f"Yakin hapus semua berita tanggal ini?",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML
            )

        # ---- ADMIN: DELETE NEWS — LAKUKAN HAPUS PER TANGGAL ----
        elif data.startswith("dn_do_date_"):
            if not is_admin(chat_id): return
            tgl = data.replace("dn_do_date_", "")
            try:
                conn   = sqlite3.connect('news_bot.db')
                cursor = conn.cursor()
                # Hapus sent_news terkait dulu
                cursor.execute("""
                    DELETE FROM sent_news WHERE news_hash IN
                    (SELECT hash_id FROM news WHERE date(published)=?)
                """, (tgl,))
                cursor.execute("DELETE FROM news WHERE date(published)=?", (tgl,))
                deleted = cursor.rowcount
                conn.commit()
                conn.close()
                await safe_edit_or_reply(query,
                    f"✅ <b>Berhasil dihapus!</b>\n\n"
                    f"📅 Tanggal : {tgl}\n"
                    f"🗑️ Dihapus : {deleted} berita",
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                await safe_edit_or_reply(query, f"❌ Gagal hapus: {e}")

        # ---- ADMIN: DELETE NEWS — TAMPILKAN LIST BERITA ----
        elif data == "dn_by_item":
            if not is_admin(chat_id): return
            await _deletenews_show_items(query, context, page=0)

        # ---- ADMIN: DELETE NEWS — PAGINATION ----
        elif data.startswith("dn_page_"):
            if not is_admin(chat_id): return
            page = int(data.replace("dn_page_", ""))
            await _deletenews_show_items(query, context, page=page)

        # ---- ADMIN: DELETE NEWS — KONFIRMASI HAPUS 1 BERITA ----
        elif data.startswith("dn_item_"):
            if not is_admin(chat_id): return
            hash_id = data.replace("dn_item_", "")
            try:
                conn   = sqlite3.connect('news_bot.db')
                cursor = conn.cursor()
                cursor.execute("SELECT title, category, date(published) FROM news WHERE hash_id=?", (hash_id,))
                row = cursor.fetchone()
                conn.close()
            except Exception:
                row = None
            if not row:
                await safe_edit_or_reply(query, "❌ Berita tidak ditemukan.")
                return
            title, cat, tgl = row
            kb = [[
                InlineKeyboardButton("✅ Ya, Hapus",  callback_data=f"dn_do_item_{hash_id}"),
                InlineKeyboardButton("❌ Batal",      callback_data="dn_by_item"),
            ]]
            await safe_edit_or_reply(query,
                f"⚠️ <b>Konfirmasi Hapus Berita</b>\n\n"
                f"📰 {title}\n"
                f"🏷️ {CATEGORY_LABELS.get(cat, cat)}\n"
                f"📅 {tgl}\n\n"
                f"Yakin hapus berita ini?",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML
            )

        # ---- ADMIN: DELETE NEWS — LAKUKAN HAPUS 1 BERITA ----
        elif data.startswith("dn_do_item_"):
            if not is_admin(chat_id): return
            hash_id = data.replace("dn_do_item_", "")
            try:
                conn   = sqlite3.connect('news_bot.db')
                cursor = conn.cursor()
                cursor.execute("SELECT title FROM news WHERE hash_id=?", (hash_id,))
                row = cursor.fetchone()
                cursor.execute("DELETE FROM sent_news WHERE news_hash=?", (hash_id,))
                cursor.execute("DELETE FROM news WHERE hash_id=?", (hash_id,))
                conn.commit()
                conn.close()
                title = row[0] if row else hash_id
                kb = [[InlineKeyboardButton("🔍 Lanjut Hapus Berita Lain", callback_data="dn_by_item")]]
                await safe_edit_or_reply(query,
                    f"✅ <b>Berita dihapus!</b>\n\n📰 {title}",
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                await safe_edit_or_reply(query, f"❌ Gagal hapus: {e}")

        # ---- ADMIN: KONFIRMASI BAYAR ----
        elif data.startswith("pay_confirm_"):
            if not is_admin(chat_id):
                await query.answer("❌ Bukan admin", show_alert=True)
                return
            parts     = data.replace("pay_confirm_","").split("_",1)
            target_id = parts[0]
            plan_key  = parts[1] if len(parts)>1 else "standard"
            plan      = PRICING_PLANS.get(plan_key, PRICING_PLANS['standard'])

            plist = load_payment_pending()
            tp    = next((p for p in plist if str(p.get('chat_id'))==target_id), None)
            plist = [p for p in plist if str(p.get('chat_id'))!=target_id]
            save_payment_pending(plist)

            accounts  = load_accounts()
            main_token= get_admin_token()
            exists    = any(str(a.get('chat_id'))==target_id for a in accounts)

            if not exists and main_token:
                accounts.append({
                    "token":main_token, "chat_id":target_id,
                    "name":tp.get('name','Unknown') if tp else target_id,
                    "categories":plan['kategori'], "max_per_hour":plan['max_per_hour'],
                    "is_active":True, "banned":False, "plan":plan_key,
                    "payment_verified":True,
                    "created_at":datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
                save_accounts(accounts)
            else:
                for acc in accounts:
                    if str(acc.get('chat_id'))==target_id:
                        acc.update({"is_active":True,"banned":False,"plan":plan_key,
                                    "categories":plan['kategori'],"max_per_hour":plan['max_per_hour']})
                save_accounts(accounts)

            expiry = set_user_subscription(target_id, plan_key)

            # FIX: gunakan reply_text, bukan edit_message_text karena pesan berisi foto
            try:
                await query.message.reply_text(
                    f"✅ <b>Pembayaran DIKONFIRMASI</b>\n\n"
                    f"👤 {tp.get('name','?') if tp else target_id}\n"
                    f"📦 {plan['label']} ({plan['harga']})\n"
                    f"📅 Aktif hingga: {expiry.strftime('%d %b %Y')}",
                    parse_mode=ParseMode.HTML
                )
            except Exception: pass

            try:
                bot = Bot(token=main_token)
                cats_str = ", ".join(CATEGORY_LABELS.get(c,'') for c in plan['kategori'])
                await bot.send_message(
                    chat_id=target_id,
                    text=(
                        f"🎉 <b>PEMBAYARAN DIKONFIRMASI!</b>\n\n"
                        f"Selamat! Akun kamu sekarang <b>AKTIF</b>.\n\n"
                        f"📦 Paket: {plan['label']}\n"
                        f"📅 Aktif hingga: {expiry.strftime('%d %b %Y')}\n"
                        f"📰 Kategori: {cats_str}\n\n"
                        f"Ketik /status untuk detail.\nSelamat menikmati NewsBot PRO! 🗞️"
                    ),
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.error(f"Notif ke user gagal: {e}")

        # ---- ADMIN: TOLAK BAYAR ----
        elif data.startswith("pay_reject_"):
            if not is_admin(chat_id):
                await query.answer("❌ Bukan admin", show_alert=True)
                return
            target_id = data.replace("pay_reject_","")
            plist = load_payment_pending()
            tp    = next((p for p in plist if str(p.get('chat_id'))==target_id), None)
            plist = [p for p in plist if str(p.get('chat_id'))!=target_id]
            save_payment_pending(plist)

            # FIX: reply bukan edit
            try:
                await query.message.reply_text(
                    f"❌ Pembayaran <b>{tp.get('name','?') if tp else target_id}</b> DITOLAK.",
                    parse_mode=ParseMode.HTML
                )
            except Exception: pass

            try:
                bot = Bot(token=get_admin_token())
                await bot.send_message(
                    chat_id=target_id,
                    text=(
                        "❌ <b>BUKTI PEMBAYARAN DITOLAK</b>\n\n"
                        "Maaf, bukti tidak valid atau tidak jelas.\n\n"
                        "Silakan coba lagi: /start → Pilih Paket → Transfer → Kirim bukti jelas.\n"
                        "Hubungi admin jika ada masalah."
                    ),
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.error(f"Notif reject: {e}")

        # ---- ADMIN: APPROVE/REJECT PENDING ----
        elif data.startswith("approve_"):
            if not is_admin(chat_id): return
            target_id   = data.replace("approve_","")
            pending     = load_pending()
            target_user = next((p for p in pending if str(p.get('chat_id'))==target_id), None)
            pending     = [p for p in pending if str(p.get('chat_id'))!=target_id]
            save_pending(pending)
            if not target_user:
                await safe_edit_or_reply(query, "❌ User tidak ditemukan.")
                return
            plan_key = target_user.get('plan','standard')
            plan     = PRICING_PLANS.get(plan_key, PRICING_PLANS['standard'])
            accounts = load_accounts()
            token    = get_admin_token()
            if not any(str(a.get('chat_id'))==target_id for a in accounts):
                accounts.append({
                    "token":token, "chat_id":target_id,
                    "name":target_user.get('name','Unknown'),
                    "categories":plan['kategori'], "max_per_hour":plan['max_per_hour'],
                    "is_active":True, "banned":False, "plan":plan_key,
                    "created_at":datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
                save_accounts(accounts)
            expiry = set_user_subscription(target_id, plan_key)
            await safe_edit_or_reply(query, f"✅ {target_user.get('name','')} disetujui! Aktif hingga {expiry.strftime('%d %b %Y')}.")
            try:
                bot = Bot(token=token)
                await bot.send_message(chat_id=target_id, text=f"✅ Akun aktif!\nPaket: {plan['label']}\nHingga: {expiry.strftime('%d %b %Y')}")
            except Exception: pass

        elif data.startswith("reject_"):
            if not is_admin(chat_id): return
            target_id = data.replace("reject_","")
            pending   = [p for p in load_pending() if str(p.get('chat_id'))!=target_id]
            save_pending(pending)
            await safe_edit_or_reply(query, f"❌ {target_id} ditolak.")



        # ---- ADMIN: HAPUS BERITA — MENU NAVIGASI ----
        elif data == "dn_back":
            if not is_admin(chat_id): return
            # Kembali ke menu utama delnews
            if not os.path.exists('news_bot.db'):
                await safe_edit_or_reply(query, "❌ Database tidak ditemukan.")
                return
            conn   = sqlite3.connect('news_bot.db')
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM news")
            total_news = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM sent_news")
            total_sent = cursor.fetchone()[0]
            cursor.execute("SELECT date(sent_at), COUNT(*) FROM sent_news GROUP BY date(sent_at) ORDER BY date(sent_at) DESC LIMIT 7")
            per_date = cursor.fetchall()
            conn.close()
            date_info = "\n".join(f"  📅 {d}: {c} terkirim" for d,c in per_date) or "  (kosong)"
            kb = [
                [InlineKeyboardButton("🗑️ Hapus SEMUA Berita",     callback_data="dn_confirm_all")],
                [InlineKeyboardButton("📅 Hapus Per Tanggal",       callback_data="dn_by_date")],
                [InlineKeyboardButton("🔍 Cari & Hapus Per Berita", callback_data="dn_by_item")],
                [InlineKeyboardButton("❌ Batal",                   callback_data="cancel")],
            ]
            await safe_edit_or_reply(query,
                f"🗑️ <b>HAPUS BERITA</b>\n\n"
                f"📰 Total berita di DB   : <b>{total_news}</b>\n"
                f"📤 Total history kirim  : <b>{total_sent}</b>\n\n"
                f"📊 Riwayat 7 hari terakhir:\n{date_info}\n\n"
                f"Pilih mode hapus:",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML
            )

        elif data == "dn_by_date":
            if not is_admin(chat_id): return
            await _deletenews_show_dates(query, context)

        elif data == "dn_by_item":
            if not is_admin(chat_id): return
            await _deletenews_show_items(query, context, page=0)

        elif data.startswith("dn_page_"):
            if not is_admin(chat_id): return
            page = int(data.replace("dn_page_", ""))
            await _deletenews_show_items(query, context, page=page)

        # ---- ADMIN: HAPUS PER TANGGAL ----
        elif data.startswith("dn_date_"):
            if not is_admin(chat_id): return
            tgl = data.replace("dn_date_", "")
            kb  = [[
                InlineKeyboardButton(f"✅ Ya, hapus {tgl}", callback_data=f"dn_confirm_date_{tgl}"),
                InlineKeyboardButton("❌ Batal",             callback_data="dn_by_date"),
            ]]
            await safe_edit_or_reply(query,
                f"⚠️ Konfirmasi hapus berita tanggal <b>{tgl}</b>?\n\n"
                f"Berita & log pengiriman pada tanggal ini akan dihapus permanen.",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML
            )

        elif data.startswith("dn_confirm_date_"):
            if not is_admin(chat_id): return
            tgl = data.replace("dn_confirm_date_", "")
            try:
                conn   = sqlite3.connect('news_bot.db')
                cursor = conn.cursor()
                cursor.execute("DELETE FROM sent_news WHERE news_hash IN (SELECT hash_id FROM news WHERE date(published)=?)", (tgl,))
                del_sent = cursor.rowcount
                cursor.execute("DELETE FROM news WHERE date(published)=?", (tgl,))
                del_news = cursor.rowcount
                conn.commit()
                conn.close()
                await safe_edit_or_reply(query,
                    f"✅ <b>Berita {tgl} dihapus!</b>\n\n"
                    f"🗞 Berita: {del_news} | 📤 Log: {del_sent}",
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                await safe_edit_or_reply(query, f"❌ Gagal: {e}")
                logger.error(f"dn_confirm_date: {e}")

        # ---- ADMIN: HAPUS PER BERITA ----
        elif data.startswith("dn_item_"):
            if not is_admin(chat_id): return
            hash_id = data.replace("dn_item_", "")
            try:
                conn   = sqlite3.connect('news_bot.db')
                cursor = conn.cursor()
                cursor.execute("SELECT title, category, date(published) FROM news WHERE hash_id=?", (hash_id,))
                row = cursor.fetchone()
                conn.close()
                if not row:
                    await safe_edit_or_reply(query, "❌ Berita tidak ditemukan.")
                    return
                title, cat, tgl = row
                kb = [[
                    InlineKeyboardButton("🗑️ Ya, hapus berita ini", callback_data=f"dn_confirm_item_{hash_id}"),
                    InlineKeyboardButton("❌ Batal",                 callback_data="dn_by_item"),
                ]]
                await safe_edit_or_reply(query,
                    f"⚠️ Hapus berita ini?\n\n"
                    f"📰 {title}\n"
                    f"🏷 {CATEGORY_LABELS.get(cat, cat)} | 📅 {tgl}",
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                await safe_edit_or_reply(query, f"❌ Error: {e}")

        elif data.startswith("dn_confirm_item_"):
            if not is_admin(chat_id): return
            hash_id = data.replace("dn_confirm_item_", "")
            try:
                conn   = sqlite3.connect('news_bot.db')
                cursor = conn.cursor()
                cursor.execute("DELETE FROM sent_news WHERE news_hash=?", (hash_id,))
                del_sent = cursor.rowcount
                cursor.execute("DELETE FROM news WHERE hash_id=?", (hash_id,))
                del_news = cursor.rowcount
                conn.commit()
                conn.close()
                await safe_edit_or_reply(query,
                    f"✅ Berita berhasil dihapus!\n📤 Log terkirim dihapus: {del_sent}"
                )
            except Exception as e:
                await safe_edit_or_reply(query, f"❌ Gagal: {e}")

        # ---- ADMIN: HAPUS SEMUA BERITA ----
        elif data == "dn_confirm_all":
            if not is_admin(chat_id): return
            kb = [[
                InlineKeyboardButton("✅ Ya, HAPUS SEMUA", callback_data="dn_do_all"),
                InlineKeyboardButton("❌ Batal",           callback_data="cancel"),
            ]]
            await safe_edit_or_reply(query,
                "⚠️ <b>HAPUS SEMUA BERITA?</b>\n\n"
                "Seluruh berita & log pengiriman akan dihapus permanen.\n"
                "<b>Tidak bisa dibatalkan!</b>",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML
            )

        elif data == "dn_do_all":
            if not is_admin(chat_id): return
            try:
                conn   = sqlite3.connect('news_bot.db')
                cursor = conn.cursor()
                cursor.execute("DELETE FROM sent_news")
                del_sent = cursor.rowcount
                cursor.execute("DELETE FROM news")
                del_news = cursor.rowcount
                conn.commit()
                conn.close()
                await safe_edit_or_reply(query,
                    f"✅ <b>Semua berita dihapus!</b>\n\n"
                    f"🗞 Berita: {del_news} | 📤 Log: {del_sent}",
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                await safe_edit_or_reply(query, f"❌ Gagal: {e}")
                logger.error(f"dn_do_all: {e}")

    except Exception as e:
        logger.error(f"button_handler: {e}")

# ==================== ADMIN COMMANDS ====================

async def admin_payments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not is_admin(str(update.effective_chat.id)):
            await update.message.reply_text("❌ Hanya admin.")
            return
        plist = load_payment_pending()
        if not plist:
            await update.message.reply_text("📭 Tidak ada pembayaran pending.")
            return
        for p in plist:
            pid      = p.get('chat_id')
            plan_key = p.get('plan','standard')
            plan     = PRICING_PLANS.get(plan_key, PRICING_PLANS['standard'])
            kb = [[
                InlineKeyboardButton("✅ Konfirmasi", callback_data=f"pay_confirm_{pid}_{plan_key}"),
                InlineKeyboardButton("❌ Tolak",      callback_data=f"pay_reject_{pid}"),
            ]]
            await update.message.reply_text(
                f"💳 <b>PENDING PEMBAYARAN</b>\n\n"
                f"👤 {p.get('name','?')} (@{p.get('username','?')})\n"
                f"🆔 {pid}\n"
                f"📦 {plan['label']} — {plan['harga']}\n"
                f"🕒 {p.get('submitted_at','?')}",
                reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML
            )
    except Exception as e:
        logger.error(f"admin_payments: {e}")

async def admin_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = str(update.effective_chat.id)
        if not is_admin(chat_id): return
        accounts = load_accounts()
        members  = [a for a in accounts if str(a.get('chat_id'))!=chat_id]
        if not members:
            await update.message.reply_text("📭 Belum ada member.")
            return
        lines = [f"📋 <b>MEMBER AKTIF</b> ({len(members)} total)\n"]
        for i,acc in enumerate(members,1):
            icon   = "✅" if acc.get('is_active') and not acc.get('banned') else ("🚫" if acc.get('banned') else "⏸️")
            days   = days_until_expiry(acc.get('chat_id',''))
            lines.append(f"{i}. {icon} {acc.get('name','?')} | {acc.get('chat_id')} | {acc.get('plan','?')} | {days}hr")
        text = "\n".join(lines)
        for chunk in [text[i:i+3800] for i in range(0,len(text),3800)]:
            await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"admin_list: {e}")

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = str(update.effective_chat.id)
        if not is_admin(chat_id): return
        accounts = load_accounts()
        members  = [a for a in accounts if str(a.get('chat_id'))!=chat_id]
        active   = [a for a in members if a.get('is_active') and not a.get('banned')]
        banned   = [a for a in members if a.get('banned')]
        disabled = [a for a in members if not a.get('is_active') and not a.get('banned')]
        plist    = load_payment_pending()
        pending  = load_pending()

        expiring = sum(1 for m in active if days_until_expiry(m.get('chat_id',''))<=7)
        revenue  = sum(PRICING_PLANS.get(m.get('plan','standard'),{}).get('harga_int',0) for m in active)

        sent_today = 0
        try:
            if os.path.exists('news_bot.db'):
                conn = sqlite3.connect('news_bot.db')
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM sent_news WHERE date(sent_at)=date('now')")
                sent_today = cursor.fetchone()[0]
                conn.close()
        except Exception: pass

        # Breakdown per paket
        plan_breakdown = {}
        for m in active:
            pk = m.get('plan','standard')
            plan_breakdown[pk] = plan_breakdown.get(pk,0)+1

        plan_str = "\n".join(f"  {PRICING_PLANS.get(pk,{}).get('label',pk)}: {cnt}" for pk,cnt in plan_breakdown.items())

        await update.message.reply_text(
            f"📊 <b>STATISTIK NEWSBOT PRO</b>\n"
            f"{'='*35}\n\n"
            f"👥 Total Member    : {len(members)}\n"
            f"✅ Aktif           : {len(active)}\n"
            f"🚫 Banned          : {len(banned)}\n"
            f"⏸️ Disabled        : {len(disabled)}\n"
            f"⚠️ Habis 7 hari    : {expiring}\n\n"
            f"💳 Pending Bayar   : {len(plist)}\n"
            f"⏳ Pending Verif   : {len(pending)}\n\n"
            f"📰 Terkirim Hari Ini: {sent_today}\n\n"
            f"📦 Per Paket:\n{plan_str}\n\n"
            f"💰 Est. Pendapatan Aktif: Rp {revenue:,}\n"
            f"🕒 {datetime.now().strftime('%d %b %Y %H:%M')}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"admin_stats: {e}")

async def admin_kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not is_admin(str(update.effective_chat.id)): return
        if not context.args:
            await update.message.reply_text("❌ /kick <id> [alasan]"); return
        target_id = context.args[0]
        reason    = " ".join(context.args[1:]) if len(context.args)>1 else "Tanpa alasan"
        accounts  = [a for a in load_accounts() if str(a.get('chat_id'))!=target_id]
        save_accounts(accounts)
        kick_log  = load_kick_log()
        kick_log.append({"chat_id":target_id,"reason":reason,"kicked_at":datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        save_kick_log(kick_log)
        subs = load_subscriptions(); subs.pop(target_id,None); save_subscriptions(subs)
        await update.message.reply_text(f"✅ User {target_id} di-kick.\nAlasan: {reason}")
        try:
            bot = Bot(token=get_admin_token())
            await bot.send_message(chat_id=target_id, text=f"⚠️ Akun kamu dinonaktifkan.\nAlasan: {reason}\n\nKetik /start untuk mendaftar ulang.")
        except Exception: pass
    except Exception as e:
        logger.error(f"admin_kick: {e}")

async def admin_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not is_admin(str(update.effective_chat.id)): return
        if not context.args:
            await update.message.reply_text("❌ /ban <id>"); return
        target_id = context.args[0]
        accounts  = load_accounts()
        for a in accounts:
            if str(a.get('chat_id'))==target_id:
                a['banned']=True; a['is_active']=False
        save_accounts(accounts)
        await update.message.reply_text(f"🚫 User {target_id} di-ban.")
        try:
            bot=Bot(token=get_admin_token())
            await bot.send_message(chat_id=target_id,text="🚫 Akun kamu di-banned dari layanan ini.")
        except Exception: pass
    except Exception as e:
        logger.error(f"admin_ban: {e}")

async def admin_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not is_admin(str(update.effective_chat.id)): return
        if not context.args:
            await update.message.reply_text("❌ /unban <id>"); return
        target_id = context.args[0]
        accounts  = load_accounts()
        for a in accounts:
            if str(a.get('chat_id'))==target_id: a['banned']=False; a['is_active']=True
        save_accounts(accounts)
        await update.message.reply_text(f"✅ User {target_id} di-unban.")
    except Exception as e:
        logger.error(f"admin_unban: {e}")

async def admin_disable(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not is_admin(str(update.effective_chat.id)): return
        if not context.args:
            await update.message.reply_text("❌ /disable <id>"); return
        target_id = context.args[0]
        accounts  = load_accounts()
        for a in accounts:
            if str(a.get('chat_id'))==target_id: a['is_active']=False
        save_accounts(accounts)
        await update.message.reply_text(f"⏸️ User {target_id} dinonaktifkan.")
    except Exception as e:
        logger.error(f"admin_disable: {e}")

async def admin_enable(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not is_admin(str(update.effective_chat.id)): return
        if not context.args:
            await update.message.reply_text("❌ /enable <id>"); return
        target_id = context.args[0]
        accounts  = load_accounts()
        for a in accounts:
            if str(a.get('chat_id'))==target_id: a['is_active']=True; a['banned']=False
        save_accounts(accounts)
        await update.message.reply_text(f"✅ User {target_id} diaktifkan.")
    except Exception as e:
        logger.error(f"admin_enable: {e}")

async def admin_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not is_admin(str(update.effective_chat.id)): return
        if not context.args:
            await update.message.reply_text("❌ /user <id>"); return
        target_id = context.args[0]
        ud = get_user_data(target_id)
        if not ud:
            await update.message.reply_text(f"❌ User {target_id} tidak ditemukan."); return
        sub  = get_user_subscription(target_id)
        days = days_until_expiry(target_id)
        icon = "✅" if ud.get('is_active') and not ud.get('banned') else ("🚫" if ud.get('banned') else "⏸️")
        cats = " | ".join(CATEGORY_LABELS.get(c,'') for c in ud.get('categories',[]))
        await update.message.reply_text(
            f"👤 <b>Detail User</b>\n\n"
            f"Nama     : {ud.get('name','?')}\n"
            f"ID       : {target_id}\n"
            f"Status   : {icon}\n"
            f"Paket    : {ud.get('plan','?')}\n"
            f"Sisa     : {days} hari\n"
            f"Bergabung: {ud.get('created_at','?')}\n"
            f"Expired  : {sub.get('expiry','-') if sub else '-'}\n\n"
            f"Kategori : {cats}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"admin_user: {e}")

async def admin_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not is_admin(str(update.effective_chat.id)): return
        data = {
            "exported_at":datetime.now().isoformat(),
            "accounts":load_accounts(),
            "subscriptions":load_subscriptions(),
            "payment_pending":load_payment_pending(),
            "kick_log":load_kick_log()
        }
        fname = f"/tmp/export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(fname,'w') as f: json.dump(data,f,indent=2,ensure_ascii=False)
        bot = Bot(token=get_admin_token())
        with open(fname,'rb') as f:
            await bot.send_document(
                chat_id=str(update.effective_chat.id), document=f,
                filename="newsbot_export.json",
                caption=f"📦 Export data — {len(data['accounts'])} accounts"
            )
        os.remove(fname)
    except Exception as e:
        logger.error(f"admin_export: {e}")

async def admin_kicklog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not is_admin(str(update.effective_chat.id)): return
        kl = load_kick_log()
        if not kl:
            await update.message.reply_text("📭 Tidak ada riwayat kick."); return
        lines = [f"📋 <b>RIWAYAT KICK</b> ({len(kl)} total)\n"]
        for k in kl[-20:]:
            lines.append(f"• {k.get('chat_id')} | {k.get('reason','?')} | {k.get('kicked_at','?')}")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"admin_kicklog: {e}")

async def admin_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not is_admin(str(update.effective_chat.id)): return
        pending = load_pending()
        if not pending:
            await update.message.reply_text("📭 Tidak ada user pending."); return
        for p in pending:
            kb = [[
                InlineKeyboardButton("✅", callback_data=f"approve_{p.get('chat_id')}"),
                InlineKeyboardButton("❌", callback_data=f"reject_{p.get('chat_id')}"),
            ]]
            await update.message.reply_text(
                f"⏳ {p.get('name','?')} | {p.get('chat_id')} | {p.get('requested_at','?')}",
                reply_markup=InlineKeyboardMarkup(kb)
            )
    except Exception as e:
        logger.error(f"admin_pending: {e}")

async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = str(update.effective_chat.id)
        if is_admin(chat_id):
            text = (
                "👑 <b>NEWSBOT PRO — PANDUAN LENGKAP ADMIN</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

                "💰 <b>PEMBAYARAN</b>\n"
                "/payments — Lihat daftar bukti bayar masuk\n"
                "  └ Konfirmasi ✅ atau Tolak ❌ langsung dari tombol\n\n"

                "👥 <b>MANAJEMEN MEMBER</b>\n"
                "/pending — Daftar user yang menunggu verifikasi manual\n"
                "/list — Semua member aktif (nama, paket, sisa hari)\n"
                "/user &lt;id&gt; — Detail lengkap 1 user\n"
                "  └ Info: nama, status, paket, expired, kategori\n"
                "/kick &lt;id&gt; [alasan] — Keluarkan user (bisa daftar ulang)\n"
                "/ban &lt;id&gt; — Ban permanen, tidak bisa daftar ulang\n"
                "/unban &lt;id&gt; — Hapus ban, aktifkan kembali\n"
                "/disable &lt;id&gt; — Nonaktifkan sementara (tidak terima berita)\n"
                "/enable &lt;id&gt; — Aktifkan kembali user yang disabled\n\n"

                "📨 <b>KOMUNIKASI</b>\n"
                "/msg &lt;id&gt; &lt;teks&gt; — Kirim pesan teks ke 1 member\n"
                "  └ Pilih + tambah foto/file setelah ketik perintah\n"
                "/broadcast &lt;teks&gt; — Kirim pesan ke SEMUA member aktif\n"
                "  └ Pilih kirim teks saja atau tambah foto/file\n\n"

                "🗃️ <b>DATA & LAPORAN</b>\n"
                "/stats — Statistik lengkap + estimasi pendapatan bulanan\n"
                "  └ Jumlah aktif, disabled, banned, expiring soon\n"
                "/export — Export seluruh data (akun, langganan, pembayaran) → file JSON\n"
                "/kicklog — Riwayat semua user yang pernah di-kick\n\n"

                "🗑️ <b>MANAJEMEN BERITA</b>\n"
                "/deletenews — Menu hapus berita di database\n"
                "  ├ Hapus SEMUA berita sekaligus\n"
                "  ├ Hapus per tanggal tertentu\n"
                "  └ Cari & hapus per judul berita\n\n"

                "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "💡 <b>CONTOH PENGGUNAAN</b>\n"
                "<code>/kick 123456789 Tidak bayar</code>\n"
                "<code>/msg 123456789 Halo, langganan kamu sudah aktif!</code>\n"
                "<code>/broadcast Promo perpanjang diskon 20% hari ini!</code>\n"
                "<code>/user 123456789</code>\n\n"

                "💳 <b>Info Pembayaran:</b>\n"
                "GoPay/OVO: <code>081236072208</code>\n"
                "a/n Gede Dylan Pratama Wijaya\n\n"

                "📦 <b>Paket:</b>\n"
                "🥉 Basic     Rp 35.000/bln — 3 kategori\n"
                "🥈 Standard  Rp 45.000/bln — 7 kategori\n"
                "🥇 Premium   Rp 65.000/bln — 10 kategori\n"
                "👑 Tahunan   Rp 550.000/thn — Hemat 44%"
            )
        else:
            text = (
                "📰 <b>NEWSBOT PRO — PANDUAN PENGGUNA</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

                "🚀 <b>MEMULAI</b>\n"
                "/start — Menu utama, daftar, atau lihat status akun\n"
                "  └ Jika belum daftar: pilih paket → transfer → kirim bukti\n\n"

                "📊 <b>AKUN & LANGGANAN</b>\n"
                "/status — Cek detail langganan kamu\n"
                "  └ Info: paket aktif, sisa hari, kategori, jumlah berita\n"
                "/categories — Atur kategori berita yang ingin diterima\n"
                "  └ Sesuai paket: Basic 3 kat, Standard 7 kat, Premium 10 kat\n\n"

                "📥 <b>EXPORT BERITA</b>\n"
                "/export — Download berita yang sudah kamu terima\n"
                "  ├ 📄 TXT — semua berita (maks 200), langsung download\n"
                "  └ 📕 PDF — pilih kategori, berita hari ini & kemarin\n"
                "           diterjemahkan otomatis ke Bahasa Indonesia\n\n"

                "🔄 <b>LAINNYA</b>\n"
                "/unregister — Berhenti berlangganan (akun dihapus)\n"
                "/help — Tampilkan panduan ini\n\n"

                "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "📦 <b>Paket Berlangganan:</b>\n"
                "🥉 Basic     Rp 35.000/bln  — 3 kategori\n"
                "🥈 Standard  Rp 45.000/bln  — 7 kategori\n"
                "🥇 Premium   Rp 65.000/bln  — 10 kategori (incl. Kripto & Militer)\n"
                "👑 Tahunan   Rp 550.000/thn — Semua fitur Premium, hemat 44%!\n\n"

                "💳 <b>Cara Bayar:</b>\n"
                "Transfer ke GoPay/OVO: <code>081236072208</code>\n"
                "a/n Gede Dylan Pratama Wijaya\n"
                "Lalu kirim screenshot bukti ke bot ini 📸\n\n"

                "⏰ <b>Notifikasi otomatis:</b>\n"
                "Kamu akan dapat reminder 7, 3, dan 1 hari sebelum expired."
            )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"admin_help: {e}")


# ==================== ADMIN DELETE NEWS ====================

async def admin_deletenews(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menu utama hapus berita"""
    try:
        chat_id = str(update.effective_chat.id)
        if not is_admin(chat_id):
            await update.message.reply_text("❌ Hanya admin.")
            return
        if not os.path.exists('news_bot.db'):
            await update.message.reply_text("❌ Database berita belum tersedia.")
            return

        conn   = sqlite3.connect('news_bot.db')
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM news")
        total_news = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM sent_news")
        total_sent = cursor.fetchone()[0]
        cursor.execute("SELECT date(sent_at), COUNT(*) FROM sent_news GROUP BY date(sent_at) ORDER BY date(sent_at) DESC LIMIT 7")
        per_date = cursor.fetchall()
        conn.close()

        date_info = "\n".join(f"  📅 {d}: {c} terkirim" for d,c in per_date) or "  (kosong)"

        kb = [
            [InlineKeyboardButton("🗑️ Hapus SEMUA Berita",      callback_data="dn_confirm_all")],
            [InlineKeyboardButton("📅 Hapus Per Tanggal",        callback_data="dn_by_date")],
            [InlineKeyboardButton("🔍 Cari & Hapus Per Berita",  callback_data="dn_by_item")],
            [InlineKeyboardButton("❌ Batal",                    callback_data="cancel")],
        ]
        await update.message.reply_text(
            f"🗑️ <b>HAPUS BERITA</b>\n\n"
            f"📰 Total berita di DB   : <b>{total_news}</b>\n"
            f"📤 Total history kirim  : <b>{total_sent}</b>\n\n"
            f"📊 Riwayat 7 hari terakhir:\n{date_info}\n\n"
            f"Pilih mode hapus:",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"admin_deletenews: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def _deletenews_show_dates(query, context):
    """Tampilkan daftar tanggal yang bisa dipilih untuk dihapus"""
    try:
        conn   = sqlite3.connect('news_bot.db')
        cursor = conn.cursor()
        cursor.execute("""
            SELECT date(published), COUNT(*)
            FROM news
            GROUP BY date(published)
            ORDER BY date(published) DESC
            LIMIT 20
        """)
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            await safe_edit_or_reply(query, "📭 Tidak ada berita di database.")
            return

        kb = []
        for tgl, cnt in rows:
            kb.append([InlineKeyboardButton(
                f"📅 {tgl}  ({cnt} berita)",
                callback_data=f"dn_date_{tgl}"
            )])
        kb.append([InlineKeyboardButton("◀️ Kembali", callback_data="dn_back")])

        await safe_edit_or_reply(query,
            "📅 <b>Pilih Tanggal yang Ingin Dihapus:</b>\n\n"
            "(Menampilkan 20 tanggal terbaru)",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"_deletenews_show_dates: {e}")

async def _deletenews_show_items(query, context, page=0):
    """Tampilkan daftar berita satu per satu dengan pagination"""
    try:
        limit  = 8
        offset = page * limit
        conn   = sqlite3.connect('news_bot.db')
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM news")
        total = cursor.fetchone()[0]
        cursor.execute("""
            SELECT hash_id, title, category, date(published)
            FROM news
            ORDER BY published DESC
            LIMIT ? OFFSET ?
        """, (limit, offset))
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            await safe_edit_or_reply(query, "📭 Tidak ada berita.")
            return

        kb = []
        for hash_id, title, cat, tgl in rows:
            label = f"[{CATEGORY_LABELS.get(cat, cat)[:8]}] {title[:35]}…" if len(title) > 35 else f"[{CATEGORY_LABELS.get(cat, cat)[:8]}] {title}"
            kb.append([InlineKeyboardButton(
                f"🗑️ {label}",
                callback_data=f"dn_item_{hash_id}"
            )])

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"dn_page_{page-1}"))
        if offset + limit < total:
            nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"dn_page_{page+1}"))
        if nav:
            kb.append(nav)
        kb.append([InlineKeyboardButton("◀️ Kembali", callback_data="dn_back")])

        total_pages = (total + limit - 1) // limit
        await safe_edit_or_reply(query,
            f"🔍 <b>Pilih Berita yang Ingin Dihapus</b>\n"
            f"Halaman {page+1}/{total_pages} — Total {total} berita\n\n"
            f"Tap berita untuk konfirmasi hapus:",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"_deletenews_show_items: {e}")

# ==================== SCHEDULER ====================

async def check_expiring_subscriptions():
    """Cek & notif member yang akan habis langganan"""
    try:
        subs     = load_subscriptions()
        accounts = load_accounts()
        token    = get_admin_token()
        if not token: return
        bot = Bot(token=token)
        modified = False

        for chat_id, sub in subs.items():
            days = days_until_expiry(chat_id)
            if days in (7, 3, 1):
                try:
                    plan = PRICING_PLANS.get(sub.get('plan','standard'), PRICING_PLANS['standard'])
                    await bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"⏰ <b>Langganan Hampir Habis!</b>\n\n"
                            f"Paket <b>{plan['label']}</b> kamu akan habis dalam <b>{days} hari</b>.\n\n"
                            f"Perpanjang sekarang:\n"
                            f"Ketik /start → Pilih Paket → Transfer → Kirim Bukti\n\n"
                            f"💳 GoPay/OVO: <code>081236072208</code>\n"
                            f"a/n Gede Dylan Pratama Wijaya\n\n"
                            f"Harga perpanjang: {plan['harga']}/{plan['periode']}"
                        ),
                        parse_mode=ParseMode.HTML
                    )
                except Exception: pass

        # Auto-disable expired
        for acc in accounts:
            cid = str(acc.get('chat_id',''))
            if is_admin(cid): continue
            sub_data = subs.get(cid)
            if sub_data:
                expiry = datetime.strptime(sub_data['expiry'],"%Y-%m-%d %H:%M:%S")
                if datetime.now() > expiry and acc.get('is_active'):
                    acc['is_active'] = False
                    modified = True
                    try:
                        await bot.send_message(
                            chat_id=cid,
                            text=(
                                "⚠️ <b>Langganan Habis</b>\n\n"
                                "Masa langganan kamu telah berakhir.\n"
                                "Kamu tidak akan menerima berita sementara.\n\n"
                                "Perpanjang dengan /start 🙏"
                            ),
                            parse_mode=ParseMode.HTML
                        )
                    except Exception: pass

        if modified:
            save_accounts(accounts)

    except Exception as e:
        logger.error(f"check_expiring: {e}")

# ==================== MAIN ====================

def main():
    accounts = load_accounts()
    if not accounts:
        print("❌ accounts.json kosong!"); return

    # Auto-fix akun admin: pastikan punya plan=premium & semua kategori
    admin = accounts[0]
    changed = False
    if admin.get('plan') != 'premium':
        admin['plan'] = 'premium'; changed = True
    if set(admin.get('categories', [])) != set(ALL_CATEGORIES):
        admin['categories'] = list(ALL_CATEGORIES); changed = True
    if changed:
        save_accounts(accounts)
        print("✅ Akun admin diperbarui: plan=premium, semua kategori aktif.")

    token    = accounts[0].get('token')
    admin_id = accounts[0].get('chat_id')
    if not token:
        print("❌ Token tidak ditemukan!"); return

    app = Application.builder().token(token).build()

    # USER
    app.add_handler(CommandHandler("start",      start))
    app.add_handler(CommandHandler("status",     status))
    app.add_handler(CommandHandler("unregister", unregister))
    app.add_handler(CommandHandler("categories", categories_command))
    app.add_handler(CommandHandler("export",     export_my_news))

    # MEDIA HANDLER ADMIN — upload untuk /msg dan /broadcast (group 0, prioritas lebih tinggi)
    app.add_handler(MessageHandler(
        (filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND,
        handle_admin_media
    ), group=0)

    # MEDIA HANDLER USER — bukti bayar (group 1)
    app.add_handler(MessageHandler(
        (filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND,
        handle_payment_proof
    ), group=1)

    # ADMIN
    app.add_handler(CommandHandler("payments",   admin_payments))
    app.add_handler(CommandHandler("pending",    admin_pending))
    app.add_handler(CommandHandler("list",       admin_list))
    app.add_handler(CommandHandler("user",       admin_user))
    app.add_handler(CommandHandler("kick",       admin_kick))
    app.add_handler(CommandHandler("ban",        admin_ban))
    app.add_handler(CommandHandler("unban",      admin_unban))
    app.add_handler(CommandHandler("disable",    admin_disable))
    app.add_handler(CommandHandler("enable",     admin_enable))
    app.add_handler(CommandHandler("msg",        admin_msg))
    app.add_handler(CommandHandler("broadcast",  admin_broadcast))
    app.add_handler(CommandHandler("stats",      admin_stats))
    app.add_handler(CommandHandler("export",     admin_export))
    app.add_handler(CommandHandler("kicklog",    admin_kicklog))
    app.add_handler(CommandHandler("help",       admin_help))
    app.add_handler(CommandHandler("deletenews", admin_deletenews))

    # CALLBACK
    app.add_handler(CallbackQueryHandler(button_handler))

    # SCHEDULER — dijalankan setelah event loop aktif via post_init
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_expiring_subscriptions, 'cron', hour=9, minute=0)

    async def on_startup(app):
        scheduler.start()
        logger.info("Scheduler started.")

    async def on_shutdown(app):
        if scheduler.running:
            scheduler.shutdown()
        logger.info("Scheduler stopped.")

    app.post_init = on_startup
    app.post_shutdown = on_shutdown

    print("=" * 72)
    print("🤖  NEWSBOT PRO — REGISTRATION & MANAGEMENT SYSTEM  v3.0")
    print("=" * 72)
    print(f"✅ Admin ID : {admin_id}")
    print(f"✅ Token    : {token[:12]}...")
    print()
    print("💳 PEMBAYARAN:")
    for m in PAYMENT_CONFIG['metode']:
        print(f"   {m['emoji']} {m['nama']:8}  {m['nomor']}  a/n {m['atas_nama']}")
    print()
    print("📦 PAKET:")
    for pk,plan in PRICING_PLANS.items():
        print(f"   {plan['label']:25}  {plan['harga']:12}  {plan['desc']}")
    print()
    print("🔧 FITUR BARU:")
    print("   ✅ FIX: konfirmasi bayar tidak error pada pesan foto")
    print("   ✅ Broadcast: teks, foto, file ke semua/individual member")
    print("   ✅ Export berita per user (hanya berita yang diterima)")
    print("   ✅ Pilih kategori per paket + toggle realtime")
    print("   ✅ Notifikasi langganan habis (7/3/1 hari)")
    print("   ✅ Auto-disable akun expired via scheduler")
    print("   ✅ Multi-tier pricing 4 paket")
    print("   ✅ Estimasi pendapatan di /stats")
    print("=" * 72)
    print("🚀 Bot started. Ctrl+C to stop.")
    print("=" * 72)

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
