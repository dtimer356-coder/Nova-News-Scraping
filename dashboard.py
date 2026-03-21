#!/usr/bin/env python3
"""
NOVA NEWSBOT - Dashboard Utama
Dengan filter lengkap berdasarkan database
"""

from flask import Flask, jsonify, render_template_string, request, Response
import sqlite3
import json
import os
import subprocess
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
import socket
import time
import yaml
import re
import csv
import io
from collections import defaultdict

app = Flask(__name__)

# ==================== LOAD SENTIMENT FROM YAML ====================
def load_sentiment_lexicon(yaml_file='sentiment_config.yaml'):
    """Load kamus sentimen dari file YAML"""
    lexicon = {}
    aspect_categories = defaultdict(list)
    
    try:
        with open(yaml_file, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        for aspect_name, aspect_data in config['aspects'].items():
            if 'positif' in aspect_name:
                sentiment_value = 0.7
            elif 'negatif' in aspect_name:
                sentiment_value = -0.7
            else:
                sentiment_value = 0.0
            
            for phrase in aspect_data['phrases']:
                phrase_lower = phrase.lower().strip()
                lexicon[phrase_lower] = sentiment_value
                aspect_categories[phrase_lower].append(aspect_name)
        
        print(f"✅ Loaded {len(lexicon)} sentiment phrases from YAML")
        return lexicon, aspect_categories
    except Exception as e:
        print(f"⚠️ Error loading YAML: {e}")
        return {}, defaultdict(list)

SENTIMENT_LEXICON, ASPECT_CATEGORIES = load_sentiment_lexicon()

# ==================== FUNGSI SENTIMENT ANALYSIS ====================
def analyze_sentiment(text):
    """Analisis sentimen teks berdasarkan kamus YAML"""
    if not text:
        return {'sentiment': 'neutral', 'score': 0, 'words': []}
    
    text_lower = text.lower()
    words = re.findall(r'\b[a-z0-9]+\b', text_lower)
    
    total_score = 0
    matches = 0
    matched_words = []
    
    # Cek kata per kata
    for word in words:
        if word in SENTIMENT_LEXICON:
            score = SENTIMENT_LEXICON[word]
            total_score += score
            matches += 1
            matched_words.append({'word': word, 'score': score})
    
    # Cek frasa 2 kata
    for i in range(len(words)-1):
        phrase = f"{words[i]} {words[i+1]}"
        if phrase in SENTIMENT_LEXICON:
            score = SENTIMENT_LEXICON[phrase]
            total_score += score
            matches += 1
            matched_words.append({'word': phrase, 'score': score})
    
    if matches == 0:
        return {'sentiment': 'neutral', 'score': 0, 'words': []}
    
    avg_score = total_score / matches
    
    if avg_score > 0.1:
        sentiment = 'positive'
    elif avg_score < -0.1:
        sentiment = 'negative'
    else:
        sentiment = 'neutral'
    
    return {
        'sentiment': sentiment,
        'score': round(avg_score, 2),
        'words': matched_words[:5]  # Top 5 kata
    }

# ==================== KONFIGURASI FILE ====================
DB_PATH = "news_bot.db"
ACCOUNTS_FILE = "accounts.json"
PENDING_FILE = "payment_pending.json"      # v3.0: payment_pending.json
SUBSCRIPTIONS_FILE = "subscriptions.json"  # v3.0: plan & expiry per user
KICK_LOG_FILE = "kick_log.json"
BLACKLIST_FILE = "blacklist.json"
OPML_FILE = "feeds.opml"
LOG_FILE = "register.log"                  # v3.0: register.log
FAILED_LOG_FILE = "failed.log"
SENT_FILE = "sent.json"                    # v3.0: sent cache D-NEWS
BACKUP_DIR = "backups"

PRICING_PLANS = {
    "basic":    {"label": "Basic",           "harga": "Rp 35.000",  "emoji": "🥉", "harga_int": 35000,  "periode": "1 bulan"},
    "standard": {"label": "Standard",        "harga": "Rp 45.000",  "emoji": "🥈", "harga_int": 45000,  "periode": "1 bulan"},
    "premium":  {"label": "Premium",         "harga": "Rp 65.000",  "emoji": "🥇", "harga_int": 65000,  "periode": "1 bulan"},
    "yearly":   {"label": "Premium Tahunan", "harga": "Rp 550.000", "emoji": "👑", "harga_int": 550000, "periode": "1 tahun"},
}

FAVORITES_FILE = "favorites.json"

def get_db_connection():
    """Koneksi ke database SQLite"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def load_json_file(filename, default_data=None):
    """Load JSON file dengan aman"""
    if default_data is None:
        default_data = [] if filename.endswith('.json') and 'pending' in filename else {}
    
    try:
        if os.path.exists(filename):
            with open(filename, 'r') as f:
                content = f.read().strip()
                if content:
                    return json.loads(content)
        return default_data
    except Exception as e:
        print(f"Error loading {filename}: {e}")
        return default_data

# ==================== FUNGSI STATISTIK ====================

def get_user_stats():
    """Ambil statistik user dari semua file JSON"""
    accounts = load_json_file(ACCOUNTS_FILE, [])
    pending = load_json_file(PENDING_FILE, [])  # payment_pending.json v3.0
    kick_log = load_json_file(KICK_LOG_FILE, [])
    blacklist = load_json_file(BLACKLIST_FILE, {})
    subscriptions = load_json_file(SUBSCRIPTIONS_FILE, {})
    
    # Harga per plan untuk estimasi revenue
    PLAN_PRICES = {pk: p["harga_int"] for pk, p in PRICING_PLANS.items()}
    
    admin_count = 1 if accounts else 0
    active_users = 0
    disabled_users = 0
    banned_users = 0
    plan_counts = {"basic": 0, "standard": 0, "premium": 0, "yearly": 0, "free": 0}
    revenue_estimate = 0
    
    now = datetime.now()
    expiring_soon = []  # user yang expire dalam 7 hari
    
    for acc in accounts[1:]:
        if acc.get('banned', False):
            banned_users += 1
        elif not acc.get('is_active', True):
            disabled_users += 1
        else:
            active_users += 1
        
        # Hitung plan dari subscriptions
        chat_id = str(acc.get('chat_id', ''))
        if chat_id in subscriptions:
            sub = subscriptions[chat_id]
            plan = sub.get('plan', 'free')
            plan_counts[plan] = plan_counts.get(plan, 0) + 1
            revenue_estimate += PLAN_PRICES.get(plan, 0)
            
            # Cek expiry
            expiry_str = sub.get('expiry')
            if expiry_str:
                try:
                    try:
                        expiry_dt = datetime.strptime(expiry_str, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        expiry_dt = datetime.fromisoformat(expiry_str)
                    days_left = (expiry_dt - now).days
                    if 0 <= days_left <= 7:
                        expiring_soon.append({
                            'name': acc.get('name', 'Unknown'),
                            'chat_id': chat_id,
                            'plan': plan,
                            'days_left': days_left,
                            'expiry': expiry_str
                        })
                except:
                    pass
        else:
            plan_counts['free'] = plan_counts.get('free', 0) + 1
    
    ALL_CATEGORIES_LABELS = {
        "technology":"💻 Teknologi","business":"📈 Bisnis","sports":"⚽ Olahraga",
        "entertainment":"🎬 Hiburan","science":"🔬 Sains","health":"🏥 Kesehatan",
        "politik":"🏛️ Politik","militer":"🎖️ Militer","general":"📰 Umum",
        "crypto":"₿ Kripto",
    }

    user_details = []
    try:
        conn = get_db_connection()
        for acc in accounts:  # semua akun termasuk admin
            chat_id = acc.get('chat_id')
            if chat_id:
                sent_count = conn.execute(
                    "SELECT COUNT(*) FROM sent_news WHERE account_id = ?", 
                    (str(chat_id),)
                ).fetchone()[0]
                
                last_sent = conn.execute(
                    "SELECT MAX(sent_at) FROM sent_news WHERE account_id = ?",
                    (str(chat_id),)
                ).fetchone()[0]
                
                # Ambil info subscription
                sub_info = subscriptions.get(str(chat_id), {})
                plan = sub_info.get('plan', 'free')
                expiry = sub_info.get('expiry', '')
                
                raw_cats = acc.get('categories', [])
                cat_labels = [ALL_CATEGORIES_LABELS.get(c, c) for c in raw_cats]
                is_admin_acc = (accounts.index(acc) == 0)
                user_details.append({
                    'chat_id': chat_id,
                    'name': acc.get('name', 'Unknown'),
                    'username': acc.get('username', ''),
                    'sent_count': sent_count,
                    'last_sent': last_sent,
                    'status': 'admin' if is_admin_acc else ('banned' if acc.get('banned') else ('disabled' if not acc.get('is_active') else 'active')),
                    'created_at': acc.get('created_at', 'Unknown'),
                    'plan': plan,
                    'plan_label': '👑 Admin' if is_admin_acc else PRICING_PLANS.get(plan, {}).get('label', plan.capitalize()),
                    'plan_harga': PRICING_PLANS.get(plan, {}).get('harga', '-'),
                    'expiry': expiry,
                    'categories': raw_cats,
                    'category_labels': cat_labels,
                    'favorites_count': 0
                })
        conn.close()
        try:
            favs = load_json_file(FAVORITES_FILE, {})
            for ud in user_details:
                ud['favorites_count'] = len(favs.get(str(ud['chat_id']), []))
        except Exception:
            pass
    except Exception as e:
        print(f"Error getting user details: {e}")
    
    return {
        'total_accounts': len(accounts),
        'admin_count': admin_count,
        'active_users': active_users,
        'disabled_users': disabled_users,
        'banned_users': banned_users,
        'pending_users': len(pending),
        'kicked_users': len(kick_log),
        'blacklisted_feeds': len(blacklist),
        'plan_counts': plan_counts,
        'revenue_estimate': revenue_estimate,
        'expiring_soon': expiring_soon,
        'user_details': user_details,
        'pending_details': pending,
        'kick_details': kick_log
    }

def get_news_stats():
    """Ambil statistik berita dari database"""
    stats = {
        'total_news': 0,
        'today_news': 0,
        'sent_news': 0,
        'today_sent': 0,
        'categories': [],
        'regions': []  # Tambahkan statistik region
    }
    
    if not os.path.exists(DB_PATH):
        return stats
    
    try:
        conn = get_db_connection()
        
        stats['total_news'] = conn.execute('SELECT COUNT(*) FROM news').fetchone()[0]
        stats['today_news'] = conn.execute('''
            SELECT COUNT(*) FROM news 
            WHERE date(published) = date('now')
        ''').fetchone()[0]
        stats['sent_news'] = conn.execute('SELECT COUNT(*) FROM sent_news').fetchone()[0]
        stats['today_sent'] = conn.execute('''
            SELECT COUNT(*) FROM sent_news 
            WHERE date(sent_at) = date('now')
        ''').fetchone()[0]
        
        # Statistik per kategori
        categories = conn.execute('''
            SELECT category, COUNT(*) as count 
            FROM news 
            WHERE category IS NOT NULL AND category != ''
            GROUP BY category 
            ORDER BY count DESC
        ''').fetchall()
        stats['categories'] = [dict(cat) for cat in categories]
        
        # Statistik per region (diekstrak dari sent_news? atau dari news?)
        # Karena region tidak disimpan di database, kita ambil dari data yang ada
        # atau bisa ditambahkan kolom region di database untuk akurasi lebih baik
        
        conn.close()
    except Exception as e:
        print(f"Error getting news stats: {e}")
    
    return stats

def get_feed_stats():
    """Ambil statistik RSS feeds dari OPML"""
    feeds = []
    active = 0
    failed = []
    
    if os.path.exists(OPML_FILE):
        try:
            tree = ET.parse(OPML_FILE)
            root = tree.getroot()
            for outline in root.findall('.//outline'):
                if 'xmlUrl' in outline.attrib:
                    feed_url = outline.get('xmlUrl')
                    title = outline.get('title') or outline.get('text') or 'Unknown'
                    category = outline.get('text', 'General')
                    feeds.append({
                        'url': feed_url,
                        'title': title,
                        'category': category
                    })
                    active += 1
                for child in outline.findall('outline'):
                    if 'xmlUrl' in child.attrib:
                        feed_url = child.get('xmlUrl')
                        title = child.get('title') or child.get('text') or 'Unknown'
                        category = outline.get('text', 'General')
                        feeds.append({
                            'url': feed_url,
                            'title': title,
                            'category': category
                        })
                        active += 1
        except Exception as e:
            print(f"Error parsing OPML: {e}")
    
    if os.path.exists(FAILED_LOG_FILE):
        try:
            with open(FAILED_LOG_FILE, 'r') as f:
                failed = f.readlines()[-20:]
        except:
            failed = []
    
    return {
        'total': len(feeds),
        'active': active,
        'failed': len(failed),
        'failed_list': [f.strip() for f in failed],
        'feeds': feeds
    }

def get_system_stats():
    """Ambil statistik sistem"""
    stats = {
        'bot_running': False,
        'register_bot_running': False,
        'file_sizes': {},
        'last_backup': None
    }
    
    try:
        result = subprocess.run(['pgrep', '-f', 'D_NEWS.py'], 
                               capture_output=True, text=True)
        stats['bot_running'] = result.returncode == 0
    except:
        pass
    
    try:
        result = subprocess.run(['pgrep', '-f', 'register.py'], 
                               capture_output=True, text=True)
        stats['register_bot_running'] = result.returncode == 0
    except:
        pass
    
    files_to_check = [
        DB_PATH, ACCOUNTS_FILE, PENDING_FILE, SUBSCRIPTIONS_FILE,
        KICK_LOG_FILE, BLACKLIST_FILE, OPML_FILE, LOG_FILE, SENT_FILE
    ]
    
    for file in files_to_check:
        if os.path.exists(file):
            size = os.path.getsize(file) / 1024
            stats['file_sizes'][os.path.basename(file)] = round(size, 2)
    
    if os.path.exists(BACKUP_DIR):
        backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith('backup_')])
        if backups:
            stats['last_backup'] = backups[-1]
            stats['backup_count'] = len(backups)
    
    return stats

# ==================== API ROUTES ====================

@app.route('/')
def index():
    """Halaman utama dashboard"""
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/stats')
def api_stats():
    """API untuk semua statistik"""
    return jsonify({
        'user_stats': get_user_stats(),
        'news_stats': get_news_stats(),
        'feed_stats': get_feed_stats(),
        'system_stats': get_system_stats(),
        'ai_available': len(SENTIMENT_LEXICON) > 0,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/users')
def api_users():
    """API untuk data users"""
    return jsonify(get_user_stats())

@app.route('/api/news')
def api_news():
    """API untuk berita dengan filter lengkap"""
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 10, type=int)
    category = request.args.get('category', 'all')
    search = request.args.get('search', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    offset = (page - 1) * limit

    # FIX: cek DB dulu sebelum koneksi
    if not os.path.exists(DB_PATH):
        return jsonify({'news': [], 'total': 0, 'page': 1, 'pages': 0,
                        'warning': 'Database belum tersedia'})

    try:
        conn = get_db_connection()

        # FIX: cek kolom apa saja yang tersedia di tabel news
        cols_raw = conn.execute("PRAGMA table_info(news)").fetchall()
        col_names = [c[1] for c in cols_raw]
        has_pop = 'popularity_score' in col_names

        # Query dasar — popularity_score aman via COALESCE jika ada
        if has_pop:
            select = "SELECT title, category, published, link, summary, sent_count, COALESCE(popularity_score, sent_count, 0) as pop_score FROM news"
        else:
            select = "SELECT title, category, published, link, summary, sent_count, sent_count as pop_score FROM news"

        params = []
        where_clauses = []

        if category and category != 'all':
            where_clauses.append("category = ?")
            params.append(category)

        if search:
            where_clauses.append("(title LIKE ? OR summary LIKE ?)")
            params.extend([f'%{search}%', f'%{search}%'])

        if start_date:
            where_clauses.append("date(published) >= ?")
            params.append(start_date)

        if end_date:
            where_clauses.append("date(published) <= ?")
            params.append(end_date)

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        count_query = "SELECT COUNT(*) FROM news" + where_sql
        total = conn.execute(count_query, params).fetchone()[0]

        query = select + where_sql + " ORDER BY published DESC LIMIT ? OFFSET ?"
        news = conn.execute(query, params + [limit, offset]).fetchall()
        conn.close()

        news_list = [dict(n) for n in news]

        return jsonify({
            'news': news_list,
            'total': total,
            'page': page,
            'pages': (total + limit - 1) // limit if total > 0 else 0
        })
    except Exception as e:
        import traceback
        print(f"[api_news ERROR] {e}")
        print(traceback.format_exc())
        return jsonify({'error': str(e), 'detail': traceback.format_exc()}), 500

@app.route('/api/news/all')
def api_news_all():
    """API untuk semua berita"""
    try:
        conn = get_db_connection()
        news = conn.execute('''
            SELECT title, category, published, link, summary, sent_count 
            FROM news 
            ORDER BY published DESC
        ''').fetchall()
        conn.close()
        return jsonify([dict(n) for n in news])
    except Exception as e:
        return jsonify([])

# ==================== API SENTIMENT ANALYSIS ====================

@app.route('/api/sentiment/analyze')
def api_sentiment_analyze():
    """API untuk menganalisis sentimen berita"""
    try:
        # Ambil parameter
        days = request.args.get('days', 7, type=int)
        limit = request.args.get('limit', 50, type=int)
        
        conn = get_db_connection()
        news = conn.execute('''
            SELECT title, summary, category, published 
            FROM news 
            WHERE published > datetime('now', ?)
            ORDER BY published DESC
            LIMIT ?
        ''', (f'-{days} days', limit)).fetchall()
        conn.close()
        
        if not news:
            return jsonify({
                'total_analyzed': 0,
                'message': 'Tidak ada berita untuk dianalisis'
            })
        
        results = []
        sentiment_counts = {'positive': 0, 'negative': 0, 'neutral': 0}
        
        for item in news:
            text = f"{item['title']} {item['summary'] or ''}"
            if len(text.strip()) < 20:
                continue
            
            analysis = analyze_sentiment(text)
            sentiment = analysis['sentiment']
            sentiment_counts[sentiment] += 1
            
            results.append({
                'title': item['title'][:100] + ('...' if len(item['title']) > 100 else ''),
                'category': item['category'] or 'general',
                'sentiment': sentiment,
                'score': analysis['score'],
                'words': analysis['words'],
                'published': item['published']
            })
        
        total = len(results)
        percentage = {}
        if total > 0:
            for key in sentiment_counts:
                percentage[key] = round((sentiment_counts[key] / total) * 100, 1)
        
        return jsonify({
            'total_analyzed': total,
            'days_analyzed': days,
            'summary': sentiment_counts,
            'percentage': percentage,
            'details': results[:20],  # Kirim 20 hasil pertama
            'lexicon_size': len(SENTIMENT_LEXICON),
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/api/sentiment/text', methods=['POST'])
def api_sentiment_text():
    """API untuk menganalisis teks langsung"""
    data = request.json
    text = data.get('text', '')
    
    if not text:
        return jsonify({'error': 'Teks tidak boleh kosong'})
    
    try:
        analysis = analyze_sentiment(text)
        return jsonify(analysis)
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/api/news/export/csv')
def export_news_csv():
    """Export berita ke CSV — semua berita sesuai filter tanggal, tanpa batas jumlah"""
    try:
        category   = request.args.get('category', 'all')
        start_date = request.args.get('start_date', '')
        end_date   = request.args.get('end_date', '')
        search     = request.args.get('search', '')

        conn = get_db_connection()

        query  = "SELECT hash_id, title, category, published, link, summary, sent_count FROM news"
        params = []
        where_clauses = []

        if category and category != 'all':
            where_clauses.append("category = ?")
            params.append(category)

        if search:
            where_clauses.append("(title LIKE ? OR summary LIKE ?)")
            params.extend([f'%{search}%', f'%{search}%'])

        if start_date:
            where_clauses.append("date(published) >= ?")
            params.append(start_date)

        if end_date:
            where_clauses.append("date(published) <= ?")
            params.append(end_date)

        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)

        query += " ORDER BY published DESC"
        # Tidak ada LIMIT — ambil SEMUA berita sesuai filter

        news = conn.execute(query, params).fetchall()
        total = len(news)
        conn.close()

        # Buat CSV dengan encoding UTF-8 BOM (agar Excel langsung terbaca)
        output = io.StringIO()
        writer = csv.writer(output, quoting=csv.QUOTE_ALL)

        # Header
        writer.writerow(['No', 'Judul', 'Kategori', 'Tanggal Terbit', 'Link', 'Ringkasan', 'Dibaca'])

        for idx, item in enumerate(news, 1):
            writer.writerow([
                idx,
                item['title'] or '',
                item['category'] or 'general',
                item['published'] or '',
                item['link'] or '',
                item['summary'] or '',   # FULL — tidak dipotong
                item['sent_count'] or 0
            ])

        # Nama file sertakan info filter supaya mudah diidentifikasi
        date_part = ''
        if start_date and end_date:
            date_part = f"_{start_date}_sd_{end_date}"
        elif start_date:
            date_part = f"_dari_{start_date}"
        elif end_date:
            date_part = f"_sampai_{end_date}"

        cat_part = f"_{category}" if category and category != 'all' else ""
        filename = f"berita{cat_part}{date_part}_{total}data_{datetime.now().strftime('%Y%m%d%H%M%S')}.csv"

        csv_bytes = '\ufeff' + output.getvalue()   # BOM untuk Excel

        return Response(
            csv_bytes,
            mimetype='text/csv; charset=utf-8',
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'X-Total-Records': str(total)
            }
        )

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/news/export/pdf')
def export_news_pdf():
    """Export berita ke PDF dengan filter — otomatis diterjemahkan ke Bahasa Indonesia"""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
        import io as io_module
        import re

        category  = request.args.get('category', 'all')
        start_date= request.args.get('start_date', '')
        end_date  = request.args.get('end_date', '')
        search    = request.args.get('search', '')

        conn = get_db_connection()

        q = "SELECT title, category, published, link, summary, sent_count FROM news"
        params = []
        where  = []

        if category and category != 'all':
            where.append("category = ?"); params.append(category)
        if search:
            where.append("(title LIKE ? OR summary LIKE ?)"); params.extend([f'%{search}%', f'%{search}%'])

        # Default: hanya berita hari ini & kemarin (sesuai hasil scan harian)
        # Kecuali user set manual lewat filter tanggal
        if start_date:
            where.append("date(published) >= ?"); params.append(start_date)
        else:
            where.append("date(published) >= date('now', '-1 day')")

        if end_date:
            where.append("date(published) <= ?"); params.append(end_date)
        else:
            where.append("date(published) <= date('now')")

        if where:
            q += " WHERE " + " AND ".join(where)

        # Maks 50 berita per kategori, cukup untuk terjemahan tidak berat
        limit = 50
        q += f" ORDER BY published DESC LIMIT {limit}"

        rows = conn.execute(q, params).fetchall()
        conn.close()

        if not rows:
            return jsonify({'error': 'Tidak ada berita untuk diexport'}), 404

        # ---- Helper functions ----
        def strip_emoji(text):
            return re.sub(r'[^\x00-\x7F\u00C0-\u024F\u1E00-\u1EFF]', '', str(text or '')).strip()

        def is_indonesian(text):
            """Cek kasar apakah teks sudah Bahasa Indonesia"""
            id_words = {
                'yang','dan','di','dengan','ini','itu','untuk','dari','ke','pada',
                'adalah','akan','telah','sudah','tidak','bisa','dalam','ada','atau',
                'juga','karena','saat','setelah','bahwa','sehingga','namun','lebih',
                'pemerintah','menteri','presiden','Indonesia','nasional','warga','rakyat',
            }
            words = set(re.findall(r'\b[a-zA-Z]+\b', text or ''))
            return len(words & id_words) >= 2

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
                return text  # Fallback: pakai teks asli

        CATEGORY_PLAIN = {
            "technology":"Teknologi","business":"Bisnis & Ekonomi",
            "sports":"Olahraga","entertainment":"Hiburan",
            "science":"Sains","health":"Kesehatan",
            "politik":"Politik","militer":"Militer",
            "general":"Umum","crypto":"Kripto & Blockchain",
        }

        buf = io_module.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4,
                                leftMargin=2*cm, rightMargin=2*cm,
                                topMargin=2*cm, bottomMargin=2*cm)
        styles  = getSampleStyleSheet()
        title_s = ParagraphStyle('TS', parent=styles['Heading1'], fontSize=16, spaceAfter=4,
                                 textColor=colors.HexColor('#1a1a2e'))
        head_s  = ParagraphStyle('HS', parent=styles['Normal'], fontSize=9,
                                 textColor=colors.grey, spaceAfter=12)
        cat_s   = ParagraphStyle('CS', parent=styles['Normal'], fontSize=8,
                                 textColor=colors.HexColor('#2563eb'), spaceBefore=10, spaceAfter=2)
        news_s  = ParagraphStyle('NS', parent=styles['Normal'], fontSize=10,
                                 fontName='Helvetica-Bold', spaceAfter=3, leading=14)
        sum_s   = ParagraphStyle('SS', parent=styles['Normal'], fontSize=9,
                                 textColor=colors.HexColor('#555555'), spaceAfter=2, leading=12)
        link_s  = ParagraphStyle('LS', parent=styles['Normal'], fontSize=8,
                                 textColor=colors.HexColor('#2563eb'), spaceAfter=2)
        date_s  = ParagraphStyle('DS', parent=styles['Normal'], fontSize=8,
                                 textColor=colors.grey, spaceAfter=6)

        story = []
        cat_label_filter = CATEGORY_PLAIN.get(category, 'Semua Kategori') if category != 'all' else 'Semua Kategori'
        tgl_dari  = start_date if start_date else (datetime.now() - timedelta(days=1)).strftime('%d %b %Y')
        tgl_sampai= end_date   if end_date   else datetime.now().strftime('%d %b %Y')
        story.append(Paragraph("Laporan Berita - NewsBot PRO Dashboard", title_s))
        story.append(Paragraph(
            f"Dibuat: {datetime.now().strftime('%d %B %Y, %H:%M')} WIB  |  "
            f"Kategori: {cat_label_filter}  |  Periode: {tgl_dari} s/d {tgl_sampai}  |  "
            f"Total: {len(rows)} berita  |  Bahasa: Indonesia",
            head_s))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#e5e7eb')))
        story.append(Spacer(1, 0.3*cm))

        for i, row in enumerate(rows, 1):
            raw_title   = row['title'] or ''
            raw_summary = (row['summary'] or '')[:400]
            cat         = row['category'] or 'general'
            pub         = row['published'] or ''
            link        = row['link'] or ''
            views       = row['sent_count'] or 0
            cat_lbl     = CATEGORY_PLAIN.get(cat, cat)

            # Terjemahkan ke Bahasa Indonesia
            title_id   = translate_to_id(raw_title)
            summary_id = translate_to_id(raw_summary) if raw_summary else ''

            safe_title   = strip_emoji(title_id).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;') or '(Tanpa judul)'
            safe_summary = strip_emoji(summary_id[:200]).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
            safe_link    = link.replace('&','&amp;')

            story.append(Paragraph(f"[{cat_lbl}]  <font size='7' color='grey'>#{i} | Dibaca: {views}x</font>", cat_s))
            story.append(Paragraph(safe_title, news_s))
            if safe_summary:
                story.append(Paragraph(safe_summary + ("..." if len(summary_id) > 200 else ""), sum_s))
            story.append(Paragraph(
                f"<a href='{safe_link}' color='#2563eb'>{safe_link[:90]}{'...' if len(safe_link)>90 else ''}</a>",
                link_s))
            story.append(Paragraph(f"Tanggal Terbit: {pub[:16]}", date_s))
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#f0f0f0')))

        doc.build(story)
        buf.seek(0)

        filename = f"newsbot_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        return Response(
            buf.getvalue(),
            mimetype='application/pdf',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )

    except ImportError:
        return jsonify({'error': 'Modul reportlab tidak terinstall. Jalankan: pip install reportlab'}), 500
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/pending')
def api_pending():
    """API untuk pending users"""
    pending = load_json_file(PENDING_FILE, [])
    return jsonify(pending)

@app.route('/api/kicked')
def api_kicked():
    """API untuk kicked users"""
    kick_log = load_json_file(KICK_LOG_FILE, [])
    return jsonify(kick_log)

@app.route('/api/blacklist')
def api_blacklist():
    """API untuk blacklisted feeds"""
    blacklist = load_json_file(BLACKLIST_FILE, {})
    return jsonify(blacklist)

@app.route('/api/feeds')
def api_feeds():
    """API untuk feed stats"""
    return jsonify(get_feed_stats())

@app.route('/api/logs')
def api_logs():
    """API untuk system logs"""
    logs = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, 'r') as f:
                logs = f.readlines()[-50:]
        except:
            logs = ['Error reading log file']
    return jsonify({'logs': logs})

@app.route('/api/failed')
def api_failed():
    """API untuk failed feeds"""
    failed = []
    if os.path.exists(FAILED_LOG_FILE):
        try:
            with open(FAILED_LOG_FILE, 'r') as f:
                failed = f.readlines()[-30:]
        except:
            failed = ['Error reading failed log']
    return jsonify({'failed': failed})

@app.route('/api/export/users')
def export_users():
    """Export users ke JSON"""
    accounts = load_json_file(ACCOUNTS_FILE, [])
    return jsonify(accounts)

@app.route('/api/export/news')
def export_news():
    """Export news ke JSON"""
    try:
        conn = get_db_connection()
        news = conn.execute('''
            SELECT title, category, published, link, summary, sent_count 
            FROM news ORDER BY published DESC LIMIT 1000
        ''').fetchall()
        conn.close()
        return jsonify([dict(n) for n in news])
    except:
        return jsonify([])

@app.route('/api/news/dates')
def api_news_dates():
    """API daftar tanggal berita yang tersedia"""
    try:
        conn = get_db_connection()
        rows = conn.execute('''
            SELECT date(published) as tgl, COUNT(*) as cnt
            FROM news
            GROUP BY date(published)
            ORDER BY date(published) DESC
            LIMIT 60
        ''').fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/news/categories')
def api_news_categories():
    """Daftar kategori yang tersedia di DB beserta jumlah berita"""
    try:
        if not os.path.exists(DB_PATH):
            return jsonify([])
        conn = get_db_connection()
        rows = conn.execute('''
            SELECT category, COUNT(*) as cnt
            FROM news
            WHERE category IS NOT NULL AND category != ''
            GROUP BY category
            ORDER BY cnt DESC
        ''').fetchall()
        conn.close()
        CATEGORY_PLAIN = {
            "technology":"Teknologi","business":"Bisnis & Ekonomi",
            "sports":"Olahraga","entertainment":"Hiburan",
            "science":"Sains","health":"Kesehatan",
            "politik":"Politik","militer":"Militer",
            "general":"Umum","crypto":"Kripto & Blockchain",
        }
        result = [
            {"key": r["category"], "label": CATEGORY_PLAIN.get(r["category"], r["category"].capitalize()), "count": r["cnt"]}
            for r in rows
        ]
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/news/delete/all', methods=['DELETE'])
def api_delete_all_news():
    """Hapus semua berita dari database"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sent_news")
        cursor.execute("DELETE FROM news")
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'deleted': deleted, 'message': 'Semua berita dihapus'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/news/delete/date/<tgl>', methods=['DELETE'])
def api_delete_news_by_date(tgl):
    """Hapus berita berdasarkan tanggal"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        # Hapus sent_news dulu
        cursor.execute('''
            DELETE FROM sent_news WHERE news_hash IN
            (SELECT hash_id FROM news WHERE date(published)=?)
        ''', (tgl,))
        cursor.execute("DELETE FROM news WHERE date(published)=?", (tgl,))
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'deleted': deleted, 'date': tgl})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/news/delete/item/<path:hash_id>', methods=['DELETE'])
def api_delete_news_item(hash_id):
    """Hapus satu berita berdasarkan hash_id"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT title FROM news WHERE hash_id=?", (hash_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({'success': False, 'error': 'Berita tidak ditemukan'}), 404
        title = row[0]
        cursor.execute("DELETE FROM sent_news WHERE news_hash=?", (hash_id,))
        cursor.execute("DELETE FROM news WHERE hash_id=?", (hash_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'title': title})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/news/list')
def api_news_list():
    """API daftar berita dengan hash_id untuk keperluan delete"""
    page   = request.args.get('page', 1, type=int)
    limit  = request.args.get('limit', 10, type=int)
    search = request.args.get('search', '')
    cat    = request.args.get('category', 'all')
    offset = (page - 1) * limit
    try:
        conn = get_db_connection()
        where = []
        params = []
        if cat and cat != 'all':
            where.append("category=?"); params.append(cat)
        if search:
            where.append("(title LIKE ? OR summary LIKE ?)"); params.extend([f'%{search}%', f'%{search}%'])
        w = ("WHERE " + " AND ".join(where)) if where else ""
        total = conn.execute(f"SELECT COUNT(*) FROM news {w}", params).fetchone()[0]
        rows  = conn.execute(
            f"SELECT hash_id, title, category, date(published) as tgl FROM news {w} ORDER BY published DESC LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()
        conn.close()
        return jsonify({'items': [dict(r) for r in rows], 'total': total, 'page': page, 'pages': (total+limit-1)//limit})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/broadcast', methods=['POST'])
def api_broadcast():
    """Simulasi broadcast message"""
    data = request.json
    message = data.get('message', '')
    accounts = load_json_file(ACCOUNTS_FILE, [])
    
    with open(LOG_FILE, 'a') as f:
        f.write(f"[BROADCAST] {datetime.now()}: {message}\n")
    
    return jsonify({
        'sent': len(accounts) - 1,
        'message': message,
        'timestamp': datetime.now().isoformat()
    })

# ==================== CHATBOT API ====================

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Cache data statis dashboard (refresh tiap 5 menit)
_dashboard_cache = {'data': None, 'ts': 0}

def get_dashboard_context_cached():
    """Ambil data statis dashboard dengan cache 5 menit"""
    now = time.time()
    if _dashboard_cache['data'] and (now - _dashboard_cache['ts']) < 300:
        return _dashboard_cache['data']

    try:
        conn = get_db_connection()

        # Statistik berita ringkas
        total_news      = conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]
        today_count     = conn.execute("SELECT COUNT(*) FROM news WHERE date(published)=date('now')").fetchone()[0]
        yesterday_count = conn.execute("SELECT COUNT(*) FROM news WHERE date(published)=date('now','-1 day')").fetchone()[0]
        week_count      = conn.execute("SELECT COUNT(*) FROM news WHERE date(published)>=date('now','-7 days')").fetchone()[0]
        sent_total      = conn.execute("SELECT COUNT(*) FROM sent_news").fetchone()[0]

        all_cats = conn.execute(
            "SELECT category, COUNT(*) as cnt FROM news GROUP BY category ORDER BY cnt DESC"
        ).fetchall()
        cat_detail = " | ".join([f"{r['category']}:{r['cnt']}" for r in all_cats])

        conn.close()

        # Data user, feed, sistem
        u = get_user_stats()
        f = get_feed_stats()
        s = get_system_stats()
        pc = u.get('plan_counts', {})

        expiring = u.get('expiring_soon', [])
        expiring_txt = ""
        if expiring:
            expiring_txt = "\nUser mau expire: " + ", ".join(
                [f"{x['name']}({x['plan']},{x['days_left']}hr)" for x in expiring[:5]]
            )

        ctx = f"""=== STATISTIK BERITA ===
Total: {total_news} | Hari ini: {today_count} | Kemarin: {yesterday_count} | 7 hari: {week_count} | Total terkirim: {sent_total}
Kategori: {cat_detail}

=== PENGGUNA ===
Total: {u.get('total_accounts',0)} | Aktif: {u.get('active_users',0)} | Nonaktif: {u.get('disabled_users',0)} | Banned: {u.get('banned_users',0)} | Pending: {u.get('pending_users',0)}
Paket — Basic:{pc.get('basic',0)} | Standard:{pc.get('standard',0)} | Premium:{pc.get('premium',0)} | Tahunan:{pc.get('yearly',0)} | Free:{pc.get('free',0)}
Estimasi revenue: Rp {u.get('revenue_estimate',0):,}{expiring_txt}

=== RSS FEED ===
Total:{f.get('total',0)} | Aktif:{f.get('active',0)} | Gagal:{f.get('failed',0)}

=== SISTEM ===
Bot D-NEWS: {'✅ Jalan' if s.get('bot_running') else '❌ Mati'} | Bot Register: {'✅ Jalan' if s.get('register_bot_running') else '❌ Mati'}
Backup terakhir: {s.get('last_backup','Belum ada')} | Jumlah backup: {s.get('backup_count',0)}

=== PAKET HARGA ===
Basic Rp35rb/bln | Standard Rp45rb/bln | Premium Rp65rb/bln | Tahunan Rp550rb/thn"""

        _dashboard_cache['data'] = ctx
        _dashboard_cache['ts']   = now
        return ctx
    except Exception as e:
        return f"(Gagal load dashboard: {e})"

@app.route('/api/chatbot', methods=['POST'])
def api_chatbot():
    """Chatbot berbasis Claude API — menjawab pertanyaan tentang berita di database"""
    import urllib.request
    import urllib.error

    data = request.get_json(silent=True) or {}
    user_message = (data.get('message') or '').strip()
    if not user_message:
        return jsonify({'error': 'Pesan tidak boleh kosong'}), 400

    api_key = ANTHROPIC_API_KEY
    if not api_key:
        return jsonify({'error': 'ANTHROPIC_API_KEY belum diset di environment variable'}), 500

    # ---- Ambil konteks berita dari database (sistem skor relevansi) ----
    news_context = ""
    try:
        conn = get_db_connection()

        # Statistik ringkas
        total_news = conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]
        categories = conn.execute(
            "SELECT category, COUNT(*) as cnt FROM news GROUP BY category ORDER BY cnt DESC LIMIT 5"
        ).fetchall()
        cat_summary = ", ".join([f"{r['category']}({r['cnt']})" for r in categories])

        # --- Deteksi kategori dari pertanyaan user ---
        CATEGORY_KEYWORDS = {
            'technology':    ['teknologi','tech','hp','gadget','software','hardware','ai','internet','aplikasi','digital'],
            'business':      ['bisnis','ekonomi','saham','investasi','pasar','perusahaan','dagang','ekspor','impor','rupiah'],
            'sports':        ['olahraga','sepakbola','bola','basket','tenis','olimpiade','atlet','gol','juara','liga'],
            'entertainment': ['hiburan','film','musik','artis','konser','drakor','series','netflix','lagu','celebrity'],
            'science':       ['sains','ilmu','penelitian','riset','penemuan','astronomi','fisika','kimia','biologi'],
            'health':        ['kesehatan','dokter','rumahsakit','penyakit','obat','vaksin','virus','covid','medis'],
            'politik':       ['politik','presiden','menteri','pemerintah','pemilu','dpr','partai','kebijakan','hukum'],
            'militer':       ['militer','tentara','perang','senjata','pertahanan','tni','nato','serangan','konflik'],
            'crypto':        ['crypto','bitcoin','ethereum','blockchain','nft','token','defi','altcoin','kripto'],
            'general':       ['umum','berita','hari','terbaru','terkini','update','nasional','internasional'],
        }

        msg_lower = user_message.lower()
        detected_category = None
        for cat, kws in CATEGORY_KEYWORDS.items():
            if any(kw in msg_lower for kw in kws):
                detected_category = cat
                break

        # --- Deteksi intent tanggal dari pertanyaan ---
        import re as _re
        date_filter = None   # None = tidak filter tanggal

        # Cek kata "hari ini" / "terbaru" / "terkini" / "sekarang"
        if any(w in msg_lower for w in ['hari ini','terkini','terbaru','sekarang','today']):
            date_filter = 'today'

        # Cek "kemarin"
        elif any(w in msg_lower for w in ['kemarin','yesterday']):
            date_filter = 'yesterday'

        # Cek "minggu ini" / "pekan ini"
        elif any(w in msg_lower for w in ['minggu ini','pekan ini','week','7 hari','seminggu']):
            date_filter = 'week'

        # Cek tanggal spesifik format: "tanggal 15", "15 maret", "15/03", "2026-03-15"
        else:
            # format: tanggal DD atau DD bulan
            BULAN = {'januari':'01','februari':'02','maret':'03','april':'04','mei':'05',
                     'juni':'06','juli':'07','agustus':'08','september':'09',
                     'oktober':'10','november':'11','desember':'12',
                     'january':'01','february':'02','march':'03','april':'04','may':'05',
                     'june':'06','july':'07','august':'08','september':'09',
                     'october':'10','november':'11','december':'12'}

            # Cek "DD bulan" atau "bulan DD"
            for bln_name, bln_num in BULAN.items():
                m = _re.search(rf'(\d{{1,2}})\s*{bln_name}', msg_lower)
                if not m:
                    m = _re.search(rf'{bln_name}\s*(\d{{1,2}})', msg_lower)
                if m:
                    day = m.group(1).zfill(2)
                    year = datetime.now().strftime('%Y')
                    date_filter = f"{year}-{bln_num}-{day}"
                    break

            # Cek "tanggal 15" atau angka 2 digit saja
            if not date_filter:
                m = _re.search(r'tanggal\s+(\d{1,2})', msg_lower)
                if m:
                    day  = m.group(1).zfill(2)
                    now  = datetime.now()
                    date_filter = f"{now.year}-{now.strftime('%m')}-{day}"

            # Cek format lengkap YYYY-MM-DD atau DD/MM/YYYY
            if not date_filter:
                m = _re.search(r'(\d{4}-\d{2}-\d{2})', msg_lower)
                if m:
                    date_filter = m.group(1)
            if not date_filter:
                m = _re.search(r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})', msg_lower)
                if m:
                    d, mo, y = m.group(1).zfill(2), m.group(2).zfill(2), m.group(3)
                    if len(y) == 2: y = '20' + y
                    date_filter = f"{y}-{mo}-{d}"

        # --- Ekstrak kata kunci dari pertanyaan ---
        STOPWORDS = {'yang','dan','di','ke','dari','ini','itu','ada','apa','untuk','dengan',
                     'atau','juga','sudah','belum','tidak','bisa','akan','pada','oleh',
                     'dalam','tentang','berita','terbaru','terkini','tolong','gimana','bagaimana',
                     'hari','kemarin','minggu','tanggal','semua','semua','kasih','tau','lihat'}
        raw_keywords = re.findall(r'\b\w{3,}\b', msg_lower)
        keywords = [kw for kw in raw_keywords if kw not in STOPWORDS][:8]

        # --- Ambil kandidat berita dari DB (dinamis per pertanyaan) ---
        conn = get_db_connection()
        candidate_params = []
        candidate_where  = []

        if date_filter == 'today':
            candidate_where.append("date(published) = date('now')")
        elif date_filter == 'yesterday':
            candidate_where.append("date(published) = date('now', '-1 day')")
        elif date_filter == 'week':
            candidate_where.append("date(published) >= date('now', '-7 days')")
        elif date_filter:
            candidate_where.append("date(published) = ?")
            candidate_params.append(date_filter)
        if detected_category:
            candidate_where.append("category = ?")
            candidate_params.append(detected_category)
        if keywords:
            like_parts = " OR ".join(["(LOWER(title) LIKE ? OR LOWER(summary) LIKE ?)" for _ in keywords[:6]])
            for kw in keywords[:6]:
                candidate_params.extend([f'%{kw}%', f'%{kw}%'])
            candidate_where.append(f"({like_parts})")

        where_sql  = ("WHERE " + " AND ".join(candidate_where)) if candidate_where else ""
        candidates = conn.execute(
            f"SELECT title, category, published, summary, link FROM news {where_sql} ORDER BY published DESC LIMIT 60",
            candidate_params
        ).fetchall()

        # Fallback: filter tanggal saja tanpa keyword
        if not candidates and date_filter:
            dw, dp = [], []
            if date_filter == 'today':    dw.append("date(published)=date('now')")
            elif date_filter == 'yesterday': dw.append("date(published)=date('now','-1 day')")
            elif date_filter == 'week':   dw.append("date(published)>=date('now','-7 days')")
            else:                         dw.append("date(published)=?"); dp.append(date_filter)
            candidates = conn.execute(
                f"SELECT title, category, published, summary, link FROM news WHERE {' AND '.join(dw)} ORDER BY published DESC LIMIT 60", dp
            ).fetchall()

        # Fallback terakhir
        if not candidates:
            candidates = conn.execute(
                "SELECT title, category, published, summary, link FROM news ORDER BY published DESC LIMIT 15"
            ).fetchall()

        conn.close()

        # Scoring relevansi
        def relevance_score(row):
            tl = (row['title']   or '').lower()
            sl = (row['summary'] or '').lower()
            sc = sum((3 if kw in tl else 0) + (1 if kw in sl else 0) for kw in keywords)
            if detected_category and row['category'] == detected_category: sc += 5
            return sc

        top_rows = sorted(candidates, key=relevance_score, reverse=True)[:10]

        # --- Susun bagian berita relevan (dinamis) ---
        news_section = ""
        if top_rows:
            news_section = f"\n=== BERITA RELEVAN"
            if date_filter == 'today':       news_section += f" HARI INI ({datetime.now().strftime('%Y-%m-%d')})"
            elif date_filter == 'yesterday': news_section += " KEMARIN"
            elif date_filter == 'week':      news_section += " 7 HARI TERAKHIR"
            elif date_filter:                news_section += f" TANGGAL {date_filter}"
            if detected_category:            news_section += f" [{detected_category.upper()}]"
            news_section += f" — {len(top_rows)} berita ===\n"
            for i, r in enumerate(top_rows, 1):
                pub     = (r['published'] or '')[:16]
                summary = (r['summary']   or '')[:100]
                news_section += f"{i}. [{r['category']}] {r['title']} ({pub})\n"
                if summary:    news_section += f"   {summary}\n"
                if r['link']:  news_section += f"   {r['link']}\n"

        # --- Gabungkan: cache (statis) + berita relevan (dinamis) ---
        static_ctx = get_dashboard_context_cached()
        news_context = f"Waktu: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n{static_ctx}{news_section}"

    except Exception as e:
        import traceback
        news_context = f"(Gagal membaca data: {e})"

    # ---- Hitung estimasi token sebelum kirim ----
    system_chars  = len(news_context) + 400   # +400 untuk instruksi system
    user_chars    = len(user_message)
    est_in_tokens = (system_chars + user_chars) // 4
    est_out_tokens = 350
    est_cost_usd   = (est_in_tokens * 3 + est_out_tokens * 15) / 1_000_000
    est_cost_idr   = est_cost_usd * 16000

    # ---- Panggil Claude API ----
    system_prompt = (
        f"Kamu adalah asisten dashboard bernama NOVA untuk aplikasi NOVA NEWSBOT v3.0. "
        f"Tugasmu menjawab pertanyaan apapun tentang data yang ada di dashboard: "
        f"berita, statistik, pengguna, paket, revenue, RSS feed, dan status sistem. "
        f"Jawab dalam Bahasa Indonesia yang jelas, padat, dan informatif. "
        f"Sertakan angka spesifik dari data yang tersedia. "
        f"Jika ada berita relevan, tampilkan judul dan linknya. "
        f"Gunakan format yang rapi dengan bullet point jika perlu.\n\n"
        f"DATA DASHBOARD SAAT INI:\n{news_context}"
    )

    payload = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1000,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_message}]
    }).encode('utf-8')

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode('utf-8'))
        reply = result.get('content', [{}])[0].get('text', 'Maaf, tidak ada respons.')
        # Hitung biaya aktual dari response API
        usage = result.get('usage', {})
        actual_in  = usage.get('input_tokens', est_in_tokens)
        actual_out = usage.get('output_tokens', est_out_tokens)
        actual_cost_usd = (actual_in * 3 + actual_out * 15) / 1_000_000
        actual_cost_idr = actual_cost_usd * 16000
        return jsonify({
            'reply': reply,
            'usage': {
                'input_tokens' : actual_in,
                'output_tokens': actual_out,
                'cost_usd'     : round(actual_cost_usd, 6),
                'cost_idr'     : round(actual_cost_idr, 1)
            }
        })
    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8', errors='replace')
        return jsonify({'error': f'Claude API error {e.code}: {err_body}'}), 500
    except Exception as e:
        return jsonify({'error': f'Gagal menghubungi Claude API: {e}'}), 500


