# 📰 NOVA NEWSBOT PRO - Automated Telegram News Broadcasting System

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![Telegram Bot API](https://img.shields.io/badge/Telegram-Bot_API-blue.svg)](https://core.telegram.org/bots/api)
[![Flask](https://img.shields.io/badge/Flask-2.0+-green.svg)](https://flask.palletsprojects.com/)
[![SQLite](https://img.shields.io/badge/Database-SQLite-orange.svg)](https://www.sqlite.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Sistem otomatis pengiriman berita ke Telegram dengan manajemen subscription multi-tier, integrasi pembayaran GoPay/OVO, dan dashboard admin lengkap.**

---

## ✨ **Fitur Unggulan**

### 📰 **Agregator Berita Cerdas**
- **10+ Kategori Berita:** Politik, Militer, Kripto, Teknologi, Bisnis, Olahraga, Hiburan, Sains, Kesehatan, Umum
- **Auto-classification** dengan keyword boosting & URL mapping (akurasi tinggi)
- **50+ sumber RSS feed** terpercaya dari berbagai region
- **Filter spam** dan deteksi konten duplikat otomatis
- **Auto-translate** ke Bahasa Indonesia (opsional)

### 💰 **Sistem Subscription Multi-Tier**
| Paket | Harga | Kategori | Kecepatan |
|-------|-------|----------|-----------|
| 🥉 **Basic** | Rp 35.000/bulan | 3 kategori | 10 berita/jam |
| 🥈 **Standard** | Rp 45.000/bulan | 7 kategori | 30 berita/jam |
| 🥇 **Premium** | Rp 65.000/bulan | 10 kategori | 100 berita/jam |
| 👑 **Tahunan** | Rp 550.000/tahun | 10 kategori | 100 berita/jam |

### 💳 **Payment Integration**
- **GoPay & OVO** (081236072208 a/n Gede Dylan Pratama Wijaya)
- **Auto-confirm system** dengan bukti transfer
- **Notifikasi admin** untuk pending payment real-time

### 📊 **Admin Dashboard (Flask Web)**
- **Statistik real-time** (user, berita, revenue, paket)
- **Filter berita lengkap** (kategori, tanggal, pencarian)
- **Export CSV/PDF** dengan terjemahan otomatis ke Bahasa Indonesia
- **Sentiment analysis** dengan lexicon YAML
- **Broadcast message** ke semua/individual member
- **Manajemen user** (kick, ban, enable, disable)
- **Delete news** (semua, per tanggal, per judul)

### 🤖 **AI Chatbot Assistant**
- Terintegrasi dengan **Claude API**
- Menjawab pertanyaan tentang berita & statistik
- **Auto-detect kategori dan tanggal** dari pertanyaan
- Estimasi biaya API per pertanyaan

---

## 🛠️ **Instalasi**

### Prasyarat
- Python 3.8+
- Telegram Bot Token (dari [@BotFather](https://t.me/BotFather))
- (Opsional) API Key Anthropic untuk AI assistant

### Setup

```bash
# Clone repository
git clone https://github.com/dtimer356-coder/Nova-News-Scraping.git
cd Nova-News-Scraping

# Buat virtual environment
python3 -m venv newsbot_env
source newsbot_env/bin/activate  # Linux/Mac
# atau
newsbot_env\Scripts\activate      # Windows

# Install dependencies
pip install -r requirements.txt

# Terminal 1: Bot Broadcaster (pengirim berita)
cd ~/Nova-News-Scraping
source newsbot_env/bin/activate
python D_NEWS.py

# Terminal 2: Bot Registrasi & Payment Handler
cd ~/Nova-News-Scraping
source newsbot_env/bin/activate
python register.py

# Terminal 3: Dashboard Web
cd ~/Nova-News-Scraping
source newsbot_env/bin/activate
python dashboard.py

## 📁 Struktur Proyek
Nova-News-Scraping/
│
├── 📄 D_NEWS.py                 # Bot utama (broadcaster) - 3500+ baris
├── 📄 register.py               # Bot registrasi & payment handler - 4000+ baris
├── 📄 dashboard.py              # Flask web dashboard - 2500+ baris
│
├── 📁 static/                   # Assets dashboard (CSS, JS)
│
├── 📄 feeds.opml                # Daftar RSS feed (format OPML)
├── 📄 sentiment_config.yaml     # Kamus sentimen untuk analisis
├── 📄 requirements.txt          # Python dependencies
│
├── 📄 accounts.json             # Data akun user (⚠️ JANGAN COMMIT)
├── 📄 subscriptions.json        # Data langganan user (⚠️ JANGAN COMMIT)
├── 📄 payment_pending.json      # Pending pembayaran (⚠️ JANGAN COMMIT)
├── 📄 kick_log.json             # Riwayat user di-kick
├── 📄 blacklist.json            # Daftar feed yang diblokir
├── 📄 sent.json                 # Cache berita yang sudah dikirim
├── 📄 news_bot.db               # Database SQLite (auto generated)
│
├── 📁 backups/                  # Folder backup otomatis
├── 📁 __pycache__/              # Cache Python
│
├── 📄 register.log              # Log bot registrasi
├── 📄 news.log                  # Log bot broadcaster
│
└── 📄 README.md                 # Dokumentasi ini
