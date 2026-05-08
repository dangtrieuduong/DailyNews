#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI & Cloud Daily Digest
=======================
Tự động tổng hợp tin tức AI & Cloud (toàn cầu + Việt Nam) từ RSS feeds,
dùng Anthropic Claude để dịch/tóm tắt sang tiếng Việt, rồi gửi email HTML.

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

RECIPIENT = os.environ.get("RECIPIENT", "dangtrieuduong@gmail.com")
EMAIL_METHOD = os.environ.get("EMAIL_METHOD", "smtp").lower()  # "smtp" | "resend"
MAX_ITEMS_PER_SECTION = int(os.environ.get("MAX_ITEMS_PER_SECTION", "6"))
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "48"))
USE_CLAUDE_SUMMARY = os.environ.get("ANTHROPIC_API_KEY", "") != ""

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
    "product":  ("🚀 Sản phẩm & Công nghệ mới", "#1a73e8"),
    "business": ("💰 Kinh doanh & Đầu tư",      "#2e7d32"),
    "analysis": ("📊 Phân tích & Xu hướng",      "#ef6c00"),
    "vietnam":  ("🇻🇳 AI & Cloud tại Việt Nam",  "#c62828"),
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"}
RSS_FETCH_TIMEOUT = int(os.environ.get("RSS_FETCH_TIMEOUT", "20"))
HEAD_TIMEOUT = int(os.environ.get("HEAD_TIMEOUT", "5"))
CLAUDE_BATCH_SIZE = int(os.environ.get("CLAUDE_BATCH_SIZE", "10"))
API_MAX_RETRIES = int(os.environ.get("API_MAX_RETRIES", "3"))
API_RETRY_DELAY_SECONDS = float(os.environ.get("API_RETRY_DELAY_SECONDS", "2"))


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
# STEP 2 — Dịch & tóm tắt sang tiếng Việt
#   - Ưu tiên Anthropic API (chất lượng cao, tóm tắt 2-3 câu)
#   - Fallback Google Translate miễn phí (dịch nguyên văn tiêu đề + summary)
#   - Nếu không có cả hai → giữ nguyên tiếng Anh
# ----------------------------------------------------------------------------
def _is_vietnamese(text: str) -> bool:
    """Heuristic: chuỗi có dấu tiếng Việt thì không cần dịch."""
    vn_chars = "ăâđêôơưàảãáạằẳẵắặầẩẫấậèẻẽéẹềểễếệìỉĩíịòỏõóọồổỗốộờởỡớợùủũúụừửữứựỳỷỹýỵ"
    text_lower = text.lower()
    return any(c in text_lower for c in vn_chars)


def translate_with_google(items_by_section: dict[str, list[dict]]) -> bool:
    """Dịch từng item bằng deep-translator (Google Translate free). Trả về True nếu OK."""
    try:
        from deep_translator import GoogleTranslator
    except ImportError:
        log.info("deep-translator chưa cài → bỏ qua Google Translate. "
                 "Chạy: pip install deep-translator")
        return False

    translator = GoogleTranslator(source="auto", target="vi")
    count = 0
    for section, items in items_by_section.items():
        for it in items:
            try:
                title = it["title"]
                summary_raw = _strip_html(it.get("summary", ""))[:400]
                # Bỏ qua nếu đã là tiếng Việt
                if _is_vietnamese(title):
                    it["title_vi"] = title
                    it["summary_vi"] = summary_raw
                    continue
                it["title_vi"] = translator.translate(title) if title else title
                if summary_raw:
                    it["summary_vi"] = translator.translate(summary_raw[:450])
                count += 1
            except Exception as e:
                log.warning("Google Translate lỗi với '%s...': %s", it["title"][:50], e)
    log.info("✅ Dịch %d bài bằng Google Translate", count)
    return True