# ==================== HTML TEMPLATE ====================

HTML_TEMPLATE = r'''
<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NOVA NEWSBOT - Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    <style>
        * { font-family: 'Inter', system-ui, sans-serif; }
        body { background: #0a0b0e; color: #fff; }
        .glass-card { 
            background: rgba(20, 22, 36, 0.8);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 1rem;
            transition: all 0.3s;
        }
        .glass-card:hover {
            border-color: #667eea;
            box-shadow: 0 8px 32px rgba(102,126,234,0.3);
        }
        .stat-value {
            font-size: 2rem;
            font-weight: bold;
            background: linear-gradient(135deg, #fff, #a5b4fc);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .tab-button {
            padding: 0.5rem 1rem;
            border-radius: 0.5rem;
            cursor: pointer;
            transition: all 0.3s;
        }
        .tab-button.active {
            background: linear-gradient(135deg, #667eea, #764ba2);
            color: white;
        }
        .modal {
            display: none;
            position: fixed;
            inset: 0;
            background: rgba(0,0,0,0.8);
            z-index: 1000;
            align-items: center;
            justify-content: center;
        }
        .modal.active { display: flex; }
        .modal-content {
            max-width: 800px;
            width: 90%;
            max-height: 80vh;
            overflow-y: auto;
        }
        .scrollbar-custom::-webkit-scrollbar {
            width: 8px;
            height: 8px;
        }
        .scrollbar-custom::-webkit-scrollbar-track {
            background: rgba(255,255,255,0.1);
            border-radius: 10px;
        }
        .scrollbar-custom::-webkit-scrollbar-thumb {
            background: linear-gradient(135deg, #667eea, #764ba2);
            border-radius: 10px;
        }
        .news-card {
            transition: all 0.3s;
            cursor: pointer;
        }
        .news-card:hover {
            transform: translateY(-2px);
            background: rgba(40,44,62,0.9);
        }
        .sentiment-positive { color: #10b981; }
        .sentiment-negative { color: #ef4444; }
        .sentiment-neutral { color: #9ca3af; }
        .region-badge {
            background: rgba(102, 126, 234, 0.2);
            color: #a5b4fc;
            padding: 0.25rem 0.5rem;
            border-radius: 0.5rem;
            font-size: 0.75rem;
            display: inline-flex;
            align-items: center;
            gap: 0.25rem;
        }

        /* ===== CHATBOT STYLES ===== */
        #nova-chatbot { position: fixed; bottom: 24px; right: 24px; z-index: 9999; font-family: 'Inter', system-ui, sans-serif; }
        #nova-toggle {
            width: 56px; height: 56px; border-radius: 50%;
            background: linear-gradient(135deg, #667eea, #764ba2);
            border: none; cursor: pointer; color: white; font-size: 1.4rem;
            box-shadow: 0 4px 20px rgba(102,126,234,0.5);
            display: flex; align-items: center; justify-content: center;
            transition: transform 0.2s, box-shadow 0.2s;
        }
        #nova-toggle:hover { transform: scale(1.1); box-shadow: 0 6px 28px rgba(102,126,234,0.7); }
        #nova-window {
            display: none; flex-direction: column;
            width: 360px; height: 520px;
            background: #0f1117; border: 1px solid rgba(102,126,234,0.4);
            border-radius: 16px; overflow: hidden;
            box-shadow: 0 20px 60px rgba(0,0,0,0.7);
            margin-bottom: 12px;
        }
        #nova-window.open { display: flex; }
        #nova-header {
            background: linear-gradient(135deg, #667eea, #764ba2);
            padding: 14px 16px; display: flex; align-items: center; gap: 10px;
        }
        #nova-header .nova-avatar { font-size: 1.3rem; }
        #nova-header .nova-title { font-weight: 700; color: white; font-size: 0.95rem; }
        #nova-header .nova-subtitle { font-size: 0.72rem; color: rgba(255,255,255,0.8); }
        #nova-header .nova-close { margin-left: auto; background: none; border: none; color: white; cursor: pointer; font-size: 1rem; opacity: 0.8; }
        #nova-header .nova-close:hover { opacity: 1; }
        #nova-messages {
            flex: 1; overflow-y: auto; padding: 14px;
            display: flex; flex-direction: column; gap: 10px;
            background: #0a0b0e;
        }
        #nova-messages::-webkit-scrollbar { width: 5px; }
        #nova-messages::-webkit-scrollbar-thumb { background: rgba(102,126,234,0.4); border-radius: 10px; }
        .nova-msg { max-width: 88%; padding: 10px 13px; border-radius: 12px; font-size: 0.84rem; line-height: 1.5; word-break: break-word; }
        .nova-msg.user { align-self: flex-end; background: linear-gradient(135deg, #667eea, #764ba2); color: white; border-bottom-right-radius: 4px; }
        .nova-msg.bot { align-self: flex-start; background: rgba(30,33,50,0.95); color: #e2e8f0; border: 1px solid rgba(102,126,234,0.2); border-bottom-left-radius: 4px; }
        .nova-msg.bot a { color: #a5b4fc; text-decoration: underline; }
        .nova-msg.typing span { display: inline-block; width: 7px; height: 7px; background: #a5b4fc; border-radius: 50%; animation: nova-bounce 1.2s infinite; margin: 0 2px; }
        .nova-msg.typing span:nth-child(2) { animation-delay: 0.2s; }
        .nova-msg.typing span:nth-child(3) { animation-delay: 0.4s; }
        @keyframes nova-bounce { 0%,60%,100%{transform:translateY(0)} 30%{transform:translateY(-6px)} }
        #nova-input-row {
            display: flex; gap: 8px; padding: 12px;
            background: #0f1117; border-top: 1px solid rgba(255,255,255,0.07);
        }
        #nova-input {
            flex: 1; background: rgba(255,255,255,0.07); border: 1px solid rgba(102,126,234,0.3);
            border-radius: 8px; color: white; padding: 8px 12px; font-size: 0.84rem; outline: none;
        }
        #nova-input:focus { border-color: #667eea; }
        #nova-send {
            background: linear-gradient(135deg, #667eea, #764ba2); border: none; border-radius: 8px;
            color: white; padding: 8px 14px; cursor: pointer; font-size: 0.9rem; transition: opacity 0.2s;
        }
        #nova-send:hover { opacity: 0.85; }
        #nova-send:disabled { opacity: 0.4; cursor: not-allowed; }
        #nova-quick-btns { padding: 0 12px 10px; display: flex; flex-wrap: wrap; gap: 6px; background: #0a0b0e; }
        .nova-quick { background: rgba(102,126,234,0.15); border: 1px solid rgba(102,126,234,0.3);
            color: #a5b4fc; border-radius: 20px; padding: 4px 10px; font-size: 0.75rem; cursor: pointer; transition: all 0.2s; }
        .nova-quick:hover { background: rgba(102,126,234,0.35); }
        #nova-badge {
            position: absolute; top: -4px; right: -4px; background: #ef4444;
            color: white; border-radius: 50%; width: 18px; height: 18px;
            font-size: 0.65rem; display: none; align-items: center; justify-content: center;
        }

        /* ===== DATE INPUT FIX (dark mode) ===== */
        input[type="date"] {
            color-scheme: dark;
            color: #fff;
            background: #1f2937;
        }
        input[type="date"]::-webkit-calendar-picker-indicator {
            filter: invert(1);
            cursor: pointer;
            opacity: 0.8;
        }
        input[type="date"]::-webkit-calendar-picker-indicator:hover {
            opacity: 1;
        }
    </style>
</head>
<body class="text-gray-100">

    <!-- Modal Export PDF per Kategori -->
    <div id="pdfCatModal" class="modal" style="z-index:1100;">
        <div class="modal-content glass-card p-6" style="max-width:480px;">
            <div class="flex justify-between items-center mb-4">
                <h3 class="text-xl font-bold">
                    <i class="fas fa-file-pdf mr-2 text-red-400"></i>
                    Export PDF — Pilih Kategori
                </h3>
                <button onclick="closePdfCatModal()" class="text-gray-400 hover:text-white text-2xl leading-none">&times;</button>
            </div>
            <p class="text-sm text-gray-400 mb-4">
                Pilih 1 kategori. Hanya berita <b>hari ini &amp; kemarin</b> (maks 50).<br>
                Judul &amp; ringkasan otomatis diterjemahkan ke <b>Bahasa Indonesia</b>.<br>
                <span class="text-yellow-400 text-xs"><i class="fas fa-info-circle mr-1"></i>Gunakan filter tanggal di tabel berita untuk rentang lain.</span>
            </p>
            <!-- Loading state -->
            <div id="pdfCatLoading" class="text-center py-6 text-gray-400">
                <i class="fas fa-spinner fa-spin mr-2"></i>Memuat daftar kategori...
            </div>
            <!-- Category list -->
            <div id="pdfCatList" class="hidden flex flex-col gap-2 max-h-72 overflow-y-auto pr-1"></div>
            <!-- Filter info -->
            <div id="pdfCatFilterInfo" class="hidden mt-3 text-xs text-yellow-400 bg-yellow-900/30 rounded-lg p-2">
                <i class="fas fa-info-circle mr-1"></i>
                <span id="pdfCatFilterText"></span>
            </div>
            <!-- Actions -->
            <div class="flex gap-3 mt-5">
                <button onclick="closePdfCatModal()" class="flex-1 px-4 py-2 bg-gray-700 rounded-lg hover:bg-gray-600 text-sm">
                    Batal
                </button>
                <button id="pdfDownloadAllBtn" onclick="doExportPDF('all')"
                        class="flex-1 px-4 py-2 bg-gray-600 rounded-lg hover:bg-gray-500 text-sm">
                    <i class="fas fa-download mr-1"></i>Semua (maks 100)
                </button>
            </div>
        </div>
    </div>

    <!-- Modal Sentiment Analysis -->
    <div id="sentimentModal" class="modal">
        <div class="modal-content glass-card p-6">
            <div class="flex justify-between items-center mb-4">
                <h3 class="text-xl font-bold">
                    <i class="fas fa-brain mr-2 text-purple-400"></i>
                    Analisis Sentimen Berita
                </h3>
                <button onclick="closeSentimentModal()" class="text-gray-400 hover:text-white">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            
            <div id="sentimentLoading" class="text-center py-8">
                <i class="fas fa-spinner fa-spin text-3xl text-purple-400"></i>
                <p class="mt-2">Menganalisis sentimen berita...</p>
            </div>
            
            <div id="sentimentResult" class="hidden">
                <!-- Ringkasan Sentimen -->
                <div class="grid grid-cols-3 gap-4 mb-6">
                    <div class="bg-green-500/20 p-4 rounded-lg text-center">
                        <div class="text-3xl font-bold text-green-400" id="sentimentPositif">0</div>
                        <div class="text-sm text-gray-400">Positif</div>
                    </div>
                    <div class="bg-red-500/20 p-4 rounded-lg text-center">
                        <div class="text-3xl font-bold text-red-400" id="sentimentNegatif">0</div>
                        <div class="text-sm text-gray-400">Negatif</div>
                    </div>
                    <div class="bg-gray-500/20 p-4 rounded-lg text-center">
                        <div class="text-3xl font-bold text-gray-400" id="sentimentNetral">0</div>
                        <div class="text-sm text-gray-400">Netral</div>
                    </div>
                </div>
                
                <!-- Progress Bar -->
                <div class="mb-6">
                    <div class="flex justify-between text-sm mb-2">
                        <span>Total Berita Dianalisis: <span id="sentimentTotal">0</span></span>
                        <span>Kamus: <span id="lexiconSize">0</span> kata</span>
                    </div>
                    <div class="flex h-4 rounded-full overflow-hidden">
                        <div id="positifBar" class="bg-green-500 h-full" style="width: 0%"></div>
                        <div id="negatifBar" class="bg-red-500 h-full" style="width: 0%"></div>
                        <div id="netralBar" class="bg-gray-500 h-full" style="width: 0%"></div>
                    </div>
                </div>
                
                <!-- Detail Hasil -->
                <h4 class="font-semibold mb-2 flex items-center">
                    <i class="fas fa-list mr-2 text-purple-400"></i>
                    Detail Analisis (20 Berita Terbaru):
                </h4>
                <div id="sentimentDetails" class="space-y-2 max-h-60 overflow-y-auto scrollbar-custom pr-2"></div>
            </div>
            
            <div id="sentimentError" class="hidden text-center py-4 text-red-400"></div>
        </div>
    </div>


    <!-- Modal Hapus Berita -->
    <div id="deleteNewsModal" class="modal" style="z-index:1000;">
        <div class="modal-content glass-card p-6" style="max-width:640px;width:95%;max-height:85vh;display:flex;flex-direction:column;">
            <div class="flex justify-between items-center mb-4">
                <h3 class="text-xl font-bold text-red-400"><i class="fas fa-trash-alt mr-2"></i>Hapus Berita</h3>
                <button onclick="closeDeleteNewsModal()" class="text-gray-400 hover:text-white text-xl"><i class="fas fa-times"></i></button>
            </div>
            <div class="flex gap-2 mb-4">
                <button onclick="setDeleteMode(\'all\')" id="dmode-all" class="flex-1 py-2 rounded-lg bg-red-700 text-sm font-semibold border-2 border-red-400">🗑️ Semua</button>
                <button onclick="setDeleteMode(\'date\')" id="dmode-date" class="flex-1 py-2 rounded-lg bg-gray-700 text-sm font-semibold border-2 border-transparent">📅 Per Tanggal</button>
                <button onclick="setDeleteMode(\'item\')" id="dmode-item" class="flex-1 py-2 rounded-lg bg-gray-700 text-sm font-semibold border-2 border-transparent">🔍 Per Berita</button>
            </div>
            <div id="dpanel-all" style="flex:1;">
                <div class="bg-red-900/30 border border-red-700 rounded-lg p-4 mb-4">
                    <p class="text-red-300 font-semibold">⚠️ PERINGATAN!</p>
                    <p class="text-sm text-gray-300 mt-1">Menghapus <strong>semua berita</strong> dan riwayat pengiriman. Tidak bisa dibatalkan!</p>
                </div>
                <button onclick="confirmDeleteAll()" class="w-full py-3 bg-red-600 hover:bg-red-500 rounded-lg font-bold">
                    <i class="fas fa-trash-alt mr-2"></i>Ya, Hapus Semua Berita
                </button>
            </div>
            <div id="dpanel-date" style="flex:1;display:none;flex-direction:column;">
                <p class="text-sm text-gray-400 mb-3">Pilih tanggal yang ingin dihapus beritanya:</p>
                <div id="dateList" class="overflow-y-auto space-y-2 scrollbar-custom" style="max-height:320px;">
                    <p class="text-gray-400 text-sm">Memuat...</p>
                </div>
            </div>
            <div id="dpanel-item" style="flex:1;display:none;flex-direction:column;">
                <div class="flex gap-2 mb-3">
                    <input type="text" id="deleteSearchInput" placeholder="Cari judul..." 
                           class="flex-1 bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm"
                           oninput="loadDeleteItemList(1)">
                    <select id="deleteCatFilter" class="bg-gray-800 border border-gray-600 rounded-lg px-2 py-2 text-sm" onchange="loadDeleteItemList(1)">
                        <option value="all">Semua</option>
                        <option value="technology">Teknologi</option>
                        <option value="business">Bisnis</option>
                        <option value="sports">Olahraga</option>
                        <option value="entertainment">Hiburan</option>
                        <option value="science">Sains</option>
                        <option value="health">Kesehatan</option>
                        <option value="politik">Politik</option>
                        <option value="militer">Militer</option>
                        <option value="general">Umum</option>
                        <option value="crypto">Kripto</option>
                    </select>
                </div>
                <div id="itemList" class="overflow-y-auto space-y-1 scrollbar-custom" style="max-height:280px;">
                    <p class="text-gray-400 text-sm">Memuat...</p>
                </div>
                <div id="itemPagination" class="flex justify-between items-center mt-3 text-sm text-gray-400"></div>
            </div>
            <div id="deleteStatus" class="mt-3 text-sm hidden"></div>
        </div>
    </div>

    <!-- Modal Broadcast -->
    <div id="broadcastModal" class="modal">
        <div class="modal-content glass-card p-6">
            <h3 class="text-xl font-bold mb-4"><i class="fas fa-broadcast-tower mr-2 text-blue-400"></i>Broadcast Message</h3>
            <textarea id="broadcastMessage" rows="4" 
                      class="w-full bg-gray-800/50 border border-gray-700 rounded-lg p-3 mb-4"
                      placeholder="Tulis pesan untuk semua user..."></textarea>
            <div class="flex justify-end gap-2">
                <button onclick="closeBroadcastModal()" class="px-4 py-2 rounded-lg border border-gray-700">Batal</button>
                <button onclick="sendBroadcast()" class="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700">Kirim</button>
            </div>
        </div>
    </div>

    <!-- Main Container -->
    <div class="container mx-auto px-4 py-6 max-w-7xl">
        <!-- Header -->
        <div class="glass-card p-6 mb-6">
            <div class="flex items-center justify-between">
                <div class="flex items-center space-x-4">
                    <i class="fas fa-robot text-5xl text-blue-400"></i>
                    <div>
                        <h1 class="text-3xl font-bold">NOVA NEWSBOT</h1>
                        <p class="text-gray-400">By Dylan Wijaya</p>
                    </div>
                </div>
                <div class="flex items-center space-x-4">
                    <div class="px-4 py-2 bg-gray-800 rounded-lg">
                        <span id="currentTime" class="text-xl font-mono"></span>
                    </div>
                    <div id="statusBadge" class="px-4 py-2 rounded-lg bg-yellow-600/20 text-yellow-400">
                        Loading...
                    </div>
                </div>
            </div>
        </div>

        <!-- Tab Navigation -->
        <div class="glass-card p-4 mb-6 flex flex-wrap gap-2">
            <button onclick="showTab('dashboard')" class="tab-button active" id="tab-dashboard">
                <i class="fas fa-tachometer-alt mr-2"></i>Dashboard
            </button>
            <button onclick="showTab('news')" class="tab-button" id="tab-news">
                <i class="fas fa-newspaper mr-2"></i>Berita
            </button>
            <button onclick="showTab('users')" class="tab-button" id="tab-users">
                <i class="fas fa-users mr-2"></i>Users
            </button>
            <button onclick="showTab('pending')" class="tab-button" id="tab-pending">
                <i class="fas fa-clock mr-2"></i>Pending
            </button>
            <button onclick="showTab('kicked')" class="tab-button" id="tab-kicked">
                <i class="fas fa-history mr-2"></i>Kick Log
            </button>
            <button onclick="showTab('feeds')" class="tab-button" id="tab-feeds">
                <i class="fas fa-rss mr-2"></i>Feeds
            </button>
            <button onclick="showTab('system')" class="tab-button" id="tab-system">
                <i class="fas fa-server mr-2"></i>System
            </button>
        </div>

        <!-- DASHBOARD TAB -->
        <div id="dashboard-tab" class="tab-content">
            <!-- Stats Grid -->
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-6">
                <div class="glass-card p-6">
                    <div class="flex justify-between items-start">
                        <div>
                            <p class="text-gray-400 text-sm">Total News</p>
                            <p class="stat-value" id="totalNews">0</p>
                        </div>
                        <i class="fas fa-newspaper text-3xl text-blue-400"></i>
                    </div>
                    <div class="mt-2 text-sm text-gray-400">
                        Today: <span id="todayNews" class="text-white">0</span>
                    </div>
                </div>
                
                <div class="glass-card p-6">
                    <div class="flex justify-between items-start">
                        <div>
                            <p class="text-gray-400 text-sm">Sent News</p>
                            <p class="stat-value" id="sentNews">0</p>
                        </div>
                        <i class="fas fa-check-circle text-3xl text-green-400"></i>
                    </div>
                    <div class="mt-2 text-sm text-gray-400">
                        Today: <span id="todaySent" class="text-white">0</span>
                    </div>
                </div>
                
                <div class="glass-card p-6">
                    <div class="flex justify-between items-start">
                        <div>
                            <p class="text-gray-400 text-sm">Total Users</p>
                            <p class="stat-value" id="totalUsers">0</p>
                        </div>
                        <i class="fas fa-users text-3xl text-purple-400"></i>
                    </div>
                    <div class="mt-2 text-sm text-gray-400">
                        Active: <span id="activeUsers" class="text-green-400">0</span>
                    </div>
                </div>
                
                <div class="glass-card p-6">
                    <div class="flex justify-between items-start">
                        <div>
                            <p class="text-gray-400 text-sm">Total Feeds</p>
                            <p class="stat-value" id="totalFeeds">0</p>
                        </div>
                        <i class="fas fa-rss text-3xl text-orange-400"></i>
                    </div>
                    <div class="mt-2 text-sm text-gray-400">
                        Active: <span id="activeFeeds" class="text-green-400">0</span>
                    </div>
                </div>
            </div>

            <!-- AI Status dan Tombol Analisis -->
            <div class="glass-card p-4 mb-6 flex items-center justify-between">
                <div class="flex items-center">
                    <i class="fas fa-brain text-2xl text-purple-400 mr-3"></i>
                    <span>Sentiment Analysis:</span>
                </div>
                <div class="flex items-center gap-4">
                    <span id="aiStatus" class="px-3 py-1 rounded-full bg-green-500/20 text-green-400">Checking...</span>
                    <button onclick="showSentimentAnalysis()" class="px-4 py-2 bg-purple-600 rounded-lg hover:bg-purple-700">
                        <i class="fas fa-chart-pie mr-2"></i>Lihat Analisis
                    </button>
                </div>
            </div>

            <!-- Kategori Chart -->
            <div class="glass-card p-6 mb-6">
                <h3 class="text-lg font-semibold mb-4">📊 Distribusi Kategori</h3>
                <div id="categoryStats" class="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <!-- Akan diisi JS -->
                </div>
            </div>

            <!-- User Status -->
            <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
                <div class="glass-card p-4 text-center">
                    <i class="fas fa-user-check text-2xl text-green-400 mb-2"></i>
                    <p class="text-2xl font-bold" id="activeUsersCount">0</p>
                    <p class="text-xs text-gray-400">Active</p>
                </div>
                <div class="glass-card p-4 text-center">
                    <i class="fas fa-user-clock text-2xl text-yellow-400 mb-2"></i>
                    <p class="text-2xl font-bold" id="pendingUsersCount">0</p>
                    <p class="text-xs text-gray-400">Pending</p>
                </div>
                <div class="glass-card p-4 text-center">
                    <i class="fas fa-ban text-2xl text-red-400 mb-2"></i>
                    <p class="text-2xl font-bold" id="bannedUsersCount">0</p>
                    <p class="text-xs text-gray-400">Banned</p>
                </div>
                <div class="glass-card p-4 text-center">
                    <i class="fas fa-history text-2xl text-orange-400 mb-2"></i>
                    <p class="text-2xl font-bold" id="kickedUsersCount">0</p>
                    <p class="text-xs text-gray-400">Kicked</p>
                </div>
            </div>

            <!-- Revenue & Plan Breakdown -->
            <div class="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
                <div class="glass-card p-6">
                    <h3 class="text-lg font-semibold mb-4">💰 Estimasi Revenue</h3>
                    <div class="text-3xl font-bold text-green-400 mb-2" id="revenueEstimate">Rp 0</div>
                    <div class="text-sm text-gray-400">Dari subscriber aktif</div>
                    <div class="mt-4 space-y-2" id="planBreakdown">
                        <!-- diisi JS -->
                    </div>
                </div>
                <div class="glass-card p-6">
                    <h3 class="text-lg font-semibold mb-4">⏰ Segera Expired <span class="text-yellow-400 text-sm">(≤7 hari)</span></h3>
                    <div id="expiringSoonList" class="space-y-2 max-h-48 overflow-y-auto scrollbar-custom">
                        <p class="text-gray-400 text-sm">Memuat data...</p>
                    </div>
                </div>
            </div>

        </div>

        <!-- NEWS TAB - Dengan Filter Lengkap -->
        <div id="news-tab" class="tab-content hidden">
            <div class="glass-card p-6">
                <!-- Filter Bar -->
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
                    <input type="text" id="searchNews" placeholder="🔍 Cari judul atau konten..." 
                           class="w-full bg-gray-800/50 border border-gray-700 rounded-lg px-4 py-2 text-white placeholder-gray-400">
                    
                    <select id="categoryFilter" class="w-full bg-gray-800/50 border border-gray-700 rounded-lg px-4 py-2 text-white">
                        <option value="all">Semua Kategori</option>
                        <option value="technology">💻 Teknologi</option>
                        <option value="science">🔬 Sains</option>
                        <option value="entertainment">🎬 Entertainment</option>
                        <option value="general">📰 General</option>
                        <option value="business">📈 Bisnis</option>
                        <option value="health">🏥 Health</option>
                        <option value="sports">⚽ Sport</option>
                        <option value="militer">🎖️ Militer</option>
                        <option value="politik">🏛️ Politik</option>
                        <option value="crypto">₿ Crypto</option>
                    </select>
                    
                    <div class="flex flex-col gap-1">
                        <label class="text-xs text-gray-400 px-1">📅 Dari Tanggal</label>
                        <input type="date" id="startDateFilter"
                               style="color-scheme: dark;"
                               class="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-white cursor-pointer hover:border-blue-500 focus:border-blue-500 focus:outline-none">
                    </div>

                    <div class="flex flex-col gap-1">
                        <label class="text-xs text-gray-400 px-1">📅 Sampai Tanggal</label>
                        <input type="date" id="endDateFilter"
                               style="color-scheme: dark;"
                               class="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-white cursor-pointer hover:border-blue-500 focus:border-blue-500 focus:outline-none">
                    </div>
                </div>
                
                <!-- Action Buttons -->
                <div class="flex flex-wrap gap-2 mb-6">
                    <button onclick="applyFilters()" class="px-4 py-2 bg-blue-600 rounded-lg hover:bg-blue-700">
                        <i class="fas fa-filter mr-2"></i>Terapkan Filter
                    </button>
                    
                    <button onclick="resetFilters()" class="px-4 py-2 bg-gray-600 rounded-lg hover:bg-gray-700">
                        <i class="fas fa-undo mr-2"></i>Reset
                    </button>
                    
                    <button onclick="exportNewsCSV()" class="px-4 py-2 bg-green-600 rounded-lg hover:bg-green-700">
                        <i class="fas fa-file-csv mr-2"></i>Export CSV
                    </button>
                    
                    <button onclick="exportNewsPDF()" class="px-4 py-2 bg-red-700 rounded-lg hover:bg-red-600">
                        <i class="fas fa-file-pdf mr-2"></i>Export PDF (pilih kategori)
                    </button>
                    
                    <button onclick="refreshNews()" class="px-4 py-2 bg-blue-600 rounded-lg hover:bg-blue-700">
                        <i class="fas fa-sync-alt mr-2"></i>Refresh
                    </button>

                    <button onclick="showDeleteNewsModal()" class="px-4 py-2 bg-red-600 rounded-lg hover:bg-red-700">
                        <i class="fas fa-trash-alt mr-2"></i>Hapus Berita
                    </button>
                </div>
                
                <!-- Results Info -->
                <div class="flex justify-between items-center mb-4 text-sm text-gray-400">
                    <span>Ditemukan <span id="newsTotalCount">0</span> berita</span>
                    <span>Halaman <span id="currentPage">1</span></span>
                </div>
                
                <!-- News Grid -->
                <div id="newsGrid" class="grid grid-cols-1 md:grid-cols-2 gap-4 max-h-[600px] overflow-y-auto pr-2 scrollbar-custom"></div>
                
                <!-- Loading -->
                <div id="newsLoading" class="text-center py-4 hidden">
                    <i class="fas fa-spinner fa-spin text-2xl text-blue-400"></i>
                </div>
                
                <!-- Load More Button -->
                <div class="text-center mt-4">
                    <button onclick="loadMore()" id="loadMoreBtn" class="px-4 py-2 bg-purple-600 rounded-lg hover:bg-purple-700 hidden">
                        <i class="fas fa-arrow-down mr-2"></i>Muat Lebih Banyak
                    </button>
                </div>
            </div>
        </div>

        <!-- USERS TAB -->
        <div id="users-tab" class="tab-content hidden">
            <div class="glass-card p-6">
                <div class="flex justify-between mb-4">
                    <h2 class="text-xl font-bold">👥 Daftar User Aktif</h2>
                    <div class="space-x-2">
                        <button onclick="exportUsers()" class="px-4 py-2 bg-green-600 rounded-lg hover:bg-green-700">
                            <i class="fas fa-download mr-2"></i>Export
                        </button>
                        <button onclick="showBroadcastModal()" class="px-4 py-2 bg-purple-600 rounded-lg hover:bg-purple-700">
                            <i class="fas fa-broadcast-tower mr-2"></i>Broadcast
                        </button>
                    </div>
                </div>
                
                <div class="overflow-x-auto">
                    <table class="w-full">
                        <thead>
                            <tr class="border-b border-gray-700">
                                <th class="text-left py-3 px-4">No</th>
                                <th class="text-left py-3 px-4">Nama</th>
                                <th class="text-left py-3 px-4">Chat ID</th>
                                <th class="text-left py-3 px-4">Status</th>
                                <th class="text-left py-3 px-4">Plan</th>
                                <th class="text-left py-3 px-4">Expiry</th>
                                <th class="text-left py-3 px-4">Berita</th>
                                <th class="text-left py-3 px-4">Kategori</th>
                                <th class="text-left py-3 px-4">Terakhir</th>
                            </tr>
                        </thead>
                        <tbody id="userTableBody"></tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- PENDING TAB -->
        <div id="pending-tab" class="tab-content hidden">
            <div class="glass-card p-6">
                <h2 class="text-xl font-bold mb-4">⏳ Pending Pembayaran</h2>
                <div class="overflow-x-auto">
                    <table class="w-full">
                        <thead>
                            <tr class="border-b border-gray-700">
                                <th class="text-left py-3 px-4">No</th>
                                <th class="text-left py-3 px-4">Nama</th>
                                <th class="text-left py-3 px-4">Username</th>
                                <th class="text-left py-3 px-4">Chat ID</th>
                                <th class="text-left py-3 px-4">Plan</th>
                                <th class="text-left py-3 px-4">Metode</th>
                                <th class="text-left py-3 px-4">Waktu</th>
                            </tr>
                        </thead>
                        <tbody id="pendingTableBody"></tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- KICKED TAB -->
        <div id="kicked-tab" class="tab-content hidden">
            <div class="glass-card p-6">
                <h2 class="text-xl font-bold mb-4">📋 Riwayat Kick</h2>
                <div class="overflow-x-auto">
                    <table class="w-full">
                        <thead>
                            <tr class="border-b border-gray-700">
                                <th class="text-left py-3 px-4">No</th>
                                <th class="text-left py-3 px-4">Nama</th>
                                <th class="text-left py-3 px-4">Chat ID</th>
                                <th class="text-left py-3 px-4">Kicked At</th>
                                <th class="text-left py-3 px-4">Alasan</th>
                            </tr>
                        </thead>
                        <tbody id="kickedTableBody"></tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- FEEDS TAB -->
        <div id="feeds-tab" class="tab-content hidden">
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <div class="glass-card p-6">
                    <h2 class="text-xl font-bold mb-4">📡 Active Feeds</h2>
                    <div id="feedList" class="space-y-2 max-h-96 overflow-y-auto scrollbar-custom"></div>
                </div>
                <div class="glass-card p-6">
                    <h2 class="text-xl font-bold mb-4">⚠️ Failed Feeds</h2>
                    <div id="failedFeedsList" class="space-y-1 max-h-96 overflow-y-auto scrollbar-custom text-red-400"></div>
                </div>
            </div>
        </div>

        <!-- SYSTEM TAB -->
        <div id="system-tab" class="tab-content hidden">
            <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div class="glass-card p-6">
                    <h2 class="text-xl font-bold mb-4">🤖 Bot Status</h2>
                    <div class="space-y-3">
                        <div class="flex justify-between">
                            <span>Main Bot (D-NEWS.py):</span>
                            <span id="botStatus" class="px-2 py-1 rounded bg-yellow-600/20">Checking...</span>
                        </div>
                        <div class="flex justify-between">
                            <span>Register Bot (register.py):</span>
                            <span id="registerStatus" class="px-2 py-1 rounded bg-yellow-600/20">Checking...</span>
                        </div>
                    </div>
                </div>
                
                <div class="glass-card p-6">
                    <h2 class="text-xl font-bold mb-4">📁 File Sizes</h2>
                    <div id="fileSizes" class="space-y-2 max-h-60 overflow-y-auto scrollbar-custom"></div>
                </div>
                
                <div class="glass-card p-6">
                    <h2 class="text-xl font-bold mb-4">💾 Backup Info</h2>
                    <div id="backupInfo" class="space-y-2"></div>
                </div>
                
                <div class="glass-card p-6">
                    <h2 class="text-xl font-bold mb-4">⚙️ Quick Actions</h2>
                    <div class="space-y-2">
                        <button onclick="exportNews()" class="w-full px-4 py-2 bg-blue-600 rounded-lg hover:bg-blue-700">
                            <i class="fas fa-download mr-2"></i>Export News JSON
                        </button>
                        <button onclick="exportUsers()" class="w-full px-4 py-2 bg-green-600 rounded-lg hover:bg-green-700">
                            <i class="fas fa-download mr-2"></i>Export Users JSON
                        </button>
                        <button onclick="exportNewsCSV()" class="w-full px-4 py-2 bg-purple-600 rounded-lg hover:bg-purple-700">
                            <i class="fas fa-file-csv mr-2"></i>Export News CSV
                        </button>
                        <button onclick="exportNewsPDF()" class="w-full px-4 py-2 bg-red-700 rounded-lg hover:bg-red-600">
                            <i class="fas fa-file-pdf mr-2"></i>Export News PDF (pilih kategori)
                        </button>
                    </div>
                </div>
            </div>
        </div>

        <!-- Footer -->
        <div class="glass-card p-4 mt-6 text-center text-sm text-gray-400">
            <span id="lastUpdate">Loading...</span> | 
            <i class="fas fa-brain ml-2 mr-1 text-purple-400"></i> NOVA NEWSBOT
        </div>
    </div>

    <script>
        // ==================== GLOBAL VARIABLES ====================
        let currentPage = 1;
        let totalPages = 1;
        let isLoading = false;
        let currentFilters = {
            category: 'all',
            search: '',
            startDate: '',
            endDate: ''
        };
        let aiAvailable = false;

        // ==================== TIME UPDATE ====================
        function updateTime() {
            const now = new Date();
            document.getElementById('currentTime').innerHTML = now.toLocaleTimeString('id-ID');
        }
        setInterval(updateTime, 1000);
        updateTime();

        // ==================== TAB SWITCHING ====================
        function showTab(tabName) {
            document.querySelectorAll('.tab-content').forEach(tab => tab.classList.add('hidden'));
            document.getElementById(tabName + '-tab').classList.remove('hidden');
            
            document.querySelectorAll('.tab-button').forEach(btn => btn.classList.remove('active'));
            document.getElementById('tab-' + tabName).classList.add('active');
            
            if (tabName === 'news')    { currentPage = 1; loadNews(1); }
            if (tabName === 'users')   loadUserData();
            if (tabName === 'pending') loadPendingData();
            if (tabName === 'kicked')  loadKickedData();
            if (tabName === 'feeds')   loadFeedData();
            if (tabName === 'system')  loadSystemData();
        }

        // ==================== LOAD ALL DATA ====================
        async function loadAllData() {
            try {
                const res = await fetch('/api/stats');
                const data = await res.json();
                
                document.getElementById('totalNews').innerHTML = data.news_stats.total_news || 0;
                document.getElementById('todayNews').innerHTML = data.news_stats.today_news || 0;
                document.getElementById('sentNews').innerHTML = data.news_stats.sent_news || 0;
                document.getElementById('todaySent').innerHTML = data.news_stats.today_sent || 0;
                document.getElementById('totalUsers').innerHTML = data.user_stats.total_accounts || 0;
                document.getElementById('activeUsers').innerHTML = data.user_stats.active_users || 0;
                document.getElementById('totalFeeds').innerHTML = data.feed_stats.total || 0;
                document.getElementById('activeFeeds').innerHTML = data.feed_stats.active || 0;
                
                document.getElementById('activeUsersCount').innerHTML = data.user_stats.active_users || 0;
                document.getElementById('pendingUsersCount').innerHTML = data.user_stats.pending_users || 0;
                document.getElementById('bannedUsersCount').innerHTML = data.user_stats.banned_users || 0;
                document.getElementById('kickedUsersCount').innerHTML = data.user_stats.kicked_users || 0;
                
                aiAvailable = data.ai_available;
                const aiStatus = document.getElementById('aiStatus');
                if (aiAvailable) {
                    aiStatus.innerHTML = '✓ Aktif';
                    aiStatus.className = 'px-3 py-1 rounded-full bg-green-500/20 text-green-400';
                } else {
                    aiStatus.innerHTML = '✗ Tidak Aktif';
                    aiStatus.className = 'px-3 py-1 rounded-full bg-red-500/20 text-red-400';
                }
                
                const statusBadge = document.getElementById('statusBadge');
                if (data.system_stats.bot_running) {
                    statusBadge.innerHTML = '<i class="fas fa-check-circle mr-2 text-green-400"></i>System Online';
                    statusBadge.className = 'px-4 py-2 rounded-lg bg-green-600/20 text-green-400';
                } else {
                    statusBadge.innerHTML = '<i class="fas fa-exclamation-circle mr-2 text-red-400"></i>System Offline';
                    statusBadge.className = 'px-4 py-2 rounded-lg bg-red-600/20 text-red-400';
                }
                
                // Tampilkan kategori stats
                let categoryHtml = '';
                const categories = data.news_stats.categories || [];
                
                // Mapping nama kategori
                const categoryNames = {
                    'technology': '💻 Teknologi', 'science': '🔬 Sains', 
                    'entertainment': '🎬 Entertainment', 'general': '📰 General',
                    'business': '📈 Bisnis', 'health': '🏥 Health',
                    'sports': '⚽ Sport', 'militer': '🎖️ Militer', 
                    'politik': '🏛️ Politik', 'crypto': '₿ Crypto'
                };
                
                const categoryColors = {
                    'technology': 'bg-blue-500', 'science': 'bg-purple-500',
                    'entertainment': 'bg-pink-500', 'general': 'bg-gray-500',
                    'business': 'bg-green-500', 'health': 'bg-red-500',
                    'sports': 'bg-orange-500', 'militer': 'bg-green-600',
                    'politik': 'bg-red-600', 'crypto': 'bg-yellow-500'
                };
                
                categories.forEach(cat => {
                    const displayName = categoryNames[cat.category] || cat.category;
                    const bgColor = categoryColors[cat.category] || 'bg-blue-500';
                    
                    const percent = data.news_stats.total_news > 0 ? (cat.count / data.news_stats.total_news * 100).toFixed(1) : 0;
                    
                    categoryHtml += `
                        <div class="bg-gray-800/50 rounded-lg p-3">
                            <div class="flex justify-between mb-1">
                                <span class="text-sm">${displayName}</span>
                                <span class="text-blue-400 font-bold">${cat.count}</span>
                            </div>
                            <div class="w-full bg-gray-700 rounded-full h-1.5">
                                <div class="${bgColor} h-1.5 rounded-full" style="width: ${percent}%"></div>
                            </div>
                        </div>
                    `;
                });
                
                document.getElementById('categoryStats').innerHTML = categoryHtml || '<p class="text-gray-400 col-span-4 text-center">Belum ada data kategori</p>';
                
                document.getElementById('lastUpdate').innerHTML = 'Last update: ' + new Date().toLocaleString('id-ID');
                
                // Revenue estimate
                const revenue = data.user_stats.revenue_estimate || 0;
                document.getElementById('revenueEstimate').innerHTML = 'Rp ' + revenue.toLocaleString('id-ID');
                
                // Plan breakdown
                const planCounts = data.user_stats.plan_counts || {};
                const planInfo = {
                    'basic':    {label: '🥉 Basic',           harga: 'Rp 35.000',  color: 'text-gray-300'},
                    'standard': {label: '🥈 Standard',        harga: 'Rp 45.000',  color: 'text-blue-300'},
                    'premium':  {label: '🥇 Premium',         harga: 'Rp 65.000', color: 'text-yellow-300'},
                    'yearly':   {label: '👑 Premium Tahunan', harga: 'Rp 550.000', color: 'text-purple-300'},
                    'free':     {label: '🆓 Free',     harga: '-',           color: 'text-gray-500'},
                };
                let planHtml = '';
                for (const [plan, info] of Object.entries(planInfo)) {
                    const count = planCounts[plan] || 0;
                    if (count > 0 || plan !== 'free') {
                        planHtml += `<div class="flex justify-between text-sm">
                            <span class="${info.color}">${info.label}</span>
                            <span><strong>${count}</strong> user <span class="text-gray-500">(${info.harga})</span></span>
                        </div>`;
                    }
                }
                document.getElementById('planBreakdown').innerHTML = planHtml || '<p class="text-gray-400 text-sm">Belum ada data plan</p>';
                
                // Expiring soon
                const expiring = data.user_stats.expiring_soon || [];
                let expiringHtml = '';
                if (expiring.length === 0) {
                    expiringHtml = '<p class="text-gray-400 text-sm">Tidak ada yang akan expired</p>';
                } else {
                    expiring.forEach(u => {
                        const urgency = u.days_left <= 1 ? 'text-red-400' : u.days_left <= 3 ? 'text-orange-400' : 'text-yellow-400';
                        expiringHtml += `<div class="flex justify-between text-sm bg-gray-800/50 rounded p-2">
                            <span>${u.name}</span>
                            <span class="${urgency}">${u.days_left === 0 ? 'Hari ini!' : u.days_left + ' hari lagi'}</span>
                        </div>`;
                    });
                }
                document.getElementById('expiringSoonList').innerHTML = expiringHtml;
                
            } catch (error) {
                console.error('Error loading data:', error);
            }
        }

        // ==================== NEWS FUNCTIONS ====================
        async function loadNews(page = 1) {
            if (isLoading) return;
            
            isLoading = true;
            document.getElementById('newsLoading').classList.remove('hidden');
            
            const params = new URLSearchParams({
                page: page,
                limit: 10,
                category: currentFilters.category,
                search: currentFilters.search,
                start_date: currentFilters.startDate,
                end_date: currentFilters.endDate
            });
            
            try {
                const res = await fetch(`/api/news?${params}`);
                const data = await res.json();
                
                if (data.error) {
                    console.error('Error:', data.error);
                    return;
                }
                
                if (page === 1) {
                    document.getElementById('newsGrid').innerHTML = '';
                }
                
                appendNews(data.news);
                
                totalPages = data.pages;
                document.getElementById('newsTotalCount').innerHTML = data.total;
                document.getElementById('currentPage').innerHTML = page;
                
                // Tampilkan tombol Load More jika masih ada halaman
                const loadMoreBtn = document.getElementById('loadMoreBtn');
                if (page < totalPages) {
                    loadMoreBtn.classList.remove('hidden');
                } else {
                    loadMoreBtn.classList.add('hidden');
                }
                
                currentPage = page;
                
            } catch (error) {
                console.error('Error loading news:', error);
            }
            
            isLoading = false;
            document.getElementById('newsLoading').classList.add('hidden');
        }

        function appendNews(newsArray) {
            const grid = document.getElementById('newsGrid');
            
            const categoryNames = {
                'technology': '💻 Teknologi', 'science': '🔬 Sains',
                'entertainment': '🎬 Entertainment', 'general': '📰 General',
                'business': '📈 Bisnis', 'health': '🏥 Health',
                'sports': '⚽ Sport', 'militer': '🎖️ Militer',
                'politik': '🏛️ Politik', 'crypto': '₿ Crypto'
            };
            
            const categoryColors = {
                'technology': 'blue', 'science': 'purple',
                'entertainment': 'pink', 'general': 'gray',
                'business': 'green', 'health': 'red',
                'sports': 'orange', 'militer': 'green',
                'politik': 'red', 'crypto': 'yellow'
            };
            
            newsArray.forEach(news => {
                const card = document.createElement('a');
                card.href = news.link;
                card.target = '_blank';
                card.className = 'glass-card p-4 news-card block';
                
                const categoryColor = categoryColors[news.category] || 'blue';
                
                const date = new Date(news.published);
                const formattedDate = date.toLocaleDateString('id-ID', {
                    day: '2-digit',
                    month: 'short',
                    year: 'numeric',
                    hour: '2-digit',
                    minute: '2-digit'
                });
                
                card.innerHTML = `
                    <div class="flex justify-between items-start mb-2">
                        <span class="px-2 py-1 bg-${categoryColor}-500/20 text-${categoryColor}-400 rounded-full text-xs">
                            ${categoryNames[news.category] || news.category || '📰 General'}
                        </span>
                        <span class="text-xs text-gray-400">
                            <i class="far fa-clock mr-1"></i>${formattedDate}
                        </span>
                    </div>
                    <h3 class="font-semibold mb-2 line-clamp-2 hover:text-blue-400">${news.title}</h3>
                    ${news.summary ? `<p class="text-sm text-gray-400 mb-2 line-clamp-2">${news.summary}</p>` : ''}
                    <div class="flex justify-between items-center text-xs">
                        <span class="text-gray-500">
                            <i class="fas fa-chart-line mr-1"></i>${(news.pop_score || news.sent_count || 0).toFixed(1)} skor
                        </span>
                        <span class="text-blue-400">Baca <i class="fas fa-arrow-right ml-1"></i></span>
                    </div>
                `;
                
                grid.appendChild(card);
            });
        }

        function loadMore() {
            if (currentPage < totalPages) {
                loadNews(currentPage + 1);
            }
        }

        function applyFilters() {
            currentFilters = {
                category: document.getElementById('categoryFilter').value,
                search: document.getElementById('searchNews').value,
                startDate: document.getElementById('startDateFilter').value,
                endDate: document.getElementById('endDateFilter').value
            };
            loadNews(1);
        }

        function resetFilters() {
            document.getElementById('categoryFilter').value = 'all';
            document.getElementById('searchNews').value = '';
            document.getElementById('startDateFilter').value = '';
            document.getElementById('endDateFilter').value = '';
            
            currentFilters = {
                category: 'all',
                search: '',
                startDate: '',
                endDate: ''
            };
            loadNews(1);
        }

        function refreshNews() {
            loadNews(1);
        }

        async function exportNewsCSV() {
            // Baca LANGSUNG dari input — tidak perlu klik "Terapkan Filter" dulu
            const category   = document.getElementById('categoryFilter').value || 'all';
            const search     = document.getElementById('searchNews').value || '';
            const start_date = document.getElementById('startDateFilter').value || '';
            const end_date   = document.getElementById('endDateFilter').value || '';

            // Validasi: kalau tidak ada filter tanggal sama sekali, tanya dulu
            if (!start_date && !end_date) {
                const lanjut = confirm('⚠️ Tidak ada filter tanggal yang dipilih.\nApakah yakin ingin export SEMUA berita?\n\nKalau mau per tanggal, isi dulu kolom "Dari Tanggal" dan "Sampai Tanggal" lalu klik Export CSV lagi.');
                if (!lanjut) return;
            }

            const params = new URLSearchParams({ category, search, start_date, end_date });

            // Cek dulu berapa berita yang akan diexport
            try {
                const countRes  = await fetch(`/api/news?page=1&limit=1&category=${category}&search=${encodeURIComponent(search)}&start_date=${start_date}&end_date=${end_date}`);
                const countData = await countRes.json();
                const total     = countData.total || 0;

                if (total === 0) {
                    alert('⚠️ Tidak ada berita yang cocok dengan filter tersebut.');
                    return;
                }

                const filterInfo = [];
                if (category && category !== 'all') filterInfo.push(`Kategori: ${category}`);
                if (start_date) filterInfo.push(`Dari: ${start_date}`);
                if (end_date)   filterInfo.push(`Sampai: ${end_date}`);
                if (search)     filterInfo.push(`Kata kunci: "${search}"`);
                const filterText = filterInfo.length ? '\n🔍 Filter: ' + filterInfo.join(' | ') : '\n🔍 Filter: Semua berita (tanpa filter tanggal)';

                if (!confirm(`📥 Export CSV\n✅ Total: ${total} berita akan diexport${filterText}\n\nLanjutkan?`)) return;

            } catch(e) {
                // kalau gagal cek, tetap lanjut download
            }

            window.open(`/api/news/export/csv?${params}`, '_blank');
        }

        // ==================== PDF PER KATEGORI ====================
        async function exportNewsPDF() {
            // Isi periode default (hari ini & kemarin)
            const today = new Date();
            const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1);
            const fmt = d => d.toLocaleDateString('id-ID', {day:'2-digit', month:'short', year:'numeric'});
            const periodEl = document.getElementById('pdfDefaultPeriod');
            if (periodEl) periodEl.textContent = fmt(yesterday) + ' s/d ' + fmt(today);

            // Buka modal, load kategori dari DB
            document.getElementById('pdfCatModal').classList.add('active');
            document.getElementById('pdfCatLoading').classList.remove('hidden');
            document.getElementById('pdfCatList').classList.add('hidden');
            document.getElementById('pdfCatFilterInfo').classList.add('hidden');

            try {
                const res  = await fetch('/api/news/categories');
                const cats = await res.json();

                const listEl = document.getElementById('pdfCatList');
                if (!cats.length) {
                    listEl.innerHTML = '<p class="text-gray-400 text-sm text-center py-4">Belum ada berita di database.</p>';
                } else {
                    listEl.innerHTML = cats.map(c => `
                        <button onclick="doExportPDF('${c.key}')"
                            class="w-full flex justify-between items-center px-4 py-3 bg-gray-800 hover:bg-gray-700 rounded-lg text-left transition">
                            <span class="font-medium">${c.label}</span>
                            <span class="text-xs text-gray-400 bg-gray-700 px-2 py-1 rounded-full">${c.count} berita</span>
                        </button>
                    `).join('');
                }

                // Tampilkan info filter aktif jika ada
                const hasFilter = currentFilters.search || currentFilters.startDate || currentFilters.endDate;
                if (hasFilter) {
                    let info = 'Filter aktif akan diterapkan: ';
                    const parts = [];
                    if (currentFilters.search) parts.push(`Pencarian "${currentFilters.search}"`);
                    if (currentFilters.startDate) parts.push(`Dari ${currentFilters.startDate}`);
                    if (currentFilters.endDate) parts.push(`Sampai ${currentFilters.endDate}`);
                    document.getElementById('pdfCatFilterText').textContent = info + parts.join(', ');
                    document.getElementById('pdfCatFilterInfo').classList.remove('hidden');
                }

                document.getElementById('pdfCatLoading').classList.add('hidden');
                document.getElementById('pdfCatList').classList.remove('hidden');

            } catch(e) {
                document.getElementById('pdfCatLoading').innerHTML =
                    '<p class="text-red-400 text-sm text-center">Gagal memuat kategori: ' + e.message + '</p>';
            }
        }

        function closePdfCatModal() {
            document.getElementById('pdfCatModal').classList.remove('active');
        }

        function doExportPDF(category) {
            const params = new URLSearchParams({
                category: category,
                search: currentFilters.search || '',
                start_date: currentFilters.startDate || '',
                end_date: currentFilters.endDate || ''
            });
            window.open(`/api/news/export/pdf?${params}`, '_blank');
            closePdfCatModal();
        }

        // ==================== SENTIMENT ANALYSIS FUNCTIONS ====================
        async function showSentimentAnalysis() {
            document.getElementById('sentimentModal').classList.add('active');
            document.getElementById('sentimentLoading').classList.remove('hidden');
            document.getElementById('sentimentResult').classList.add('hidden');
            document.getElementById('sentimentError').classList.add('hidden');
            
            if (!aiAvailable) {
                document.getElementById('sentimentLoading').classList.add('hidden');
                document.getElementById('sentimentError').classList.remove('hidden');
                document.getElementById('sentimentError').innerHTML = `
                    <i class="fas fa-exclamation-triangle text-2xl mb-2"></i>
                    <p>Sentiment analysis tidak tersedia</p>
                    <p class="text-sm mt-2">File sentiment_config.yaml tidak ditemukan atau kosong</p>
                `;
                return;
            }
            
            try {
                const res = await fetch('/api/sentiment/analyze?days=7&limit=100');
                const data = await res.json();
                
                document.getElementById('sentimentLoading').classList.add('hidden');
                
                if (data.error) {
                    document.getElementById('sentimentError').classList.remove('hidden');
                    document.getElementById('sentimentError').innerHTML = `
                        <i class="fas fa-exclamation-circle text-2xl mb-2"></i>
                        <p>${data.error}</p>
                    `;
                    return;
                }
                
                if (data.total_analyzed === 0) {
                    document.getElementById('sentimentError').classList.remove('hidden');
                    document.getElementById('sentimentError').innerHTML = `
                        <i class="fas fa-info-circle text-2xl mb-2"></i>
                        <p>Tidak ada berita untuk dianalisis dalam 7 hari terakhir</p>
                    `;
                    return;
                }
                
                // Tampilkan ringkasan
                document.getElementById('sentimentPositif').innerHTML = data.summary.positive || 0;
                document.getElementById('sentimentNegatif').innerHTML = data.summary.negative || 0;
                document.getElementById('sentimentNetral').innerHTML = data.summary.neutral || 0;
                document.getElementById('sentimentTotal').innerHTML = data.total_analyzed;
                document.getElementById('lexiconSize').innerHTML = data.lexicon_size;
                
                // Progress bar
                const positifPercent = data.percentage.positive || 0;
                const negatifPercent = data.percentage.negative || 0;
                const netralPercent = data.percentage.neutral || 0;
                
                document.getElementById('positifBar').style.width = positifPercent + '%';
                document.getElementById('negatifBar').style.width = negatifPercent + '%';
                document.getElementById('netralBar').style.width = netralPercent + '%';
                
                // Detail hasil
                let detailsHtml = '';
                data.details.forEach(item => {
                    let color = item.sentiment === 'positive' ? 'green' : 
                               item.sentiment === 'negative' ? 'red' : 'gray';
                    
                    // Ambil kata-kata yang terdeteksi
                    let wordsHtml = '';
                    if (item.words && item.words.length > 0) {
                        wordsHtml = item.words.map(w => 
                            `<span class="text-xs bg-${color}-500/20 px-1 py-0.5 rounded">${w.word} (${w.score})</span>`
                        ).join(' ');
                    } else {
                        wordsHtml = '<span class="text-xs text-gray-500">Tidak ada kata terdeteksi</span>';
                    }
                    
                    detailsHtml += `
                        <div class="p-3 bg-gray-800/50 rounded">
                            <div class="flex justify-between items-start mb-1">
                                <div class="text-sm font-medium">${item.title}</div>
                                <span class="px-2 py-0.5 bg-${color}-500/20 text-${color}-400 rounded-full text-xs ml-2 whitespace-nowrap">
                                    ${item.sentiment} (${item.score})
                                </span>
                            </div>
                            <div class="flex flex-wrap gap-1 mt-1">
                                ${wordsHtml}
                            </div>
                        </div>
                    `;
                });
                
                document.getElementById('sentimentDetails').innerHTML = detailsHtml;
                document.getElementById('sentimentResult').classList.remove('hidden');
                
            } catch (error) {
                document.getElementById('sentimentLoading').classList.add('hidden');
                document.getElementById('sentimentError').classList.remove('hidden');
                document.getElementById('sentimentError').innerHTML = `
                    <i class="fas fa-exclamation-circle text-2xl mb-2"></i>
                    <p>Gagal menganalisis: ${error.message}</p>
                `;
            }
        }

        function closeSentimentModal() {
            document.getElementById('sentimentModal').classList.remove('active');
        }

        // ==================== USER FUNCTIONS ====================
        async function loadUserData() {
            try {
                const res = await fetch('/api/users');
                const data = await res.json();
                
                const planEmoji = {'basic':'🥉','standard':'🥈','premium':'🥇','yearly':'💎','free':'🆓'};
                
                let html = '';
                data.user_details.forEach((user, index) => {
                    let statusBadge = '';
                    if (user.status === 'admin') {
                        statusBadge = '<span class="px-2 py-1 bg-purple-500/20 text-purple-400 rounded-full text-xs">👑 Admin</span>';
                    } else if (user.status === 'active') {
                        statusBadge = '<span class="px-2 py-1 bg-green-500/20 text-green-400 rounded-full text-xs">✅ Aktif</span>';
                    } else if (user.status === 'banned') {
                        statusBadge = '<span class="px-2 py-1 bg-red-500/20 text-red-400 rounded-full text-xs">🚫 Banned</span>';
                    } else {
                        statusBadge = '<span class="px-2 py-1 bg-gray-500/20 text-gray-400 rounded-full text-xs">❌ Disabled</span>';
                    }
                    
                    const lastSent = user.last_sent ? new Date(user.last_sent).toLocaleString('id-ID') : 'Never';
                    const plan = user.plan || 'free';
                    const planLabel = user.plan_label || ((planEmoji[plan] || '🆓') + ' ' + plan.charAt(0).toUpperCase() + plan.slice(1));
                    
                    let expiryDisplay = user.status === 'admin' ? '<span class="text-purple-400">∞ Selamanya</span>' : '-';
                    if (user.expiry && user.status !== 'admin') {
                        const expDate = new Date(user.expiry);
                        const daysLeft = Math.ceil((expDate - new Date()) / 86400000);
                        const color = daysLeft <= 3 ? 'text-red-400' : daysLeft <= 7 ? 'text-yellow-400' : 'text-gray-300';
                        expiryDisplay = `<span class="${color}">${expDate.toLocaleDateString('id-ID')} (${daysLeft}h)</span>`;
                    }

                    // Render kategori sebagai badge kecil
                    const catLabels = user.category_labels || [];
                    const catHtml = catLabels.length
                        ? catLabels.map(c => `<span class="inline-block bg-gray-700 text-gray-300 text-xs px-1 py-0.5 rounded mr-0.5 mb-0.5">${c}</span>`).join('')
                        : '<span class="text-gray-500 text-xs">-</span>';
                    
                    html += `
                        <tr class="border-b border-gray-700 hover:bg-gray-800/50">
                            <td class="py-3 px-4">${index + 1}</td>
                            <td class="py-3 px-4">${user.name}</td>
                            <td class="py-3 px-4">${user.chat_id}</td>
                            <td class="py-3 px-4">${statusBadge}</td>
                            <td class="py-3 px-4">${planLabel}</td>
                            <td class="py-3 px-4">${expiryDisplay}</td>
                            <td class="py-3 px-4">${user.sent_count}</td>
                            <td class="py-3 px-4 max-w-xs"><div class="flex flex-wrap">${catHtml}</div></td>
                            <td class="py-3 px-4">${lastSent}</td>
                        </tr>
                    `;
                });
                
                document.getElementById('userTableBody').innerHTML = html || '<tr><td colspan="9" class="text-center py-4 text-gray-400">Tidak ada user</td></tr>';
            } catch (error) {
                console.error('Error loading user data:', error);
            }
        }

        // ==================== PENDING FUNCTIONS ====================
        async function loadPendingData() {
            try {
                const res = await fetch('/api/pending');
                const data = await res.json();
                
                // data bisa berupa object {chat_id: {...}} (v3.0 payment_pending.json)
                let entries = [];
                if (Array.isArray(data)) {
                    entries = data;
                } else if (typeof data === 'object') {
                    entries = Object.values(data);
                }
                
                const planEmoji = {'basic':'🥉','standard':'🥈','premium':'🥇','yearly':'💎'};
                
                let html = '';
                entries.forEach((user, index) => {
                    const plan = user.plan || user.selected_plan || '-';
                    const planLabel = plan !== '-' ? (planEmoji[plan] || '') + ' ' + plan : '-';
                    const metode = user.payment_method || user.metode || '-';
                    const waktu = user.submitted_at || user.requested_at || user.timestamp || 'Unknown';
                    html += `
                        <tr class="border-b border-gray-700 hover:bg-gray-800/50">
                            <td class="py-3 px-4">${index + 1}</td>
                            <td class="py-3 px-4">${user.name || 'Unknown'}</td>
                            <td class="py-3 px-4">@${user.username || 'None'}</td>
                            <td class="py-3 px-4">${user.chat_id || user.user_id || '-'}</td>
                            <td class="py-3 px-4">${planLabel}</td>
                            <td class="py-3 px-4">${metode}</td>
                            <td class="py-3 px-4">${waktu}</td>
                        </tr>
                    `;
                });
                
                document.getElementById('pendingTableBody').innerHTML = html || '<tr><td colspan="7" class="text-center py-4 text-gray-400">Tidak ada pending pembayaran</td></tr>';
            } catch (error) {
                console.error('Error loading pending data:', error);
            }
        }

        // ==================== KICKED FUNCTIONS ====================
        async function loadKickedData() {
            try {
                const res = await fetch('/api/kicked');
                const data = await res.json();
                
                let html = '';
                data.forEach((user, index) => {
                    html += `
                        <tr class="border-b border-gray-700 hover:bg-gray-800/50">
                            <td class="py-3 px-4">${index + 1}</td>
                            <td class="py-3 px-4">${user.name || 'Unknown'}</td>
                            <td class="py-3 px-4">${user.chat_id}</td>
                            <td class="py-3 px-4">${user.kicked_at || 'Unknown'}</td>
                            <td class="py-3 px-4">${user.reason || 'No reason'}</td>
                        </tr>
                    `;
                });
                
                document.getElementById('kickedTableBody').innerHTML = html || '<tr><td colspan="5" class="text-center py-4 text-gray-400">Belum ada riwayat kick</td></tr>';
            } catch (error) {
                console.error('Error loading kicked data:', error);
            }
        }

        // ==================== FEED FUNCTIONS ====================
        async function loadFeedData() {
            try {
                const res = await fetch('/api/feeds');
                const data = await res.json();
                
                let feedHtml = '';
                data.feeds.forEach((feed, index) => {
                    feedHtml += `
                        <div class="flex justify-between items-center p-2 hover:bg-gray-800/50 rounded">
                            <div class="flex-1">
                                <span class="font-medium">${feed.title}</span>
                                <span class="text-xs text-gray-400 ml-2">[${feed.category}]</span>
                            </div>
                            <a href="${feed.url}" target="_blank" class="text-blue-400 text-sm ml-2">
                                <i class="fas fa-external-link-alt"></i>
                            </a>
                        </div>
                    `;
                });
                document.getElementById('feedList').innerHTML = feedHtml || '<p class="text-gray-400">Tidak ada feed</p>';
                
                let failedHtml = '';
                data.failed_list.forEach(log => {
                    failedHtml += `<div class="text-sm py-1 border-b border-red-900/30">${log}</div>`;
                });
                document.getElementById('failedFeedsList').innerHTML = failedHtml || '<p class="text-gray-400">Tidak ada failed feeds</p>';
                
            } catch (error) {
                console.error('Error loading feed data:', error);
            }
        }

        // ==================== SYSTEM FUNCTIONS ====================
        async function loadSystemData() {
            try {
                const res = await fetch('/api/stats');
                const data = await res.json();
                
                document.getElementById('botStatus').innerHTML = data.system_stats.bot_running ? 
                    '<span class="text-green-400">● Running</span>' : 
                    '<span class="text-red-400">○ Stopped</span>';
                document.getElementById('registerStatus').innerHTML = data.system_stats.register_bot_running ? 
                    '<span class="text-green-400">● Running</span>' : 
                    '<span class="text-red-400">○ Stopped</span>';
                
                let fileHtml = '';
                for (const [file, size] of Object.entries(data.system_stats.file_sizes || {})) {
                    fileHtml += `<div class="flex justify-between"><span>${file}</span><span>${size} KB</span></div>`;
                }
                document.getElementById('fileSizes').innerHTML = fileHtml;
                
                if (data.system_stats.last_backup) {
                    document.getElementById('backupInfo').innerHTML = `
                        <div>Last backup: ${data.system_stats.last_backup}</div>
                        <div>Total backups: ${data.system_stats.backup_count || 0}</div>
                    `;
                } else {
                    document.getElementById('backupInfo').innerHTML = '<p class="text-gray-400">Belum ada backup</p>';
                }
                
            } catch (error) {
                console.error('Error loading system data:', error);
            }
        }

        // ==================== EXPORT FUNCTIONS ====================
        function exportUsers() {
            window.open('/api/export/users', '_blank');
        }

        function exportNews() {
            window.open('/api/export/news', '_blank');
        }

        // ==================== BROADCAST FUNCTIONS ====================
        function showBroadcastModal() {
            document.getElementById('broadcastModal').classList.add('active');
        }

        function closeBroadcastModal() {
            document.getElementById('broadcastModal').classList.remove('active');
        }

        async function sendBroadcast() {
            const message = document.getElementById('broadcastMessage').value;
            if (!message) return;
            
            try {
                const res = await fetch('/api/broadcast', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({message: message})
                });
                
                const data = await res.json();
                alert(`Broadcast terkirim ke ${data.sent} user`);
                closeBroadcastModal();
                document.getElementById('broadcastMessage').value = '';
                
            } catch (error) {
                alert('Gagal mengirim broadcast');
            }
        }

        // ==================== EVENT LISTENERS ====================
        document.getElementById('searchNews')?.addEventListener('keyup', function(e) {
            if (e.key === 'Enter') {
                applyFilters();
            }
        });


        // ==================== DELETE NEWS FUNCTIONS ====================
        let deleteCurrentPage = 1;

        function showDeleteNewsModal() {
            document.getElementById('deleteNewsModal').classList.add('active');
            setDeleteMode('all');
        }

        function closeDeleteNewsModal() {
            document.getElementById('deleteNewsModal').classList.remove('active');
            document.getElementById('deleteStatus').classList.add('hidden');
        }

        function setDeleteMode(mode) {
            ['all','date','item'].forEach(m => {
                document.getElementById('dpanel-' + m).style.display = 'none';
                const btn = document.getElementById('dmode-' + m);
                btn.classList.remove('bg-red-700','border-red-400','bg-blue-700','border-blue-400');
                btn.classList.add('bg-gray-700','border-transparent');
            });
            document.getElementById('dpanel-' + mode).style.display = 'flex';
            document.getElementById('dpanel-' + mode).style.flexDirection = 'column';
            const activeBtn = document.getElementById('dmode-' + mode);
            activeBtn.classList.remove('bg-gray-700','border-transparent');
            activeBtn.classList.add(mode==='all'?'bg-red-700':'bg-blue-700', mode==='all'?'border-red-400':'border-blue-400');
            document.getElementById('deleteStatus').classList.add('hidden');
            if (mode === 'date') loadDeleteDateList();
            if (mode === 'item') loadDeleteItemList(1);
        }

        async function loadDeleteDateList() {
            const el = document.getElementById('dateList');
            el.innerHTML = '<p class="text-gray-400 text-sm">Memuat...</p>';
            try {
                const res  = await fetch('/api/news/dates');
                const rows = await res.json();
                if (!rows.length) { el.innerHTML = '<p class="text-gray-400 text-sm">Tidak ada data</p>'; return; }
                el.innerHTML = rows.map(r => `
                    <div class="flex justify-between items-center p-3 bg-gray-800/50 rounded-lg">
                        <div>
                            <span class="font-medium">📅 ${r.tgl}</span>
                            <span class="text-gray-400 text-xs ml-2">${r.cnt} berita</span>
                        </div>
                        <button onclick="confirmDeleteDate(\'${r.tgl}\', ${r.cnt})"
                                class="px-3 py-1 bg-red-600 hover:bg-red-500 rounded text-sm">
                            <i class="fas fa-trash-alt mr-1"></i>Hapus
                        </button>
                    </div>
                `).join('');
            } catch(e) {
                el.innerHTML = '<p class="text-red-400 text-sm">Error: ' + e.message + '</p>';
            }
        }

        async function loadDeleteItemList(page) {
            deleteCurrentPage = page || 1;
            const search = document.getElementById('deleteSearchInput')?.value || '';
            const cat    = document.getElementById('deleteCatFilter')?.value || 'all';
            const el     = document.getElementById('itemList');
            const pgEl   = document.getElementById('itemPagination');
            el.innerHTML = '<p class="text-gray-400 text-sm">Memuat...</p>';
            try {
                const params = new URLSearchParams({ page: deleteCurrentPage, limit: 8, search, category: cat });
                const res    = await fetch('/api/news/list?' + params);
                const data   = await res.json();
                if (!data.items || !data.items.length) {
                    el.innerHTML = '<p class="text-gray-400 text-sm">Tidak ada berita</p>';
                    pgEl.innerHTML = '';
                    return;
                }
                const catEmoji = {technology:'💻',business:'📈',sports:'⚽',entertainment:'🎬',science:'🔬',health:'🏥',politik:'🏛️',militer:'🎖️',general:'📰',crypto:'₿'};
                el.innerHTML = data.items.map(item => `
                    <div class="flex justify-between items-center p-2 bg-gray-800/50 rounded gap-2">
                        <div class="flex-1 min-w-0">
                            <p class="text-sm truncate">${catEmoji[item.category]||'📰'} ${item.title}</p>
                            <p class="text-xs text-gray-400">${item.tgl || ''}</p>
                        </div>
                        <button onclick="confirmDeleteItem(\'${item.hash_id}\', this.closest(\'div\').querySelector(\'p\').textContent)"
                                class="flex-shrink-0 px-2 py-1 bg-red-600 hover:bg-red-500 rounded text-xs">
                            <i class="fas fa-trash-alt"></i>
                        </button>
                    </div>
                `).join('');
                const totalPages = data.pages || 1;
                pgEl.innerHTML = `
                    <span>Hal ${deleteCurrentPage}/${totalPages} · ${data.total} berita</span>
                    <div class="flex gap-2">
                        ${deleteCurrentPage > 1 ? `<button onclick="loadDeleteItemList(${deleteCurrentPage-1})" class="px-2 py-1 bg-gray-700 rounded text-xs">◀ Prev</button>` : ''}
                        ${deleteCurrentPage < totalPages ? `<button onclick="loadDeleteItemList(${deleteCurrentPage+1})" class="px-2 py-1 bg-gray-700 rounded text-xs">Next ▶</button>` : ''}
                    </div>
                `;
            } catch(e) {
                el.innerHTML = '<p class="text-red-400 text-sm">Error: ' + e.message + '</p>';
            }
        }

        function showDeleteStatus(msg, ok) {
            const el = document.getElementById('deleteStatus');
            el.className = 'mt-3 text-sm p-3 rounded-lg ' + (ok ? 'bg-green-900/40 text-green-300' : 'bg-red-900/40 text-red-300');
            el.innerHTML = msg;
            el.classList.remove('hidden');
        }

        async function confirmDeleteAll() {
            if (!confirm('YAKIN hapus SEMUA berita? Tindakan ini tidak bisa dibatalkan!')) return;
            try {
                const res  = await fetch('/api/news/delete/all', { method: 'DELETE' });
                const data = await res.json();
                if (data.success) {
                    showDeleteStatus('✅ Semua berita berhasil dihapus!', true);
                    loadAllData(); loadNews(1);
                } else {
                    showDeleteStatus('❌ Gagal: ' + data.error, false);
                }
            } catch(e) { showDeleteStatus('❌ Error: ' + e.message, false); }
        }

        async function confirmDeleteDate(tgl, cnt) {
            if (!confirm(`Hapus ${cnt} berita tanggal ${tgl}?`)) return;
            try {
                const res  = await fetch(`/api/news/delete/date/${tgl}`, { method: 'DELETE' });
                const data = await res.json();
                if (data.success) {
                    showDeleteStatus(`✅ ${data.deleted} berita tanggal ${tgl} dihapus!`, true);
                    loadDeleteDateList(); loadAllData(); loadNews(1);
                } else {
                    showDeleteStatus('❌ Gagal: ' + data.error, false);
                }
            } catch(e) { showDeleteStatus('❌ Error: ' + e.message, false); }
        }

        async function confirmDeleteItem(hashId, title) {
            const short = title.substring(0, 60) + (title.length > 60 ? '...' : '');
            if (!confirm(`Hapus berita:\n${short}?`)) return;
            try {
                const res  = await fetch(`/api/news/delete/item/${hashId}`, { method: 'DELETE' });
                const data = await res.json();
                if (data.success) {
                    showDeleteStatus('✅ Berita dihapus: ' + (data.title||'').substring(0,60), true);
                    loadDeleteItemList(deleteCurrentPage); loadAllData(); loadNews(1);
                } else {
                    showDeleteStatus('❌ Gagal: ' + data.error, false);
                }
            } catch(e) { showDeleteStatus('❌ Error: ' + e.message, false); }
        }

        // ==================== INITIALIZATION ====================
        loadAllData();
        loadNews(1);
        
        setInterval(loadAllData, 30000);
    </script>

    <!-- ==================== NOVA CHATBOT ==================== -->
    <div id="nova-chatbot">
        <!-- Chat Window -->
        <div id="nova-window">
            <div id="nova-header">
                <div class="nova-avatar">🤖</div>
                <div>
                    <div class="nova-title">NOVA Assistant</div>
                    <div class="nova-subtitle">Tanya apa saja tentang berita</div>
                </div>
                <button class="nova-close" onclick="novaToggle()" title="Tutup">✕</button>
            </div>

            <div id="nova-messages">
                <!-- Pesan selamat datang -->
                <div class="nova-msg bot">
                    👋 Halo! Saya <strong>NOVA</strong>, asisten berita pintar Anda.<br><br>
                    Saya bisa menjawab pertanyaan seperti:<br>
                    • "Apa berita teknologi terbaru?"<br>
                    • "Ringkaskan berita politik hari ini"<br>
                    • "Ada berapa berita bisnis?"<br><br>
                    Silakan tanya apa saja! 🚀
                </div>
            </div>

            <!-- Quick buttons -->
            <div id="nova-quick-btns">
                <button class="nova-quick" onclick="novaQuick('Berita terbaru hari ini apa saja?')">📰 Terbaru</button>
                <button class="nova-quick" onclick="novaQuick('Rangkuman berita teknologi')">💻 Teknologi</button>
                <button class="nova-quick" onclick="novaQuick('Berita politik terkini')">🏛️ Politik</button>
                <button class="nova-quick" onclick="novaQuick('Ada berapa total berita di database?')">📊 Statistik</button>
                <button class="nova-quick" onclick="novaQuick('Berita bisnis dan ekonomi terbaru')">📈 Bisnis</button>
            </div>

            <!-- Input -->
            <div id="nova-input-row">
                <input id="nova-input" type="text" placeholder="Tanya tentang berita..."
                    onkeydown="if(event.key==='Enter')novaSend()" maxlength="500" />
                <button id="nova-send" onclick="novaSend()">
                    <i class="fas fa-paper-plane"></i>
                </button>
            </div>
        </div>

        <!-- Toggle Button -->
        <div style="position:relative; display:inline-block;">
            <button id="nova-toggle" onclick="novaToggle()" title="Buka NOVA Assistant">
                <i class="fas fa-robot"></i>
            </button>
            <div id="nova-badge">1</div>
        </div>
    </div>

    <script>
        // ---- NOVA Chatbot Logic ----
        let novaOpen = false;
        let novaTyping = false;

        function novaToggle() {
            novaOpen = !novaOpen;
            const win = document.getElementById('nova-window');
            const btn = document.getElementById('nova-toggle');
            const badge = document.getElementById('nova-badge');
            if (novaOpen) {
                win.classList.add('open');
                btn.innerHTML = '<i class="fas fa-times"></i>';
                badge.style.display = 'none';
                document.getElementById('nova-input').focus();
            } else {
                win.classList.remove('open');
                btn.innerHTML = '<i class="fas fa-robot"></i>';
            }
        }

        // Tampilkan badge notif setelah 5 detik jika belum dibuka
        setTimeout(() => {
            if (!novaOpen) {
                const badge = document.getElementById('nova-badge');
                badge.style.display = 'flex';
            }
        }, 5000);

        function novaQuick(text) {
            document.getElementById('nova-input').value = text;
            novaSend();
        }

        function novaAppendMsg(text, role) {
            const el = document.createElement('div');
            el.className = 'nova-msg ' + role;
            // Render newlines & simple markdown bold
            el.innerHTML = text
                .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
                .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                .replace(/\*(.*?)\*/g, '<em>$1</em>')
                .replace(/\n/g, '<br>')
                .replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank">$1</a>');
            const msgs = document.getElementById('nova-messages');
            msgs.appendChild(el);
            msgs.scrollTop = msgs.scrollHeight;
            return el;
        }

        function novaShowTyping() {
            const el = document.createElement('div');
            el.className = 'nova-msg bot typing';
            el.id = 'nova-typing-indicator';
            el.innerHTML = '<span></span><span></span><span></span>';
            const msgs = document.getElementById('nova-messages');
            msgs.appendChild(el);
            msgs.scrollTop = msgs.scrollHeight;
        }

        function novaRemoveTyping() {
            const el = document.getElementById('nova-typing-indicator');
            if (el) el.remove();
        }

        async function novaSend() {
            const input = document.getElementById('nova-input');
            const sendBtn = document.getElementById('nova-send');
            const msg = input.value.trim();
            if (!msg || novaTyping) return;

            input.value = '';
            novaAppendMsg(msg, 'user');

            novaTyping = true;
            sendBtn.disabled = true;
            novaShowTyping();

            document.getElementById('nova-quick-btns').style.display = 'none';

            try {
                const res = await fetch('/api/chatbot', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message: msg })
                });
                const data = await res.json();
                novaRemoveTyping();
                if (data.reply) {
                    novaAppendMsg(data.reply, 'bot');
                    // Tampilkan info biaya
                    if (data.usage) {
                        const u = data.usage;
                        const costEl = document.createElement('div');
                        costEl.style.cssText = 'font-size:0.68rem;color:#6b7280;text-align:right;padding:0 4px 6px;';
                        costEl.innerHTML = `🔢 ${u.input_tokens}+${u.output_tokens} token &nbsp;|&nbsp; 💰 $${u.cost_usd.toFixed(5)} ≈ Rp${u.cost_idr.toFixed(0)}`;
                        document.getElementById('nova-messages').appendChild(costEl);
                        document.getElementById('nova-messages').scrollTop = 99999;
                    }
                } else {
                    novaAppendMsg('⚠️ ' + (data.error || 'Terjadi kesalahan. Coba lagi.'), 'bot');
                }
            } catch (e) {
                novaRemoveTyping();
                novaAppendMsg('❌ Gagal terhubung ke server. Periksa koneksi Anda.', 'bot');
            } finally {
                novaTyping = false;
                sendBtn.disabled = false;
                input.focus();
            }
        }
    </script>
</body>
</html>
'''

