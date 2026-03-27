# 1. Pastikan berada di folder proyek
cd ~/NEWS_TELEGRAM

# 2. Buat file README.md (opsional, karena sudah ada)
echo "# Nova-News-Scraping - Telegram NewsBot PRO" > README.md

# 3. Buat file .gitignore (PENTING! supaya file sensitif tidak keupload)
cat > .gitignore << 'EOF'
# Python
__pycache__/
*.pyc
*.pyo

# Database files
*.db
*.db.bak

# JSON data files (sensitif)
accounts.json
subscriptions.json
payment_pending.json
pending_users.json
kick_log.json
blacklist.json
sent.json
stats.json

# Log files
*.log

# Backup folder
backups/

# OS files
.DS_Store
Thumbs.db
.idea/
.vscode/
EOF

# 4. Inisialisasi Git (jika belum)
git init

# 5. Tambahkan semua file
git add .

# 6. Buat commit
git commit -m "Initial commit: Nova-News-Scraping NewsBot PRO v3.0"

# 7. Set branch ke main
git branch -M main

# 8. Tambahkan remote (URL dari screenshot Anda)
git remote add origin https://github.com/dtimer356-coder/Nova-News-Scraping.git

# 9. Push ke GitHub (gunakan Personal Access Token sebagai password)
git push -u origin main
