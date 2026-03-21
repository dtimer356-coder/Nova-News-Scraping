#!/usr/bin/env python3
"""
📰 NewsBot PRO - Auto News Broadcasting System
Version: 3.0.0 (Professional Commercial Grade)

PENINGKATAN v3.0:
  - Kategori CRYPTO ditambahkan dengan keyword lengkap
  - Akurasi penyaringan kategori ditingkatkan (feed URL mapping + keyword boosting)
  - Hanya kirim ke member sesuai kategori yang dipilih (strict filtering)
  - Kirim berita hanya ke member yang paketnya mendukung kategori tersebut
  - Export per member: berita dicatat per user
  - Notifikasi expired tidak mengganggu pengiriman berita
  - Rate limiting lebih pintar (per paket)
"""

import asyncio
import aiohttp
import feedparser
import json
import html
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.error import TelegramError
import random
import os
import sys
import signal
import logging
from logging.handlers import RotatingFileHandler
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
import hashlib
import time
import sqlite3
from contextlib import contextmanager
import re
from urllib.parse import urlparse
try:
    from deep_translator import GoogleTranslator
    TRANSLATOR_AVAILABLE = True
except ImportError:
    TRANSLATOR_AVAILABLE = False
import threading
from queue import Queue
import psutil

# ========= ADVANCED CONFIGURATION =========
class Config:
    OPML_FILE      = "feeds.opml"
    SENT_FILE      = "sent.json"
    LOG_FILE       = "news.log"
    BLACKLIST_FILE = "blacklist.json"
    ACCOUNTS_FILE  = "accounts.json"
    DATABASE_FILE  = "news_bot.db"
    STATS_FILE     = "stats.json"

    UPDATE_INTERVAL  = 3600    # 1 jam
    MAX_CONCURRENT   = 5
    MAX_RETRIES      = 3
    FAIL_THRESHOLD   = 3
    REQUEST_TIMEOUT  = 30
    BATCH_SIZE       = 10

    ENABLE_ANALYTICS  = True
    ENABLE_TRANSLATE  = True   # Auto-translate berita ke Bahasa Indonesia
    MAX_NEWS_AGE_DAYS = 1      # Maksimal usia berita (hari). 1 = hanya hari ini & kemarin
    ENABLE_AUTO_BACKUP= True
    ENABLE_MONITORING = False   # Disabled by default (no port needed)

    BACKUP_DIR          = "backups"
    BACKUP_INTERVAL     = 86400
    SUBSCRIPTION_FILE   = "subscriptions.json"

    # Kategori default per paket -- dipakai jika categories di akun kosong
    PLAN_CATEGORIES = {
        "basic":    ["general", "politik", "business"],
        "standard": ["general", "politik", "business", "technology", "health", "sports", "entertainment"],
        "premium":  ["technology","business","sports","entertainment","science","health","politik","militer","general","crypto"],
        "yearly":   ["technology","business","sports","entertainment","science","health","politik","militer","general","crypto"],
    }

# ========= ENUMS =========
class NewsCategory(Enum):
    TECHNOLOGY    = "technology"
    BUSINESS      = "business"
    SPORTS        = "sports"
    ENTERTAINMENT = "entertainment"
    SCIENCE       = "science"
    HEALTH        = "health"
    POLITIK       = "politik"
    MILITER       = "militer"
    GENERAL       = "general"
    CRYPTO        = "crypto"       # BARU

class MessagePriority(Enum):
    HIGH   = 1
    NORMAL = 2
    LOW    = 3

@dataclass
class NewsItem:
    title:     str
    link:      str
    published: datetime
    summary:   str = ""
    author:    str = ""
    category:  NewsCategory = NewsCategory.GENERAL
    priority:  MessagePriority = MessagePriority.NORMAL
    image_url: Optional[str] = None
    hash_id:   str = ""
    source_feed: str = ""

    def __post_init__(self):
        if not self.hash_id:
            self.hash_id = hashlib.md5(f"{self.link}{self.title}".encode()).hexdigest()

@dataclass
class Account:
    token:        str
    chat_id:      str
    name:         str = ""
    categories:   List[str] = None
    max_per_hour: int = 100
    is_active:    bool = True
    banned:       bool = False
    plan:         str = "standard"
    created_at:   datetime = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
        # FIX Celah 1: categories None/kosong -> fallback ke default paket
        if not self.categories:
            self.categories = Config.PLAN_CATEGORIES.get(self.plan, ["general"])

