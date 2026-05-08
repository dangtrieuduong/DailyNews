#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI & Cloud Daily Digest
=======================
Tự động tổng hợp tin tức AI & Cloud (toàn cầu + Việt Nam) từ RSS feeds,
dùng Groq API (miễn phí, OpenAI-compatible) để dịch/tóm tắt sang tiếng Việt,
rồi gửi email HTML.

Usage:
    python ai_cloud_digest.py

Schedule with cron (7 AM daily):
    0 7 * * * cd /path/to/folder && /usr/bin/python3 ai_cloud_digest.py >> digest.log 2>&1

Author: generated for Daniel (dangtrieuduong@gmail.com)
"""

from __future__ import annotations

import os
import ssl
import smtplib
import datetime as dt
import json
import logging
import hashlib
import re
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import feedparser
import requests
from dateutil import parser as date_parser
from dateutil.tz import tzutc, tzlocal

# ----------------------------------------------------------------------------
# CONFIG — có thể override bằng biến môi trường hoặc file .env
# ----------------------------------------------------------------------------

# Load .env nếu có
ENV_FILE = Path(__file__).parent / ".env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

def _parse_emails(raw: str) -> list[str]:
    """Tách chuỗi email ngăn cách bằng dấu phẩy hoặc chấm phẩy, bỏ whitespace rỗng."""
    if not raw:
        return []
    parts = raw.replace(";", ",").split(",")
    return [p.strip() for p in parts if p.strip()]


# Có thể set nhiều người nhận bằng cách ngăn cách dấu phẩy trong .env:
#   RECIPIENT=a@x.com, b@y.com, c@z.com
#   CC=manager@x.com
#   BCC=archive@x.com
RECIPIENTS = _parse_emails(os.environ.get("RECIPIENT", "duongdt56@fpt.com"))
CC_LIST = _parse_emails(os.environ.get("CC", ""))
BCC_LIST = _parse_emails(os.environ.get("BCC", ""))
EMAIL_METHOD = os.environ.get("EMAIL_METHOD", "smtp").lower()  # "smtp" | "resend"
MAX_ITEMS_PER_SECTION = int(os.environ.get("MAX_ITEMS_PER_SECTION", "6"))
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "48"))

# Anthropic Claude API — primary (nếu có key). Groq làm fallback.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
USE_CLAUDE_SUMMARY = ANTHROPIC_API_KEY != ""

# Groq API (miễn phí, OpenAI-compatible) — https://console.groq.com
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
USE_GROQ_SUMMARY = GROQ_API_KEY != ""

# SMTP (Gmail)
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")            # vd: yourname@gmail.com
SMTP_PASS = os.environ.get("SMTP_PASS", "")            # Gmail App Password (16 ký tự, không có khoảng trắng)

# Resend
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM = os.environ.get("RESEND_FROM", "onboarding@resend.dev")

# ----------------------------------------------------------------------------
# RSS SOURCES — phân loại sẵn
# ----------------------------------------------------------------------------
RSS_SOURCES = {
    "product": [  # 🚀 Sản phẩm & Công nghệ mới
        ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
        ("The Verge AI", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
        ("VentureBeat AI", "https://venturebeat.com/category/ai/feed/"),
        ("MIT Tech Review", "https://www.technologyreview.com/feed/"),
        ("AWS News", "https://aws.amazon.com/about-aws/whats-new/recent/feed/"),
        ("Google Cloud Blog", "https://cloudblog.withgoogle.com/rss/"),
        ("Azure Updates", "https://azurecomcdn.azureedge.net/en-us/updates/feed/"),
    ],
    "business": [  # 💰 Kinh doanh & Đầu tư
        ("TechCrunch Startups", "https://techcrunch.com/category/startups/feed/"),
        ("TechCrunch Venture", "https://techcrunch.com/category/venture/feed/"),
        ("Crunchbase News", "https://news.crunchbase.com/feed/"),
    ],
    "analysis": [  # 📊 Phân tích thị trường
        ("The New Stack", "https://thenewstack.io/feed/"),
        ("InfoQ AI/ML", "https://feed.infoq.com/ai-ml-data-eng/"),
        ("Wired Business", "https://www.wired.com/feed/category/business/latest/rss"),
    ],
    "vietnam": [  # 🇻🇳 AI/Cloud Việt Nam
        ("VnExpress Số hoá", "https://vnexpress.net/rss/so-hoa.rss"),
        ("GenK", "https://genk.vn/rss/home.rss"),
        ("ICTnews", "https://ictnews.vietnamnet.vn/rss/cong-nghe.rss"),
        ("Thanh Niên Công nghệ", "https://thanhnien.vn/rss/cong-nghe.rss"),
    ],
}

# Từ khoá để lọc bài có liên quan tới AI/Cloud (case-insensitive)
KEYWORDS = [
    "ai", "artificial intelligence", "trí tuệ nhân tạo", "machine learning",
    "llm", "gpt", "claude", "gemini", "openai", "anthropic", "deepmind",
    "cloud", "aws", "azure", "gcp", "google cloud", "kubernetes", "data center",
    "nvidia", "gpu", "h100", "h200", "b200", "tpu",
    "fpt", "viettel", "vng", "vnpt", "momo", "tiki", "zalo",
    "chuyển đổi số", "điện toán đám mây", "startup",
]

SECTION_META = {
    "vietnam":  ("🇻🇳 AI & Cloud tại Việt Nam",  "#c62828"),
    "product":  ("🚀 Sản phẩm & Công nghệ mới", "#1a73e8"),
    "business": ("💰 Kinh doanh & Đầu tư",      "#2e7d32"),
    "analysis": ("📊 Phân tích & Xu hướng",      "#ef6c00"),
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"}
RSS_FETCH_TIMEOUT = int(os.environ.get("RSS_FETCH_TIMEOUT", "20"))
HEAD_TIMEOUT = int(os.environ.get("HEAD_TIMEOUT", "5"))
GROQ_BATCH_SIZE = int(os.environ.get("GROQ_BATCH_SIZE", "6"))
# Chờ giữa các batch để không vượt Groq TPM (tokens/minute) — mặc định 3s ~ 20 req/phút
GROQ_DELAY_BETWEEN_BATCHES = float(os.environ.get("GROQ_DELAY_BETWEEN_BATCHES", "3"))
# Trần thời gian chờ khi server trả Retry-After quá lớn (phòng edge-case 1 giờ)
GROQ_MAX_BACKOFF_SECONDS = float(os.environ.get("GROQ_MAX_BACKOFF_SECONDS", "60"))
API_MAX_RETRIES = int(os.environ.get("API_MAX_RETRIES", "3"))
API_RETRY_DELAY_SECONDS = float(os.environ.get("API_RETRY_DELAY_SECONDS", "2"))
STATE_FILE = Path(__file__).parent / "last_run.txt"


# ----------------------------------------------------------------------------
# STEP 1 — Fetch RSS
# ----------------------------------------------------------------------------
def is_relevant(title: str, summary: str) -> bool:
    text = f"{title} {summary}".lower()
    for kw in KEYWORDS:
        pattern = rf"\b{re.escape(kw.lower())}\b"
        if re.search(pattern, text):
            return True
    return False


def is_valid_url(url: str) -> bool:
    try:
        response = requests.head(
            url,
            timeout=HEAD_TIMEOUT,
            allow_redirects=True,
            headers=REQUEST_HEADERS,
        )
        return response.status_code < 400
    except requests.RequestException as e:
        log.warning("HEAD check failed for %s: %s", url, e)
        return False


def fetch_feed(url: str):
    response = requests.get(url, timeout=RSS_FETCH_TIMEOUT, headers=REQUEST_HEADERS)
    response.raise_for_status()
    return feedparser.parse(response.content)


def parse_date(entry) -> dt.datetime | None:
    """Trả về datetime tz-aware (UTC) hoặc None."""
    for attr in ("published", "updated", "created"):
        val = entry.get(attr)
        if val:
            try:
                d = date_parser.parse(val)
                if d.tzinfo is None:
                    d = d.replace(tzinfo=tzutc())
                return d.astimezone(tzutc())
            except Exception:
                pass
    # Thử parsed struct
    for attr in ("published_parsed", "updated_parsed"):
        val = entry.get(attr)
        if val:
            try:
                return dt.datetime(*val[:6], tzinfo=tzutc())
            except Exception:
                pass
    return None


def fetch_section(section: str) -> list[dict]:
    cutoff = dt.datetime.now(tz=tzutc()) - dt.timedelta(hours=LOOKBACK_HOURS)
    items = []
    for source_name, url in RSS_SOURCES[section]:
        try:
            log.info("Fetching %s ...", source_name)
            feed = fetch_feed(url)
            for entry in feed.entries[:30]:
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                summary = entry.get("summary", "") or entry.get("description", "")
                date = parse_date(entry)
                if not title or not link:
                    continue
                if date and date < cutoff:
                    continue
                if not is_valid_url(link):
                    continue
                # chỉ lọc keyword cho section "analysis", "business" & Việt Nam
                # product feeds đã chuyên về AI/Cloud rồi, giữ nguyên
                if section in ("business", "analysis", "vietnam") and not is_relevant(title, summary):
                    continue
                items.append({
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "source": source_name,
                    "date": date,
                })
        except Exception as e:
            log.warning("Failed %s: %s", source_name, e)
    # Sort mới → cũ, dedupe theo title + link, cắt về MAX
    items.sort(key=lambda x: x["date"] or dt.datetime.min.replace(tzinfo=tzutc()), reverse=True)
    seen_items = set()
    unique = []
    for it in items:
        key = hashlib.md5(f"{it['title']}|{it['link']}".lower().encode("utf-8")).hexdigest()
        if key in seen_items:
            continue
        seen_items.add(key)
        unique.append(it)
    return unique[:MAX_ITEMS_PER_SECTION]


# ----------------------------------------------------------------------------
# STEP 2 — Dịch & tóm tắt sang tiếng Việt bằng Groq API
#   - Chỉ dùng Groq (miễn phí, OpenAI-compatible) — https://console.groq.com
#   - Nếu không có GROQ_API_KEY → giữ nguyên tiêu đề/summary gốc (tiếng Anh)
# ----------------------------------------------------------------------------
def _build_summary_prompt(batch_items: list[dict]) -> str:
    """Prompt dùng cho Groq (hoặc bất kỳ LLM nào có OpenAI-compatible API)."""
    return (
        "Bạn là biên tập viên công nghệ người Việt. Với mỗi bài dưới đây, "
        "hãy dịch tiêu đề sang tiếng Việt tự nhiên và viết tóm tắt 2-3 câu bằng tiếng Việt.\n"
        "Trả về JSON array với các field: id, title_vi, summary_vi.\n"
        "KHÔNG bịa thông tin, chỉ dùng những gì có trong raw. Nếu raw trống, "
        "viết summary ngắn dựa trên tiêu đề.\n\n"
        f"Bài viết:\n{json.dumps(batch_items, ensure_ascii=False, indent=2)}\n\n"
        "Chỉ trả về JSON, không kèm giải thích:"
    )


def _apply_summaries(summaries: dict, item_refs: dict[str, dict]) -> None:
    """Gán title_vi / summary_vi từ response LLM vào các item gốc."""
    for item_id, s in summaries.items():
        if item_id in item_refs:
            item_refs[item_id]["title_vi"] = s.get("title_vi", item_refs[item_id]["title"])
            item_refs[item_id]["summary_vi"] = s.get("summary_vi", "")


def summarize_with_groq(items_by_section: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Gọi Groq API (miễn phí, OpenAI-compatible) để dịch + tóm tắt tiếng Việt.

    - Nếu không có GROQ_API_KEY → log warning, giữ nguyên tiêu đề/summary gốc.
    - Batch thất bại sau retry → log warning, item đó giữ tiếng Anh.
    """
    if not USE_GROQ_SUMMARY:
        log.warning("Không có GROQ_API_KEY → giữ nguyên tiêu đề/summary gốc (tiếng Anh).")
        return items_by_section

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    payload_items = []
    item_refs: dict[str, dict] = {}
    for section, items in items_by_section.items():
        for idx, it in enumerate(items):
            raw = re.sub(r"<[^>]+>", " ", it["summary"] or "")[:500]
            item_id = f"{section}-{idx}"
            payload_items.append({"id": item_id, "title": it["title"], "raw": raw})
            item_refs[item_id] = it

    if not payload_items:
        return items_by_section

    # Groq free tier giới hạn 30 req/phút + input token limit thấp → batch nhỏ (mặc định 6)
    total_batches = (len(payload_items) + GROQ_BATCH_SIZE - 1) // GROQ_BATCH_SIZE
    for batch_idx, start_idx in enumerate(range(0, len(payload_items), GROQ_BATCH_SIZE)):
        batch = payload_items[start_idx:start_idx + GROQ_BATCH_SIZE]
        prompt = _build_summary_prompt(batch)
        success = False
        for attempt in range(1, API_MAX_RETRIES + 1):
            try:
                log.info("Calling Groq (%s) batch %d-%d (attempt %d/%d)...",
                         GROQ_MODEL, start_idx + 1, start_idx + len(batch),
                         attempt, API_MAX_RETRIES)
                r = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers,
                    json={
                        "model": GROQ_MODEL,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.3,
                        "max_tokens": 3000,
                        "response_format": {"type": "json_object"},
                    },
                    timeout=120,
                )
                r.raise_for_status()
                content = r.json()["choices"][0]["message"]["content"].strip()
                # Groq đôi khi wrap trong {...} thay vì [...]
                m = re.search(r"\[.*\]", content, re.DOTALL)
                if m:
                    parsed = json.loads(m.group(0))
                else:
                    obj = json.loads(content)
                    # Nếu trả về {"items": [...]} hoặc {"results": [...]}
                    parsed = obj if isinstance(obj, list) else next(
                        (v for v in obj.values() if isinstance(v, list)), []
                    )
                summaries = {s["id"]: s for s in parsed if "id" in s}
                _apply_summaries(summaries, item_refs)
                success = True
                break
            except requests.HTTPError as e:
                # Rate limit: đọc Retry-After header để chờ đúng thời gian server yêu cầu
                retry_wait = API_RETRY_DELAY_SECONDS * attempt
                status = e.response.status_code if e.response is not None else 0
                if status == 429 and e.response is not None:
                    ra = (e.response.headers.get("Retry-After")
                          or e.response.headers.get("retry-after"))
                    if ra:
                        try:
                            retry_wait = min(float(ra), GROQ_MAX_BACKOFF_SECONDS)
                        except ValueError:
                            pass  # Retry-After có thể là HTTP-date; giữ backoff cứng
                    log.warning("Groq 429 rate-limited batch %d-%d attempt %d/%d → chờ %.1fs",
                                start_idx + 1, start_idx + len(batch),
                                attempt, API_MAX_RETRIES, retry_wait)
                else:
                    log.warning("Groq HTTP %d batch %d-%d attempt %d/%d: %s",
                                status, start_idx + 1, start_idx + len(batch),
                                attempt, API_MAX_RETRIES, e)
                if attempt < API_MAX_RETRIES:
                    time.sleep(retry_wait)
            except Exception as e:
                log.warning("Groq failed batch %d-%d on attempt %d/%d: %s",
                            start_idx + 1, start_idx + len(batch),
                            attempt, API_MAX_RETRIES, e)
                if attempt < API_MAX_RETRIES:
                    time.sleep(API_RETRY_DELAY_SECONDS * attempt)

        if not success:
            log.warning("Batch %d-%d failed hẳn sau %d retry → giữ tiếng Anh.",
                        start_idx + 1, start_idx + len(batch), API_MAX_RETRIES)

        # Throttle proactively giữa các batch để tránh dính 429 lần sau
        if batch_idx < total_batches - 1 and GROQ_DELAY_BETWEEN_BATCHES > 0:
            time.sleep(GROQ_DELAY_BETWEEN_BATCHES)

    return items_by_section