def summarize_with_claude(items_by_section: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Gọi Anthropic API để dịch + tóm tắt tiếng Việt cho mỗi bài (2-3 câu).
    Nếu không có key, fallback sang Google Translate."""
    if not USE_CLAUDE_SUMMARY:
        log.info("Không có ANTHROPIC_API_KEY → thử Google Translate (miễn phí)...")
        translate_with_google(items_by_section)
        return items_by_section

    api_key = os.environ["ANTHROPIC_API_KEY"]
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    payload_items = []
    item_refs: dict[str, dict] = {}
    for section, items in items_by_section.items():
        for idx, it in enumerate(items):
            raw = re.sub(r"<[^>]+>", " ", it["summary"] or "")[:500]
            item_id = f"{section}-{idx}"
            payload_items.append({
                "id": item_id,
                "title": it["title"],
                "raw": raw,
            })
            item_refs[item_id] = it

    if not payload_items:
        return items_by_section

    def build_prompt(batch_items: list[dict]) -> str:
        return (
            "Bạn là biên tập viên công nghệ. Với mỗi bài dưới đây, hãy viết tóm tắt "
            "2-3 câu bằng tiếng Việt tự nhiên (dịch tiêu đề và viết tóm tắt).\n"
            "Trả về JSON array với các field: id, title_vi, summary_vi.\n"
            "KHÔNG bịa thông tin, chỉ dùng những gì có trong raw. Nếu raw trống, "
            "viết summary ngắn dựa trên tiêu đề.\n\n"
            f"Bài viết:\n{json.dumps(batch_items, ensure_ascii=False, indent=2)}\n\n"
            "Chỉ trả về JSON, không kèm giải thích:"
        )

    any_success = False
    for start_idx in range(0, len(payload_items), CLAUDE_BATCH_SIZE):
        batch = payload_items[start_idx:start_idx + CLAUDE_BATCH_SIZE]
        prompt = build_prompt(batch)
        success = False
        for attempt in range(1, API_MAX_RETRIES + 1):
            try:
                log.info(
                    "Calling Anthropic API for batch %d-%d (attempt %d/%d)...",
                    start_idx + 1,
                    start_idx + len(batch),
                    attempt,
                    API_MAX_RETRIES,
                )
                r = requests.post(
                    "https://api.anthropic.com/v1/messages",
                    headers=headers,
                    json={
                        "model": "claude-haiku-4-5-20251001",
                        "max_tokens": 4000,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                    timeout=120,
                )
                r.raise_for_status()
                text = r.json()["content"][0]["text"].strip()
                m = re.search(r"\[.*\]", text, re.DOTALL)
                if m:
                    text = m.group(0)
                summaries = {s["id"]: s for s in json.loads(text)}

                for item_id, summary_data in summaries.items():
                    if item_id in item_refs:
                        item_refs[item_id]["title_vi"] = summary_data.get("title_vi", item_refs[item_id]["title"])
                        item_refs[item_id]["summary_vi"] = summary_data.get("summary_vi", "")
                success = True
                any_success = True
                break
            except Exception as e:
                log.warning("Claude summarize failed for batch %d-%d on attempt %d: %s",
                            start_idx + 1, start_idx + len(batch), attempt, e)
                if attempt < API_MAX_RETRIES:
                    time.sleep(API_RETRY_DELAY_SECONDS * attempt)

        if not success:
            log.warning("Fallback Google Translate for batch %d-%d", start_idx + 1, start_idx + len(batch))
            for batch_item in batch:
                item = item_refs[batch_item["id"]]
                item["summary"] = item.get("summary", "")
            translate_with_google({"fallback": [item_refs[b["id"]] for b in batch]})

    if not any_success:
        log.info("Anthropic không trả về batch nào thành công → giữ fallback/translated content.")

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
    for section in ["product", "business", "analysis", "vietnam"]:
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
    <div style="background:linear-gradient(135deg,#1a73e8 0%,#0d47a1 100%);color:#fff;padding:32px 28px;text-align:center;">
      <h1 style="margin:0 0 6px;font-size:26px;">📰 Bản tin AI &amp; Cloud</h1>
      <div style="font-size:14px;opacity:.9;">{weekday}, {today_vi}</div>
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
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = RECIPIENT
    msg.attach(MIMEText("Vui lòng xem bản HTML.", "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as server:
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, [RECIPIENT], msg.as_string())
    log.info("✅ Sent via SMTP to %s", RECIPIENT)


def send_via_resend(html: str, subject: str) -> None:
    if not RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY chưa được set trong .env")
    r = post_with_retry(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json_payload={
            "from": RESEND_FROM,
            "to": [RECIPIENT],
            "subject": subject,
            "html": html,
        },
        timeout=30,
    )
    log.info("✅ Sent via Resend: %s", r.json().get("id"))


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------
def main() -> None:
    log.info("=== AI & Cloud Daily Digest ===")
    items_by_section = {sec: fetch_section(sec) for sec in RSS_SOURCES}
    total = sum(len(v) for v in items_by_section.values())
    log.info("Collected %d articles total.", total)

    if total == 0:
        log.warning("No articles found — skip sending email.")
        return

    # Disable translation/summarization: keep original language from sources
    # items_by_section = summarize_with_claude(items_by_section)
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


if __name__ == "__main__":
    main()