# ==================== RUN SERVER ====================

def find_free_port():
    """Cari port yang tersedia"""
    port = 5005
    while True:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('0.0.0.0', port))
                return port
        except OSError:
            port += 1

if __name__ == '__main__':
    port = find_free_port()
    
    print("="*70)
    print("🚀 NOVA NEWSBOT v3.0 - DASHBOARD")
    print("="*70)
    print(f"📊 Dashboard URL: http://localhost:{port}")
    print(f"📱 Akses dari HP: http://<ip-anda>:{port}")
    print("="*70)
    print("✅ FITUR YANG BERFUNGSI:")
    print("   • Filter kategori (termasuk Crypto) - berdasarkan database")
    print("   • Pencarian judul/konten - berdasarkan database")
    print("   • Filter tanggal - berdasarkan database")
    print("   • Pagination - 10 berita per halaman")
    print("   • Export CSV dengan filter aktif")
    print("   • Export PDF dengan filter aktif (memerlukan reportlab)")
    print("   • Statistik real-time dari database")
    print("   • ANALISIS SENTIMEN - menampilkan hasil dari kamus YAML")
    print("   • Revenue estimate & plan breakdown (v3.0)")
    print("   • Expiring soon notification (v3.0)")
    print("="*70)
    print(f"📚 Sentiment Lexicon: {len(SENTIMENT_LEXICON)} kata/frasa dari YAML")
    print("="*70)
    print("📁 File yang digunakan:")
    print("   • sentiment_config.yaml - Kamus sentimen")
    print("   • news_bot.db           - Database SQLite")
    print("   • D-NEWS.py             - Bot broadcaster utama")
    print("   • register.py           - Bot registrasi & pembayaran")
    print("   • subscriptions.json    - Data plan & expiry user")
    print("   • payment_pending.json  - Pending pembayaran")
    print("="*70)
    print("🚀 Menjalankan server...")
    print("="*70)
    
    app.run(host='0.0.0.0', port=port, debug=True, threaded=True)
