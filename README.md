# 📰 NewsBot PRO - Automated Telegram News Broadcasting System

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![Telegram Bot API](https://img.shields.io/badge/Telegram-Bot_API-blue.svg)](https://core.telegram.org/bots/api)
[![Flask](https://img.shields.io/badge/Flask-2.0+-green.svg)](https://flask.palletsprojects.com/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Sistem otomatis pengiriman berita ke Telegram dengan manajemen subscription multi-tier, integrasi pembayaran GoPay/OVO, dan dashboard admin lengkap.

## ✨ Fitur Unggulan

### 📰 **Agregator Berita Cerdas**
- 10+ kategori berita: Politik, Militer, Kripto, Teknologi, Bisnis, Olahraga, Hiburan, Sains, Kesehatan, Umum
- Auto-classification dengan keyword boosting & URL mapping
- 50+ sumber RSS feed terpercaya
- Filter spam dan konten duplikat

### 💰 **Sistem Subscription Multi-Tier**
| Paket | Harga | Kategori | Kecepatan |
|-------|-------|----------|-----------|
| 🥉 Basic | Rp 35.000/bulan | 3 kategori | 10 berita/jam |
| 🥈 Standard | Rp 45.000/bulan | 7 kategori | 30 berita/jam |
| 🥇 Premium | Rp 65.000/bulan | 10 kategori | 100 berita/jam |
| 👑 Tahunan | Rp 550.000/tahun | 10 kategori | 100 berita/jam |

### 💳 **Payment Integration**
- GoPay & OVO (081236072208 a/n Gede Dylan Pratama Wijaya)
- Auto-confirm system dengan bukti transfer
- Notifikasi admin untuk pending payment

### 📊 **Admin Dashboard (Flask Web)**
- Statistik real-time (user, berita, revenue)
- Filter berita lengkap (kategori, tanggal, pencarian)
- Export CSV/PDF dengan terjemahan otomatis ke Bahasa Indonesia
- Sentiment analysis dengan lexicon YAML
- Broadcast message ke semua/individual member
- Manajemen user (kick, ban, enable, disable)

### 🤖 **AI Chatbot Assistant**
- Terintegrasi dengan Claude API
- Menjawab pertanyaan tentang berita & statistik
- Auto-detect kategori dan tanggal dari pertanyaan

## 🛠️ Instalasi

### Prasyarat
- Python 3.8+
- Telegram Bot Token (dari [@BotFather](https://t.me/BotFather))
- (Opsional) API Key Anthropic untuk AI assistant

### Setup

```bash
# Clone repository
git clone https://github.com/gededylan/newsbot-pro-telegram.git
cd newsbot-pro-telegram

# Buat virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Buat file accounts.json (lihat contoh di bawah)
nano accounts.json
