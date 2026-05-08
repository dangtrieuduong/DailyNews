# AI & Cloud Daily Digest — Hướng dẫn cài đặt

Script Python tự động tổng hợp tin AI/Cloud mỗi ngày và gửi email HTML.

## 1. Cài Python packages

```bash
pip install -r requirements.txt
```

Hoặc nếu macOS/Linux dùng system Python:
```bash
pip install --break-system-packages -r requirements.txt
```

## 2. Cấu hình credentials

```bash
cp .env.example .env
```

Mở file `.env` và điền thông tin. Có 2 cách gửi email:

### Cách A — Gmail SMTP (đơn giản nhất)

1. Vào https://myaccount.google.com/security → bật **2-Step Verification**
2. Vào https://myaccount.google.com/apppasswords → tạo App Password mới (chọn "Mail" / "Other")
3. Copy 16 ký tự (bỏ khoảng trắng) vào `SMTP_PASS`
4. Set `SMTP_USER=email_của_bạn@gmail.com`
5. Set `EMAIL_METHOD=smtp`

### Cách B — Resend (nếu có domain riêng)

1. Đăng ký tại https://resend.com (free tier 3000 email/tháng)
2. Lấy API key từ dashboard → set `RESEND_API_KEY`
3. Set `EMAIL_METHOD=resend`
4. Để gửi từ địa chỉ riêng (vd `news@yourdomain.com`), verify domain trong Resend dashboard

### (Khuyến khích) Groq API — dịch/tóm tắt tiếng Việt MIỄN PHÍ

Groq cho free tier generous (30 req/phút). Không có key này thì script vẫn chạy nhưng tiêu đề/summary sẽ giữ nguyên tiếng Anh.

1. Đăng ký tại https://console.groq.com → API Keys
2. Tạo key mới → copy vào `.env`: `GROQ_API_KEY=gsk_...`
3. (Tuỳ chọn) đổi model: `GROQ_MODEL=llama-3.3-70b-versatile` (mặc định)

## 3. Chạy thử

```bash
python ai_cloud_digest.py
```

Script sẽ:
- Fetch RSS từ TechCrunch, The Verge, AWS, GCP, Azure, VnExpress, GenK, ICTnews...
- Lọc tin trong 48h, dedupe theo title
- Gọi Groq API để dịch tiêu đề + tóm tắt 2-3 câu tiếng Việt (nếu có `GROQ_API_KEY`)
- Render HTML và gửi email
- Lưu bản preview vào `digest-YYYY-MM-DD.html`

## 4. Lên lịch chạy tự động

### macOS / Linux — dùng cron

```bash
crontab -e
```

Thêm dòng (chạy 7h sáng mỗi ngày):
```cron
0 7 * * * cd /đường/dẫn/tới/folder && /usr/bin/python3 ai_cloud_digest.py >> digest.log 2>&1
```

### macOS — dùng launchd (đáng tin hơn cron khi máy ngủ)

Tạo file `~/Library/LaunchAgents/com.daniel.aidigest.plist`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.daniel.aidigest</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/đường/dẫn/ai_cloud_digest.py</string>
    </array>
    <key>WorkingDirectory</key><string>/đường/dẫn/folder</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>7</integer>
        <key>Minute</key><integer>0</integer>
    </dict>
    <key>StandardOutPath</key><string>/tmp/aidigest.log</string>
    <key>StandardErrorPath</key><string>/tmp/aidigest.err</string>
</dict>
</plist>
```

Sau đó:
```bash
launchctl load ~/Library/LaunchAgents/com.daniel.aidigest.plist
```

### Windows — Task Scheduler

1. Mở **Task Scheduler** → Create Basic Task
2. Trigger: Daily at 7:00 AM
3. Action: Start a program → `python.exe` với arguments `C:\path\to\ai_cloud_digest.py`
4. Conditions: bỏ tick "Start the task only if the computer is on AC power"

### GitHub Actions — chạy 24/7 không cần máy bật

Tạo file `.github/workflows/digest.yml`:
```yaml
name: Daily Digest
on:
  schedule:
    - cron: '0 0 * * *'   # 7h sáng VN = 0h UTC
  workflow_dispatch:
jobs:
  send:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -r requirements.txt
      - run: python ai_cloud_digest.py
        env:
          EMAIL_METHOD: ${{ secrets.EMAIL_METHOD }}
          SMTP_USER: ${{ secrets.SMTP_USER }}
          SMTP_PASS: ${{ secrets.SMTP_PASS }}
          RECIPIENT: ${{ secrets.RECIPIENT }}
          GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
```

Đẩy lên GitHub repo (private) và set secrets trong **Settings → Secrets and variables → Actions**.

## 5. Tuỳ chỉnh

- **Đổi nguồn RSS**: edit `RSS_SOURCES` trong `ai_cloud_digest.py`
- **Đổi từ khoá lọc**: edit `KEYWORDS`
- **Thay đổi giao diện email**: edit hàm `render_html()`
- **Đổi tần suất**: edit cron expression hoặc lookback hours

## Troubleshooting

| Lỗi | Cách sửa |
|---|---|
| `SMTPAuthenticationError` | App Password sai, hoặc chưa bật 2FA Gmail |
| Một số RSS feed lỗi | Bình thường — script bỏ qua và tiếp tục |
| Email vào Spam | Verify domain với Resend, hoặc thêm SPF/DKIM cho Gmail |
| Tin tiếng Anh không được dịch | Set `GROQ_API_KEY` trong .env (lấy free tại console.groq.com) |
| Groq trả về JSON lỗi | Giảm `GROQ_BATCH_SIZE` xuống 4 hoặc đổi `GROQ_MODEL` |
