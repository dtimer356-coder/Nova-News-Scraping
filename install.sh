# Buat .gitignore
cat > .gitignore << 'EOF'
.env
newsbot_env/
*.log
__pycache__/
*.pyc
*.db.bak
EOF

# Init dan push
git init
git add .
git commit -m "first commit"
git remote add origin https://github.com/dtimer356-coder/NEWS_TELEGRAM.git
git branch -M main
git push -u origin main
