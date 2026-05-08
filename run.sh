#!/bin/bash
# Quick-start script for AI & Cloud Daily Digest
# Usage: ./run.sh

set -e
cd "$(dirname "$0")"

echo "📂 Working folder: $(pwd)"
echo ""

# Rename .env.example -> .env if .env chưa tồn tại
if [ ! -f .env ] && [ -f .env.example ]; then
  echo "→ Đổi tên .env.example thành .env ..."
  mv .env.example .env
fi

# Cài dependencies
echo "→ Cài Python packages ..."
pip3 install --quiet --break-system-packages -r requirements.txt 2>/dev/null || \
  pip3 install --quiet -r requirements.txt

# Chạy script
echo "→ Bắt đầu tổng hợp tin tức ..."
echo ""
python3 ai_cloud_digest.py