def summarize_with_claude(items_by_section: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Gọi Anthropic Claude API để dịch + tóm tắt tiếng Việt.

    - Nếu không có ANTHROPIC_API_KEY → log warning, giữ nguyên tiêu đề/summary gốc.
    - Batch thất bại sau retry → log warning, item đó giữ tiếng Anh.
    """
    if not USE_CLAUDE_SUMMARY:
        log.warning("Không có ANTHROPIC_API_KEY → fallback sang Groq.")
        return summarize_with_groq(items_by_section)

    try:
        import anthropic as _anthropic
    except ImportError:
        log.warning("Thư viện 'anthropic' chưa cài → chạy: pip install anthropic. Fallback sang Groq.")
        return summarize_with_groq(items_by_section)

    client = _anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    payload_items = []
    item_refs: dict[str, dict] = {}
    for section, items in items_by_section.items():
        for idx, it in enumerate(items):
            raw = re.sub(r"<[^>]+>", " ", it["summary"] or "")[:500]
            item_id = f"{section}-{idx}"
            payload_items.append({"id": item_id, "title": it["title"], "raw": raw})
            item_refs[item_id] = it

    if not payload_items:
        return items_by_section

    batch_size = int(os.environ.get("GROQ_BATCH_SIZE", "6"))
    total_batches = (len(payload_items) + batch_size - 1) // batch_size
    for batch_idx, start_idx in enumerate(range(0, len(payload_items), batch_size)):
        batch = payload_items[start_idx:start_idx + batch_size]
        prompt = _build_summary_prompt(batch)
        success = False
        for attempt in range(1, API_MAX_RETRIES + 1):
            try:
                log.info("Calling Claude (%s) batch %d-%d (attempt %d/%d)...",
                         ANTHROPIC_MODEL, start_idx + 1, start_idx + len(batch),
                         attempt, API_MAX_RETRIES)
                msg = client.messages.create(
                    model=ANTHROPIC_MODEL,
                    max_tokens=3000,
                    messages=[{"role": "user", "content": prompt}],
                )
                content = msg.content[0].text.strip()
                m = re.search(r"\[.*\]", content, re.DOTALL)
                if m:
                    parsed = json.loads(m.group(0))
                else:
                    obj = json.loads(content)
                    parsed = obj if isinstance(obj, list) else next(
                        (v for v in obj.values() if isinstance(v, list)), []
                    )
                summaries = {s["id"]: s for s in parsed if "id" in s}
                _apply_summaries(summaries, item_refs)
                success = True
                break
            except Exception as e:
                log.warning("Claude failed batch %d-%d on attempt %d/%d: %s",
                            start_idx + 1, start_idx + len(batch),
                            attempt, API_MAX_RETRIES, e)
                if attempt < API_MAX_RETRIES:
                    time.sleep(API_RETRY_DELAY_SECONDS * attempt)

        if not success:
            log.warning("Batch %d-%d failed hẳn sau %d retry → giữ tiếng Anh.",
                        start_idx + 1, start_idx + len(batch), API_MAX_RETRIES)

        if batch_idx < total_batches - 1:
            time.sleep(1)

    return items_by_section


# ----------------------------------------------------------------------------
# STEP 3 — Render HTML
# ----------------------------------------------------------------------------
def render_html(items_by_section: dict[str, list[dict]]) -> str:
    today_vi = dt.datetime.now(tz=tzlocal()).strftime("%d/%m/%Y")
    weekday_vn = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
    weekday = weekday_vn[dt.datetime.now().weekday()]

    # TL;DR: lấy tin đầu của mỗi section
    tldr_items = []
    for section in ["vietnam", "product", "business", "analysis"]:
        items = items_by_section.get(section, [])
        if items:
            title = items[0].get("title_vi") or items[0]["title"]
            tldr_items.append(f'<li style="margin-bottom:6px;font-size:14px;">{title}</li>')

    tldr_html = "\n".join(tldr_items) if tldr_items else "<li>Không có tin mới.</li>"

    sections_html = []
    for section, (section_title, color) in SECTION_META.items():
        items = items_by_section.get(section, [])
        if not items:
            continue
        rows = []
        for it in items:
            title = it.get("title_vi") or it["title"]
            summary = it.get("summary_vi") or _strip_html(it.get("summary", ""))[:220]
            date_str = it["date"].astimezone(tzlocal()).strftime("%d/%m %H:%M") if it.get("date") else ""
            rows.append(f"""
            <div style="margin-bottom:20px;">
              <h3 style="margin:0 0 4px;font-size:16px;line-height:1.35;">
                <a href="{it['link']}" target="_blank" style="color:{color};text-decoration:none;">{title}</a>
              </h3>
              <div style="font-size:12px;color:#888;margin-bottom:6px;">{it['source']} · {date_str}</div>
              <p style="font-size:14px;color:#444;margin:0;">{summary}</p>
            </div>""")
        sections_html.append(f"""
        <h2 style="font-size:20px;color:{color};border-bottom:2px solid #e3ecf9;padding-bottom:8px;margin:32px 0 16px;">{section_title}</h2>
        {"".join(rows)}""")

    return f"""<!DOCTYPE html>
<html lang="vi"><head><meta charset="UTF-8"><title>Bản tin AI & Cloud — {today_vi}</title></head>
<body style="margin:0;padding:0;background:#f5f7fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#333;line-height:1.6;">
<div style="max-width:680px;margin:0 auto;padding:24px 16px;">
  <div style="background:#fff;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,0.06);overflow:hidden;">
    <!-- Header: background-color làm fallback cho email client không hỗ trợ gradient (Outlook, Yahoo) -->
    <div style="background-color:#1a73e8;background-image:linear-gradient(135deg,#1a73e8 0%,#0d47a1 100%);color:#ffffff;padding:32px 28px;text-align:center;">
      <h1 style="margin:0 0 6px;font-size:26px;color:#ffffff;-webkit-text-fill-color:#ffffff;">📰 Bản tin AI &amp; Cloud</h1>
      <div style="font-size:14px;opacity:.95;color:#ffffff;-webkit-text-fill-color:#ffffff;">{weekday}, {today_vi}</div>
    </div>
    <div style="padding:28px;">
      <div style="background:#eef5ff;border-left:4px solid #1a73e8;padding:16px 20px;border-radius:8px;margin-bottom:28px;">
        <h2 style="margin:0 0 10px;font-size:16px;color:#1a73e8;">⚡ TL;DR — Điểm nổi bật</h2>
        <ul style="margin:0;padding-left:20px;">{tldr_html}</ul>
      </div>
      {"".join(sections_html)}
    </div>
    <div style="padding:20px 28px;background:#f9fafc;font-size:12px;color:#888;text-align:center;border-top:1px solid #eee;">
      Bản tin tự động · Tạo lúc {dt.datetime.now(tz=tzlocal()).strftime("%H:%M %d/%m/%Y")}
    </div>
  </div>
</div>
</body></html>"""


def _strip_html(html: str) -> str:
    import re
    return re.sub(r"<[^>]+>", " ", html or "").replace("&nbsp;", " ").strip()


# ----------------------------------------------------------------------------
# STEP 4 — Send email
# ----------------------------------------------------------------------------

def post_with_retry(url: str, *, headers: dict, json_payload: dict, timeout: int):
    last_error = None
    for attempt in range(1, API_MAX_RETRIES + 1):
        try:
            response = requests.post(url, headers=headers, json=json_payload, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as e:
            last_error = e
            log.warning("POST failed for %s on attempt %d/%d: %s", url, attempt, API_MAX_RETRIES, e)
            if attempt < API_MAX_RETRIES:
                time.sleep(API_RETRY_DELAY_SECONDS * attempt)
    raise last_error

def send_via_smtp(html: str, subject: str) -> None:
    if not (SMTP_USER and SMTP_PASS):
        raise RuntimeError("SMTP_USER / SMTP_PASS chưa được set trong .env")
    if not RECIPIENTS:
        raise RuntimeError("Chưa có người nhận nào (set RECIPIENT trong .env)")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = ", ".join(RECIPIENTS)
    if CC_LIST:
        msg["Cc"] = ", ".join(CC_LIST)
    # BCC không ghi vào header — mail server sẽ gửi silently
    msg.attach(MIMEText("Vui lòng xem bản HTML.", "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    all_rcpts = RECIPIENTS + CC_LIST + BCC_LIST
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as server:
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, all_rcpts, msg.as_string())
    log.info("✅ Sent via SMTP → To: %s | Cc: %s | Bcc: %d người",
             ", ".join(RECIPIENTS), ", ".join(CC_LIST) or "(none)", len(BCC_LIST))


def send_via_resend(html: str, subject: str) -> None:
    if not RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY chưa được set trong .env")
    if not RECIPIENTS:
        raise RuntimeError("Chưa có người nhận nào (set RECIPIENT trong .env)")

    payload = {
        "from": RESEND_FROM,
        "to": RECIPIENTS,
        "subject": subject,
        "html": html,
    }
    if CC_LIST:
        payload["cc"] = CC_LIST
    if BCC_LIST:
        payload["bcc"] = BCC_LIST

    r = post_with_retry(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json_payload=payload,
        timeout=30,
    )
    log.info("✅ Sent via Resend → To: %s | Cc: %s | Bcc: %d người | id: %s",
             ", ".join(RECIPIENTS), ", ".join(CC_LIST) or "(none)",
             len(BCC_LIST), r.json().get("id"))



def should_run_now() -> bool:
    now = dt.datetime.now()
    today_7am = now.replace(hour=7, minute=0, second=0, microsecond=0)

    # Chưa tới 7h sáng thì chưa chạy
    if now < today_7am:
        return False

    # Chưa từng chạy lần nào thì chạy
    if not STATE_FILE.exists():
        return True

    try:
        last_run = dt.datetime.fromisoformat(STATE_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        # File state lỗi thì cho chạy lại để không bị miss
        return True

    # Nếu lần chạy gần nhất trước mốc 7h sáng hôm nay => hôm nay chưa chạy
    return last_run < today_7am


def mark_run_complete() -> None:
    STATE_FILE.write_text(dt.datetime.now().isoformat(), encoding="utf-8")


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------
def main() -> None:
    if not should_run_now():
        log.info("Skip run: chưa tới 7h sáng hoặc hôm nay đã chạy rồi.")
        return

    log.info("=== AI & Cloud Daily Digest ===")
    items_by_section = {sec: fetch_section(sec) for sec in RSS_SOURCES}
    total = sum(len(v) for v in items_by_section.values())
    log.info("Collected %d articles total.", total)

    if total == 0:
        log.warning("No articles found — skip sending email.")
        mark_run_complete()
        return

    # Dịch + tóm tắt tiếng Việt: Claude (primary) → Groq (fallback) → tiếng Anh.
    if USE_CLAUDE_SUMMARY:
        items_by_section = summarize_with_claude(items_by_section)
    else:
        items_by_section = summarize_with_groq(items_by_section)
    html = render_html(items_by_section)

    # Lưu bản sao để debug
    out = Path(__file__).parent / f"digest-{dt.date.today().isoformat()}.html"
    out.write_text(html, encoding="utf-8")
    log.info("Saved preview: %s", out)

    subject = f"📰 Bản tin AI & Cloud — {dt.datetime.now().strftime('%d/%m/%Y')}"

    if EMAIL_METHOD == "resend":
        send_via_resend(html, subject)
    else:
        send_via_smtp(html, subject)

    mark_run_complete()


if __name__ == "__main__":
    main()