# ========= SUBSCRIPTION VALIDATOR =========
def _load_subscriptions_cache() -> dict:
    """Baca subscriptions.json, dipanggil tiap siklus kirim."""
    try:
        if os.path.exists(Config.SUBSCRIPTION_FILE):
            with open(Config.SUBSCRIPTION_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def is_subscription_valid(chat_id: str, subs_cache: dict) -> bool:
    """
    FIX Celah 2: Cek langganan real-time dari subscriptions.json.
    User expired LANGSUNG berhenti terima berita tanpa menunggu scheduler.
    """
    sub = subs_cache.get(str(chat_id))
    if not sub:
        return True  # tidak ada data sub -> izinkan (akun admin/lama)
    try:
        expiry_str = sub.get("expiry", "")
        if not expiry_str:
            return True
        try:
            expiry = datetime.strptime(expiry_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            expiry = datetime.fromisoformat(expiry_str)
        return datetime.now() <= expiry
    except Exception:
        return True  # gagal parse -> izinkan

# ========= LOGGING =========
class AdvancedLogger:
    def __init__(self, name: str, log_file: str):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.DEBUG)
        handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        self.logger.addHandler(console)

    def debug(self, msg): self.logger.debug(msg)
    def info(self, msg):  self.logger.info(msg)
    def warning(self, msg): self.logger.warning(msg)
    def error(self, msg): self.logger.error(msg)
    def critical(self, msg): self.logger.critical(msg)

# ========= DATABASE =========
class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.pool    = Queue(maxsize=Config.MAX_CONCURRENT * 2)
        self._init_pool()
        self._init_tables()

    def _init_pool(self):
        for _ in range(Config.MAX_CONCURRENT * 2):
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.pool.put(conn)

    @contextmanager
    def get_connection(self):
        conn = self.pool.get()
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            self.pool.put(conn)

    def _init_tables(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS news (
                    hash_id TEXT PRIMARY KEY, title TEXT, link TEXT UNIQUE,
                    summary TEXT, author TEXT, category TEXT, priority INTEGER,
                    image_url TEXT, published TIMESTAMP, source_feed TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, sent_count INTEGER DEFAULT 0
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sent_news (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    news_hash TEXT, account_id TEXT,
                    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, status TEXT,
                    FOREIGN KEY (news_hash) REFERENCES news(hash_id)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT UNIQUE,
                    chat_id TEXT, name TEXT, categories TEXT, max_per_hour INTEGER,
                    is_active BOOLEAN, created_at TIMESTAMP, last_active TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS statistics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, date DATE, total_news INTEGER,
                    total_sent INTEGER, failed_count INTEGER, active_accounts INTEGER,
                    avg_response_time REAL
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS failed_feeds (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, feed_url TEXT, reason TEXT,
                    failed_count INTEGER DEFAULT 1, last_failed TIMESTAMP,
                    is_blacklisted BOOLEAN DEFAULT 0
                )
            """)
            # Index untuk performa
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sent_news_account ON sent_news(account_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sent_news_hash ON sent_news(news_hash)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_category ON news(category)")

    def save_news(self, news_item: NewsItem):
        with self.get_connection() as conn:
            conn.cursor().execute("""
                INSERT OR REPLACE INTO news
                (hash_id, title, link, summary, author, category, priority, image_url, published, source_feed)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (news_item.hash_id, news_item.title, news_item.link, news_item.summary,
                  news_item.author, news_item.category.value, news_item.priority.value,
                  news_item.image_url, news_item.published, news_item.source_feed))

    def mark_as_sent(self, news_hash: str, account_id: str, status: str = "success"):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO sent_news (news_hash, account_id, status) VALUES (?,?,?)",
                           (news_hash, account_id, status))
            cursor.execute("UPDATE news SET sent_count=sent_count+1 WHERE hash_id=?", (news_hash,))

    def is_sent(self, news_hash: str, account_id: str) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM sent_news WHERE news_hash=? AND account_id=?",
                           (news_hash, account_id))
            return cursor.fetchone() is not None

    def get_stats_today(self) -> dict:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM sent_news WHERE date(sent_at)=date('now')")
            sent_today = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM news WHERE date(created_at)=date('now')")
            news_today = cursor.fetchone()[0]
            return {"sent_today": sent_today, "news_today": news_today}

# ========= CONTENT FILTER — TERMASUK CRYPTO =========
class ContentFilter:
    def __init__(self):
        self.category_keywords = {
            'technology': {
                'primary': [
                    r'technology|tech', r'software', r'hardware', r'artificial intelligence|ai\b',
                    r'machine learning', r'deep learning', r'neural network', r'computer',
                    r'digital', r'app|application', r'programming|coding', r'developer',
                    r'cybersecurity|cyber security', r'hacking|hacker', r'database', r'algorithm',
                    r'cloud computing', r'aws|azure|google cloud', r'devops', r'api',
                    r'iot|internet of things', r'robotics|robot', r'automation', r'5g|6g',
                    r'semiconductor|chip', r'processor|cpu', r'gpu|graphics card',
                    r'smartphone|iphone|android', r'laptop|notebook', r'os|operating system',
                    r'windows|macos|linux', r'bug|glitch', r'patch|update|firmware',
                    r'github|gitlab', r'open source', r'framework', r'web development',
                    r'teknologi|tekno', r'perangkat lunak', r'perangkat keras', r'kecerdasan buatan',
                    r'komputer', r'aplikasi', r'pemrograman', r'pengembang', r'keamanan siber',
                    r'internet of things', r'robot|robotik', r'otomatisasi', r'server',
                    r'pusat data', r'semikonduktor', r'ponsel pintar', r'hp|handphone',
                    r'sistem operasi', r'sumber terbuka', r'kerangka kerja',
                ],
                'secondary': [
                    r'silicon valley', r'startup|start-up', r'innovation', r'digital transformation',
                    r'gadget', r'device', r'electronics', r'encryption', r'firewall',
                    r'malware|virus', r'ransomware', r'phishing', r'vpn', r'bandwidth',
                    r'inovasi', r'gadget', r'perangkat', r'enkripsi', r'antivirus',
                ]
            },

            'crypto': {
                'primary': [
                    # English crypto terms
                    r'bitcoin|btc\b', r'ethereum|eth\b', r'crypto|cryptocurrency', r'blockchain',
                    r'altcoin|altcoins', r'token|tokens', r'defi|decentralized finance',
                    r'nft|non-fungible token', r'web3|web 3\.0', r'stablecoin', r'usdt|usdc|busd',
                    r'solana|sol\b', r'cardano|ada\b', r'binance|bnb\b', r'ripple|xrp\b',
                    r'dogecoin|doge\b', r'shiba inu|shib\b', r'polkadot|dot\b', r'avalanche|avax\b',
                    r'chainlink|link\b', r'uniswap|uni\b', r'aave|curve\b', r'compound\b',
                    r'metamask', r'ledger|trezor', r'cold wallet|hot wallet', r'crypto wallet',
                    r'exchange|dex|cex', r'coinbase|binance exchange|kraken|okx|bybit',
                    r'mining|miner|hashrate', r'staking|yield farming', r'liquidity pool',
                    r'smart contract', r'dao|decentralized autonomous', r'layer 2|l2\b',
                    r'ethereum 2\.0|eth 2\.0|proof of stake|pos\b', r'proof of work|pow\b',
                    r'halving|bitcoin halving', r'bull run|bear market', r'crypto market',
                    r'market cap|market capitalization', r'altseason', r'whale\b',
                    r'hodl|fud|fomo', r'pump|dump', r'rug pull|scam', r'airdrop',
                    r'ico|ido|ieo', r'crypto regulation', r'sec crypto|cftc crypto',
                    r'crypto news', r'bitcoin price', r'ethereum price', r'crypto price',
                    # Indonesian
                    r'kripto|cryptocurrency', r'blockchain', r'bitcoin|btc',
                    r'ethereum|eth', r'koin kripto', r'token kripto', r'dompet kripto',
                    r'bursa kripto', r'pertukaran kripto', r'penambangan kripto',
                    r'mining bitcoin', r'regulasi kripto', r'harga bitcoin', r'harga ethereum',
                ],
                'secondary': [
                    r'digital asset|aset digital', r'virtual currency|mata uang virtual',
                    r'crypto exchange', r'trading crypto', r'invest crypto',
                    r'distributed ledger', r'consensus mechanism', r'gas fee|gas limit',
                    r'mempool', r'node|validator', r'fork|hard fork|soft fork',
                    r'wrapped token', r'cross-chain|bridge', r'layer 1|l1\b',
                    r'metaverse crypto', r'play to earn|p2e', r'gamefi',
                    r'inveatasi kripto', r'perdagangan kripto', r'aset digital',
                ]
            },

            'business': {
                'primary': [
                    r'business|bisnis', r'economy|ekonomi', r'market|pasar', r'stock|saham',
                    r'investment|investasi', r'finance|keuangan', r'bank|banking',
                    r'company|perusahaan', r'corporation|korporasi', r'profit|keuntungan',
                    r'revenue|pendapatan', r'earnings|laba', r'gdp|pdb', r'inflation|inflasi',
                    r'interest rate|suku bunga', r'exchange rate|nilai tukar', r'trade|perdagangan',
                    r'export|import|ekspor|impor', r'merger|acquisition|akuisisi',
                    r'startup|unicorn', r'ipo|initial public offering', r'venture capital',
                    r'bonds|obligasi', r'forex|valuta asing', r'commodity|komoditas',
                    r'gold|oil|silver|emas|minyak', r'real estate|properti',
                    r'retail|ecommerce|e-commerce', r'supply chain|rantai pasok',
                    r'budget|anggaran', r'tax|pajak', r'bumn|soe',
                ],
                'secondary': [
                    r'ceo|cfo|coo|board of directors', r'shareholder|pemegang saham',
                    r'dividend|dividen', r'quarter|Q[1-4]', r'fiscal year|tahun fiskal',
                    r'balance sheet|neraca', r'cash flow', r'debt|utang', r'bankruptcy|pailit',
                ]
            },

            'sports': {
                'primary': [
                    r'sports|sport|olahraga', r'football|soccer|sepak bola',
                    r'basketball|bola basket', r'tennis|tenis', r'baseball', r'golf',
                    r'swimming|renang', r'running|lari', r'athletics|atletik',
                    r'olympic|olimpiade', r'world cup|piala dunia', r'champion|juara',
                    r'league|liga', r'match|pertandingan', r'game|permainan',
                    r'score|skor', r'goal|gol', r'player|pemain', r'team|tim',
                    r'coach|pelatih', r'stadium|stadion', r'tournament|turnamen',
                    r'fifa|uefa|nba|nfl|nhl|mlb', r'transfer|kontrak pemain',
                    r'premier league|la liga|serie a|bundesliga|champions league',
                    r'formula 1|f1|motogp|wrc', r'boxing|tinju', r'mma|ufc',
                    r'badminton|bulu tangkis', r'volleyball|voli',
                ],
                'secondary': [
                    r'win|lose|draw|menang|kalah|seri', r'season|musim',
                    r'penalty|kartu merah|kartu kuning', r'offside', r'referee|wasit',
                    r'fan|supporter|suporter', r'transfer window|bursa transfer',
                ]
            },

            'entertainment': {
                'primary': [
                    r'entertainment|hiburan', r'movie|film', r'music|musik', r'celebrity|selebriti',
                    r'actor|actress|aktor|aktris', r'singer|penyanyi', r'band', r'album',
                    r'concert|konser', r'award|penghargaan', r'oscar|grammy|emmy|golden globe',
                    r'netflix|disney\+|hulu|hbo|amazon prime', r'streaming',
                    r'tv show|serial|drama|series', r'anime', r'kpop|k-pop',
                    r'hollywood|bollywood', r'box office', r'premiere|tayang perdana',
                    r'celebrity news', r'paparazzi', r'fashion|mode', r'model',
                    r'artis|idol', r'konser|tour', r'video klip', r'chart|tangga lagu',
                ],
                'secondary': [
                    r'trailer', r'review|ulasan', r'rating', r'season|episode',
                    r'plot|cerita', r'cast|pemeran', r'director|sutradara',
                    r'producer|produser', r'script|naskah',
                ]
            },

            'science': {
                'primary': [
                    r'science|sains', r'research|penelitian|riset', r'study|studi',
                    r'discovery|penemuan', r'experiment|eksperimen', r'laboratory|laboratorium',
                    r'scientist|ilmuwan', r'physics|fisika', r'chemistry|kimia',
                    r'biology|biologi', r'astronomy|astronomi', r'space|luar angkasa',
                    r'nasa|esa|spacex|blue origin', r'planet|galaxy|asteroid',
                    r'climate change|perubahan iklim', r'environment|lingkungan',
                    r'evolution|evolusi', r'genetics|genetika', r'dna|rna',
                    r'quantum|kuantum', r'particle|partikel', r'telescope|teleskop',
                    r'mars|moon|satellite|rocket', r'nuclear|nuklir',
                    r'ecology|ekologi', r'biodiversity|keanekaragaman hayati',
                ],
                'secondary': [
                    r'journal|jurnal', r'peer review', r'publication|publikasi',
                    r'professor|profesor', r'phd|doktor', r'university|universitas',
                    r'theory|teori', r'hypothesis|hipotesis', r'observation|observasi',
                    r'data analysis|analisis data',
                ]
            },

            'health': {
                'primary': [
                    r'health|kesehatan|sehat', r'medical|medis', r'doctor|dokter',
                    r'hospital|rumah sakit|klinik', r'disease|penyakit', r'virus|bakteri',
                    r'pandemic|epidemi|wabah', r'covid|coronavirus', r'vaccine|vaksin',
                    r'treatment|pengobatan', r'surgery|operasi|bedah', r'medication|obat',
                    r'patient|pasien', r'cancer|kanker', r'heart disease|penyakit jantung',
                    r'diabetes|hypertension|hipertensi', r'stroke', r'mental health|kesehatan mental',
                    r'depression|depresi', r'anxiety|kecemasan', r'nutrition|gizi|nutrisi',
                    r'obesity|obesitas', r'fitness|kebugaran', r'healthcare|layanan kesehatan',
                    r'bpjs', r'who|world health organization', r'fda', r'pharmaceutical|farmasi',
                    r'clinical trial|uji klinis', r'drug|obat-obatan', r'allergy|alergi',
                    r'malaria|dengue|dbd', r'tuberculosis|tbc', r'hiv|aids', r'hepatitis',
                ],
                'secondary': [
                    r'symptom|gejala', r'diagnosis|diagnosa', r'therapy|terapi',
                    r'recovery|pemulihan', r'prevention|pencegahan', r'immune|imun',
                    r'blood pressure|tekanan darah', r'cholesterol|kolesterol',
                    r'emergency|darurat', r'ambulance|ambulan', r'icu|igd',
                ]
            },

            'politik': {
                'primary': [
                    r'politics|political|politik', r'government|pemerintah|pemerintahan',
                    r'election|pemilu|pemilihan', r'president|presiden', r'parliament|parlemen|dpr',
                    r'minister|menteri|kementerian', r'prime minister|perdana menteri',
                    r'cabinet|kabinet', r'political party|partai politik',
                    r'democracy|demokrasi', r'constitution|konstitusi|uud',
                    r'law|legislation|undang-undang|peraturan', r'policy|kebijakan',
                    r'corruption|korupsi', r'scandal|skandal', r'impeachment|makzul',
                    r'vote|voting|suara|memilih', r'campaign|kampanye',
                    r'protest|demonstrasi|unjuk rasa', r'debate|debat',
                    r'coalition|koalisi', r'opposition|oposisi', r'candidate|kandidat|calon',
                    r'senator|congressman|legislator|anggota dpr', r'governor|gubernur',
                    r'mayor|walikota|bupati', r'pilkada|pilgub|pilpres|pileg',
                    r'kpk|komisi pemberantasan korupsi', r'mahkamah konstitusi|mk',
                    r'tni|polri|kepolisian|kejaksaan',
                    r'foreign policy|kebijakan luar negeri', r'diplomacy|diplomasi',
                    r'sanction|sanksi', r'treaty|perjanjian', r'summit|ktt',
                    r'nato|asean|g20|g7|un|pbb',
                ],
                'secondary': [
                    r'political analyst|pengamat politik', r'survey|jajak pendapat',
                    r'approval rating|tingkat kepuasan', r'nationalism|nasionalisme',
                    r'ideology|ideologi', r'left wing|right wing', r'populism|populisme',
                    r'referendum', r'autonomy|otonomi', r'sovereignty|kedaulatan',
                ]
            },

            'militer': {
                'primary': [
                    r'military|armed forces|angkatan bersenjata', r'army|angkatan darat',
                    r'navy|naval|angkatan laut', r'air force|angkatan udara',
                    r'marine|marinir', r'special forces|pasukan khusus', r'commando|kopassus',
                    r'infantry|artileri|kavaleri', r'defense|pertahanan',
                    r'intelligence|intelijen|bais', r'reconnaissance|pengintaian',
                    r'military operation|operasi militer', r'mission|misi',
                    r'weapon|senjata|alutsista', r'missile|rudal', r'rocket|roket',
                    r'bomb|bom', r'tank', r'armored vehicle|panser|ranpur',
                    r'fighter jet|pesawat tempur', r'helicopter|helikopter', r'drone|uav',
                    r'warship|kapal perang', r'aircraft carrier|kapal induk',
                    r'submarine|kapal selam', r'frigate|fregat|korvet',
                    r'military exercise|latihan militer|latma',
                    r'combat|pertempuran', r'war|perang', r'battle|pertempuran',
                    r'conflict|konflik', r'invasion|invasi', r'ceasefire|gencatan senjata',
                    r'peacekeeping|perdamaian|kontingen garuda',
                    r'terrorism|terorisme', r'insurgency|insurgensi',
                    r'defense budget|anggaran pertahanan', r'arms deal|pengadaan senjata',
                    r'tni ad|tni au|tni al', r'kodam|kostrad|kopassus|denjaka|paskhas',
                    r'defense industry|industri pertahanan',
                ],
                'secondary': [
                    r'general|jenderal', r'admiral|laksamana', r'colonel|kolonel',
                    r'major|mayor', r'captain|kapten', r'sergeant|sersan',
                    r'veteran', r'military academy|akademi militer|akmil',
                    r'military strategy|strategi militer', r'doctrine|doktrin',
                    r'nato military|us military', r'military alliance',
                ]
            }
        }

        # Compile semua patterns
        self.compiled_patterns = {}
        for category, patterns in self.category_keywords.items():
            self.compiled_patterns[category] = {
                'primary':   [re.compile(p, re.I) for p in patterns['primary']],
                'secondary': [re.compile(p, re.I) for p in patterns['secondary']]
            }

        # Mapping URL feed ke kategori (prioritas tertinggi)
        self.feed_url_category_map = {
            # Crypto feeds — langsung ke crypto
            'coindesk':        'crypto',
            'cointelegraph':   'crypto',
            'theblock':        'crypto',
            'decrypt.co':      'crypto',
            'cryptobriefing':  'crypto',
            'bitcoinmagazine': 'crypto',
            'beincrypto':      'crypto',
            'cryptoslate':     'crypto',
            'newsbtc':         'crypto',
            'ambcrypto':       'crypto',
            'bitcoinist':      'crypto',
            'u.today':         'crypto',
            'coingape':        'crypto',
            'cryptonews':      'crypto',
            'coinvestasi':     'crypto',
            'indodax':         'crypto',
            'tokocrypto':      'crypto',
            'thedefiant':      'crypto',
            'blockworks':      'crypto',
            'dlnews':          'crypto',
            # Military feeds
            'defensenews':     'militer',
            'militarytimes':   'militer',
            'armyrecognition': 'militer',
            'thedrive.com/feeds/the-war-zone': 'militer',
            'navalnews':       'militer',
            'breakingdefense': 'militer',
            'defenseone':      'militer',
            'defense.gov':     'militer',
            'chinamil':        'militer',
            'tass.com/defense':'militer',
            'malaysiandefence':'militer',
            'defence.gov.au':  'militer',
            # Tech feeds
            'techcrunch':      'technology',
            'theverge':        'technology',
            'wired':           'technology',
            'arstechnica':     'technology',
            'engadget':        'technology',
            'technologyreview':'technology',
            # Business
            'bloomberg':       'business',
            'ft.com':          'business',
            'marketwatch':     'business',
            'wsj.com':         'business',
            # Sports
            'espn':            'sports',
            'skysports':       'sports',
            'goal.com':        'sports',
            'bola.net':        'sports',
            # Science
            'sciencedaily':    'science',
            'nature.com':      'science',
            'newscientist':    'science',
            'technologyreview':'technology',
            # Health
            'who.int':         'health',
            'medicalnewstoday':'health',
            'healthline':      'health',
        }

        # Spam filter
        self.spam_keywords = [
            re.compile(p, re.I) for p in [
                r'click here', r'buy now', r'limited time offer', r'free money',
                r'casino', r'lottery', r'winner notification', r'earn money fast',
                r'work from home (earn)', r'double your money', r'urgent action required',
                r'adult content', r'xxx', r'escort', r'dating',
            ]
        ]

    def is_spam(self, text: str) -> bool:
        return any(p.search(text) for p in self.spam_keywords)

    def clean_html(self, text: str) -> str:
        text = re.sub(r'<[^>]+>', '', text)
        text = html.unescape(text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:500]

    def extract_images(self, text: str) -> List[str]:
        return re.findall(r'https?://[^\s<>"]+?\.(?:jpg|jpeg|png|gif|webp)', text, re.I)

    def detect_category_from_url(self, feed_url: str) -> Optional[str]:
        """Deteksi kategori langsung dari URL feed — akurasi 100%"""
        url_lower = feed_url.lower()
        for key, cat in self.feed_url_category_map.items():
            if key in url_lower:
                return cat
        return None

    def calculate_score(self, text: str, patterns: dict) -> Tuple[float, float, list]:
        primary_score = 0
        secondary_score = 0
        matches = []
        for p in patterns['primary']:
            m = p.findall(text)
            if m:
                primary_score += len(m) * 3
                matches.extend(m)
        for p in patterns['secondary']:
            m = p.findall(text)
            if m:
                secondary_score += len(m) * 1
                matches.extend(m)
        return primary_score, secondary_score, matches

    def classify_text(self, text: str) -> str:
        """Klasifikasi teks dengan skor tertimbang"""
        scores = {}
        for category, patterns in self.compiled_patterns.items():
            ps, ss, matches = self.calculate_score(text, patterns)
            total = ps + ss
            if total > 0:
                scores[category] = total

        if not scores:
            return 'general'

        # Disambiguasi khusus
        if 'politik' in scores and 'militer' in scores:
            mil_hints = ['tni','rudal','tank','perang','pesawat tempur','kapal perang',
                         'kopassus','prajurit','senjata','alutsista','invasi']
            pol_hints = ['pemilu','presiden','menteri','dpr','partai','kabinet','koalisi']
            mc = sum(1 for h in mil_hints if h in text)
            pc = sum(1 for h in pol_hints if h in text)
            if mc > pc * 1.5:
                scores['militer'] = scores.get('militer',0) * 1.5
            elif pc > mc * 1.5:
                scores['politik'] = scores.get('politik',0) * 1.5

        if 'crypto' in scores:
            crypto_hints = ['bitcoin','ethereum','btc','eth','blockchain','defi','nft',
                            'token','kripto','crypto','coindesk','cointelegraph']
            cc = sum(1 for h in crypto_hints if h in text.lower())
            if cc >= 2:
                scores['crypto'] = scores.get('crypto',0) * 2.0

        if 'technology' in scores and 'business' in scores:
            tech_hints = ['software','algorithm','coding','developer','ai','machine learning']
            biz_hints  = ['saham','investasi','revenue','profit','stock','market']
            tc = sum(1 for h in tech_hints if h in text.lower())
            bc = sum(1 for h in biz_hints  if h in text.lower())
            if tc > bc * 2:
                scores['technology'] = scores.get('technology',0) * 1.3
            elif bc > tc * 2:
                scores['business'] = scores.get('business',0) * 1.3

        best = max(scores, key=scores.get)
        if scores[best] >= 3:
            return best
        return 'general'

# ========= ANALYTICS ENGINE =========
class AnalyticsEngine:
    def __init__(self, db: DatabaseManager):
        self.db = db
        self.start_time  = datetime.now()
        self.total_sent  = 0
        self.total_failed = 0

    def record_message(self, success: bool, response_time: float):
        if success:
            self.total_sent += 1
        else:
            self.total_failed += 1

    def get_daily_summary(self) -> dict:
        stats = self.db.get_stats_today()
        return {
            'total': self.total_sent + self.total_failed,
            'success': self.total_sent,
            'failed': self.total_failed,
            'sent_today': stats['sent_today'],
            'news_today': stats['news_today'],
        }

    def save_stats(self):
        data = {
            'start_time': self.start_time.isoformat(),
            'total_sent': self.total_sent,
            'total_failed': self.total_failed,
            'last_update': datetime.now().isoformat()
        }
        with open(Config.STATS_FILE, 'w') as f:
            json.dump(data, f, indent=2)

# ========= TRANSLATOR =========
def translate_to_id(text: str, max_len: int = 500) -> str:
    """Terjemahkan teks ke Bahasa Indonesia. Fallback ke original jika gagal."""
    if not TRANSLATOR_AVAILABLE or not Config.ENABLE_TRANSLATE:
        return text
    if not text or not text.strip():
        return text
    # Deteksi: kalau sudah ada banyak kata Indonesia, skip
    id_indicators = ['yang','dan','dengan','untuk','dalam','adalah','ini','itu',
                     'dari','ke','di','pada','oleh','akan','telah','sudah','juga']
    words = text.lower().split()
    id_count = sum(1 for w in words if w in id_indicators)
    if len(words) > 5 and id_count / len(words) > 0.2:
        return text  # Sudah Indonesia
    try:
        chunk = text[:max_len]
        result = GoogleTranslator(source='auto', target='id').translate(chunk)
        return result if result else text
    except Exception:
        return text

# ========= MAIN BOT =========
class NewsBot:
    def __init__(self):
        self.logger         = AdvancedLogger("NewsBot", Config.LOG_FILE)
        self.db             = DatabaseManager(Config.DATABASE_FILE)
        self.content_filter = ContentFilter()
        self.analytics      = AnalyticsEngine(self.db)
        self.semaphore      = asyncio.Semaphore(Config.MAX_CONCURRENT)
        self.failed_feeds   = {}
        self.blacklist      = {}
        self.running        = True
        self.accounts: List[Account] = []
        self.sent_cache     = {}

        self._load_configs()
        signal.signal(signal.SIGINT,  self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        self.logger.info("Shutdown signal received")
        self.running = False

    def _load_configs(self):
        self._load_blacklist()
        self._load_accounts()
        self._load_sent_cache()

    def _load_blacklist(self):
        try:
            if os.path.exists(Config.BLACKLIST_FILE):
                with open(Config.BLACKLIST_FILE) as f:
                    self.blacklist = json.load(f)
        except Exception: self.blacklist = {}

    def _save_blacklist(self):
        with open(Config.BLACKLIST_FILE,'w') as f:
            json.dump(self.blacklist, f)

    def _load_accounts(self):
        try:
            with open(Config.ACCOUNTS_FILE) as f:
                data = json.load(f)
                self.accounts = []
                for d in data:
                    # Hanya load akun yang aktif dan tidak banned
                    if d.get('is_active', True) and not d.get('banned', False):
                        try:
                            acc = Account(
                                token=d['token'], chat_id=d['chat_id'],
                                name=d.get('name',''), categories=d.get('categories',['general']),
                                max_per_hour=d.get('max_per_hour',100),
                                is_active=d.get('is_active',True),
                                banned=d.get('banned',False),
                                plan=d.get('plan','standard')
                            )
                            self.accounts.append(acc)
                        except Exception as e:
                            self.logger.error(f"Skip account: {e}")
        except Exception as e:
            self.logger.error(f"Load accounts: {e}")
            self.accounts = []

    def _load_sent_cache(self):
        try:
            with open(Config.SENT_FILE) as f:
                self.sent_cache = json.load(f)
        except Exception:
            self.sent_cache = {}

    def _save_sent_cache(self):
        # Batasi cache ke 10000 entri terbaru
        if len(self.sent_cache) > 10000:
            keys = list(self.sent_cache.keys())
            self.sent_cache = {k: self.sent_cache[k] for k in keys[-8000:]}
        with open(Config.SENT_FILE,'w') as f:
            json.dump(self.sent_cache, f)

    def parse_opml(self) -> Dict[str, List[str]]:
        try:
            tree  = ET.parse(Config.OPML_FILE)
            root  = tree.getroot()
            topics = {}
            for outline in root.findall(".//outline"):
                if 'text' in outline.attrib:
                    text     = outline.attrib['text']
                    children = outline.findall("outline[@xmlUrl]")
                    if children:
                        topics[text] = [c.attrib['xmlUrl'] for c in children if 'xmlUrl' in c.attrib]
            return topics
        except Exception as e:
            self.logger.error(f"parse_opml: {e}")
            return {}

    async def fetch_feed(self, feed_url: str, topic: str = "") -> Optional[List[NewsItem]]:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; NewsBot/3.0)"}
        # Deteksi kategori dari URL dulu
        url_category = self.content_filter.detect_category_from_url(feed_url)

        async with self.semaphore:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        feed_url, headers=headers,
                        timeout=aiohttp.ClientTimeout(total=Config.REQUEST_TIMEOUT)
                    ) as resp:
                        if resp.status != 200:
                            raise Exception(f"HTTP {resp.status}")
                        content = await resp.read()
                        feed    = feedparser.parse(content)
                        if not feed.entries:
                            return []

                        entries = []
                        for entry in feed.entries[:15]:
                            title   = entry.get('title','')
                            summary = self.content_filter.clean_html(entry.get('summary',''))
                            link    = entry.get('link','')

                            if not title or not link:
                                continue
                            if self.content_filter.is_spam(title):
                                continue

                            # Deteksi kategori
                            if url_category:
                                # URL mapping — 100% akurat
                                cat_value = url_category
                            else:
                                # Keyword analysis dari teks
                                full_text = f"{title} {summary}".lower()
                                cat_value = self.content_filter.classify_text(full_text)
                                # Boost dari RSS tags
                                if hasattr(entry, 'tags'):
                                    for tag in entry.tags:
                                        if hasattr(tag,'term'):
                                            tv = tag.term.lower()
                                            for cat in NewsCategory.__members__.values():
                                                if cat.value in tv:
                                                    cat_value = cat.value
                                                    break

                            category = NewsCategory(cat_value) if cat_value in [c.value for c in NewsCategory] else NewsCategory.GENERAL

                            published = datetime.now()
                            if hasattr(entry,'published_parsed') and entry.published_parsed:
                                try:
                                    published = datetime(*entry.published_parsed[:6])
                                except Exception:
                                    pass

                            # Filter berita terlalu lama — maksimal kemarin
                            cutoff = datetime.now() - timedelta(days=Config.MAX_NEWS_AGE_DAYS)
                            if published < cutoff:
                                continue  # Skip berita lebih lama dari kemarin

                            news = NewsItem(
                                title=title, link=link, published=published,
                                summary=summary, author=entry.get('author',''),
                                category=category,
                                image_url=self._extract_image(entry),
                                source_feed=feed_url
                            )
                            entries.append(news)
                            self.db.save_news(news)

                        self.logger.info(f"Fetched {len(entries)} from {feed_url[:60]}")
                        return entries

            except Exception as e:
                self.logger.error(f"Fetch {feed_url[:60]}: {e}")
                self._record_failure(feed_url, str(e))
                return None

    def _extract_image(self, entry) -> Optional[str]:
        if hasattr(entry,'media_content') and entry.media_content:
            for m in entry.media_content:
                if m.get('url') and 'image' in m.get('type','image'):
                    return m['url']
        if hasattr(entry,'enclosures') and entry.enclosures:
            for e in entry.enclosures:
                if e.get('href') and 'image' in e.get('type',''):
                    return e['href']
        if hasattr(entry,'summary'):
            imgs = self.content_filter.extract_images(entry.summary)
            if imgs: return imgs[0]
        return None

    def _record_failure(self, feed_url: str, reason: str):
        if feed_url not in self.failed_feeds:
            self.failed_feeds[feed_url] = {'count':0, 'reason':reason}
        self.failed_feeds[feed_url]['count'] += 1
        if self.failed_feeds[feed_url]['count'] >= Config.FAIL_THRESHOLD:
            self.blacklist[feed_url] = reason
            self._save_blacklist()
            self.logger.warning(f"Blacklisted: {feed_url[:60]}")

    async def send_news_to_account(self, account: Account, topic: str, news_list: List[NewsItem],
                                    subs_cache: dict = None):
        """Kirim berita ke satu akun — HANYA kategori yang ada di akun tsb"""
        if not account.is_active or account.banned:
            return

        # Filter berita sesuai kategori akun
        # FIX Celah 2: Cek subscription real-time
        if subs_cache is not None and not is_subscription_valid(account.chat_id, subs_cache):
            self.logger.info(f"Skip {account.chat_id} ({account.name}) -- expired")
            return

        # FIX Celah 1: categories kosong -> default paket
        if not account.categories:
            account.categories = Config.PLAN_CATEGORIES.get(account.plan, ["general"])
        user_cats = set(account.categories)
        filtered = [n for n in news_list if n.category.value in user_cats]

        if not filtered:
            return

        bot       = Bot(token=account.token)
        sent_count = 0

        CATEGORY_DISPLAY = {
            'technology':    '💻 TEKNOLOGI',
            'business':      '📈 BISNIS & EKONOMI',
            'sports':        '⚽ OLAHRAGA',
            'entertainment': '🎬 HIBURAN',
            'science':       '🔬 SAINS',
            'health':        '🏥 KESEHATAN',
            'politik':       '🏛️ POLITIK',
            'militer':       '🎖️ MILITER',
            'general':       '📰 BERITA UMUM',
            'crypto':        '₿ KRIPTO & BLOCKCHAIN',
        }

        REGION_MAP = {
            'Kawasan Eropa':        '🌍 EROPA',
            'Kawasan Timur Tengah': '🕌 TIMUR TENGAH',
            'Kawasan Afrika':       '🌍 AFRIKA',
            'Kawasan Asia Pasifik': '🌏 ASIA PASIFIK',
            'Kawasan Asia Tenggara':'🌏 ASIA TENGGARA',
            'Kawasan Amerika':      '🌎 AMERIKA',
            'Berita Australia':     '🇦🇺 AUSTRALIA',
            'Militer Dunia':        '🌐 MILITER DUNIA',
            'Militer Negara':       '🎖️ MILITER NEGARA',
            'Ekonomi Global':       '💼 EKONOMI GLOBAL',
            'Media Indonesia':      '🇮🇩 INDONESIA',
            'Crypto Global':        '₿ KRIPTO GLOBAL',
            'Crypto DeFi dan NFT':  '🔗 DEFI & NFT',
            'Crypto Indonesia':     '🇮🇩₿ KRIPTO ID',
            'Teknologi Global':     '💻 TEKNOLOGI',
            'Kesehatan dan Sains':  '🏥 KESEHATAN & SAINS',
            'Olahraga':             '⚽ OLAHRAGA',
        }

        region = next((v for k,v in REGION_MAP.items() if k in topic), topic)

        for news in filtered:
            if sent_count >= account.max_per_hour:
                break
            if self.db.is_sent(news.hash_id, account.chat_id):
                continue

            try:
                cat_display = CATEGORY_DISPLAY.get(news.category.value, '📰')
                header = f"{region} • {cat_display}"

                # Auto-translate ke Bahasa Indonesia
                title_id   = translate_to_id(news.title, 200)
                summary_id = translate_to_id(news.summary, 400) if news.summary else ""

                caption_text = (
                    f"<b>{header}</b>\n\n"
                    f"📰 <b>{html.escape(title_id)}</b>\n\n"
                )
                if summary_id:
                    caption_text += f"{html.escape(summary_id[:350])}...\n\n"
                if news.author:
                    caption_text += f"✍️ {html.escape(news.author)}\n"
                caption_text += f"🕒 {news.published.strftime('%d %b %Y, %H:%M')}"

                btn = InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔗 Baca Selengkapnya", url=news.link)
                ]])

                start = time.time()

                if news.image_url:
                    try:
                        await bot.send_photo(
                            chat_id=account.chat_id, photo=news.image_url,
                            caption=caption_text, parse_mode=ParseMode.HTML, reply_markup=btn
                        )
                    except TelegramError:
                        # Fallback ke teks jika gambar gagal
                        await bot.send_message(
                            chat_id=account.chat_id, text=caption_text,
                            parse_mode=ParseMode.HTML, reply_markup=btn
                        )
                else:
                    await bot.send_message(
                        chat_id=account.chat_id, text=caption_text,
                        parse_mode=ParseMode.HTML, reply_markup=btn
                    )

                self.db.mark_as_sent(news.hash_id, account.chat_id, "success")
                self.analytics.record_message(True, time.time()-start)
                sent_count += 1
                self.logger.info(f"→ {account.chat_id[:6]}** [{news.category.value}] {news.title[:50]}")
                await asyncio.sleep(random.uniform(0.5, 1.5))

            except TelegramError as e:
                self.logger.error(f"TelegramError {account.chat_id}: {e}")
                self.analytics.record_message(False, 0)
                if "bot was blocked" in str(e).lower() or "chat not found" in str(e).lower():
                    account.is_active = False
                    self.logger.warning(f"Deactivated: {account.chat_id}")
                    break
            except Exception as e:
                self.logger.error(f"SendError {account.chat_id}: {e}")
                self.analytics.record_message(False, 0)

    async def process_topic(self, topic: str, feeds: List[str]) -> int:
        all_news: List[NewsItem] = []
        for feed_url in feeds:
            if feed_url in self.blacklist:
                continue
            for attempt in range(Config.MAX_RETRIES):
                news_items = await self.fetch_feed(feed_url, topic)
                if news_items is not None:
                    all_news.extend(news_items)
                    break
                if attempt < Config.MAX_RETRIES - 1:
                    await asyncio.sleep(random.uniform(3, 8))

        if not all_news:
            return 0

        # Dedup
        unique: Dict[str, NewsItem] = {}
        for n in all_news:
            if n.link not in self.sent_cache:
                unique[n.link] = n

        news_list = list(unique.values())
        if not news_list:
            return 0

        # Reload accounts setiap cycle untuk dapat perubahan terbaru
        self._load_accounts()

        # FIX Celah 2: load subs_cache sekali per siklus, pass ke tiap akun
        subs_cache = _load_subscriptions_cache()
        tasks = [self.send_news_to_account(acc, topic, news_list, subs_cache) for acc in self.accounts]
        await asyncio.gather(*tasks, return_exceptions=True)

        for n in news_list:
            self.sent_cache[n.link] = datetime.now().isoformat()

        self.logger.info(f"[{topic}] processed {len(news_list)} news")
        return len(news_list)

    async def notify_admin(self, summary: dict):
        if not self.accounts:
            return
        admin = self.accounts[0]
        bot   = Bot(token=admin.token)
        text  = (
            f"📊 <b>NewsBot PRO — Laporan Harian</b>\n\n"
            f"📅 {datetime.now().strftime('%d %b %Y')}\n"
            f"📰 Berita diproses: {summary['total_news']}\n"
            f"✅ Terkirim: {summary['sent_news']}\n"
            f"❌ Gagal: {summary['failed']}\n"
            f"👥 Akun aktif: {len(self.accounts)}\n\n"
        )
        if self.failed_feeds:
            text += "⚠️ <b>Feed Bermasalah:</b>\n"
            for url, d in list(self.failed_feeds.items())[:5]:
                text += f"• {url[:55]}... ({d['count']}x)\n"
        try:
            await bot.send_message(chat_id=admin.chat_id, text=text, parse_mode=ParseMode.HTML)
        except Exception as e:
            self.logger.error(f"notify_admin: {e}")

    async def backup_data(self):
        try:
            os.makedirs(Config.BACKUP_DIR, exist_ok=True)
            import tarfile
            ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
            bfile = f"{Config.BACKUP_DIR}/backup_{ts}.tar.gz"
            files = [f for f in [
                Config.SENT_FILE, Config.BLACKLIST_FILE, Config.ACCOUNTS_FILE,
                Config.DATABASE_FILE, Config.STATS_FILE, "subscriptions.json",
                "payment_pending.json"
            ] if os.path.exists(f)]
            with tarfile.open(bfile,"w:gz") as tar:
                for f in files: tar.add(f)
            # Hapus backup lama (keep 7)
            backups = sorted(
                [f for f in os.listdir(Config.BACKUP_DIR) if f.startswith("backup_")]
            )
            for old in backups[:-7]:
                os.remove(os.path.join(Config.BACKUP_DIR, old))
            self.logger.info(f"Backup: {bfile}")
        except Exception as e:
            self.logger.error(f"backup: {e}")

    async def run(self):
        self.logger.info("=" * 50)
        self.logger.info("NewsBot PRO v3.0 Starting...")
        self.logger.info("=" * 50)

        cycle = 0
        while self.running:
            cycle += 1
            self.logger.info(f"=== Cycle {cycle} ===")
            try:
                topics = self.parse_opml()
                if not topics:
                    self.logger.warning("No feeds in OPML")
                    await asyncio.sleep(60)
                    continue

                total_news = 0
                for topic, feeds in topics.items():
                    if not self.running:
                        break
                    count = await self.process_topic(topic, feeds)
                    total_news += count
                    await asyncio.sleep(3)

                self._save_sent_cache()
                if Config.ENABLE_ANALYTICS:
                    self.analytics.save_stats()

                stats = self.analytics.get_daily_summary()
                summary = {
                    'total_news': total_news,
                    'sent_news':  stats['success'],
                    'failed':     stats['failed']
                }
                await self.notify_admin(summary)

                if Config.ENABLE_AUTO_BACKUP and cycle % 24 == 0:
                    asyncio.create_task(self.backup_data())

                self.logger.info(f"Cycle {cycle} done. News: {total_news}. Next in {Config.UPDATE_INTERVAL//60} min.")
                for _ in range(int(Config.UPDATE_INTERVAL / 60)):
                    if not self.running: break
                    await asyncio.sleep(60)

            except Exception as e:
                self.logger.error(f"Main loop error: {e}")
                await asyncio.sleep(60)

        self.logger.info("NewsBot stopped.")

# ========= ENTRY POINT =========
async def main():
    # Cek accounts.json
    if not os.path.exists('accounts.json'):
        print("❌ accounts.json tidak ditemukan!")
        print("Buat file accounts.json dengan format:")
        print('''[
  {
    "token": "BOT_TOKEN_DISINI",
    "chat_id": "ADMIN_CHAT_ID",
    "name": "Admin",
    "categories": ["technology","business","sports","entertainment","science","health","politik","militer","general","crypto"],
    "max_per_hour": 100,
    "is_active": true
  }
]''')
        return

    bot = NewsBot()
    await bot.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n✅ Bot stopped by user.")
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        sys.exit(1)
