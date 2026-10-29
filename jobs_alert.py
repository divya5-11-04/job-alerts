"""Free internship alert bot. Runs on GitHub Actions, alerts through Telegram.

Sources: Greenhouse + Lever company boards (public JSON) and Internshala.
Matching: keyword score, optional Gemini free-tier check on top.
"""
import html
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
SEEN_FILE = ROOT / "seen.json"
COMPANIES_FILE = ROOT / "companies.json"

MAX_AGE_HOURS = 48
MIN_KEYWORD_SCORE = 25      # raise to get fewer, tighter alerts
MAX_ALERTS_PER_RUN = 10     # leftovers go out on the next run
GEMINI_MIN_FIT = 50
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
RESUME_SUMMARY = os.getenv("RESUME_SUMMARY", "")  # kept as a secret, repo is public

# Edit these to match your resume. Higher number = stronger signal.
KEYWORDS = {
    "machine learning": 15, "deep learning": 15, "computer vision": 15,
    "generative ai": 12, "llm": 12, "llms": 12, "data science": 12,
    "nlp": 10, "pytorch": 10, "opencv": 10, "rag": 8, "langchain": 8,
    "agentic": 8, "agents": 5, "tensorflow": 8, "robotics": 8,
    "python": 8, "fastapi": 5, "react": 4, "node.js": 4, "full stack": 5,
    "full-stack": 5, "ai": 6, "ml": 6,
}
TITLE_MUST = ("intern", "trainee")
TITLE_BLOCK = ("senior", "staff", "principal", "director", "manager", "lead")

# Internshala category pages. Check these exist in your browser first.
INTERNSHALA_PAGES = [
    "machine-learning-internship",
    "artificial-intelligence-ai-internship",
    "data-science-internship",
    "python-django-internship",
]

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
UTC = timezone.utc


def parse_iso(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def clean_html(raw):
    return BeautifulSoup(html.unescape(raw or ""), "html.parser").get_text(" ", strip=True)


def keyword_score(text):
    text = text.lower()
    score, hits = 0, []
    for kw, weight in KEYWORDS.items():
        if re.search(rf"(?<![\w]){re.escape(kw)}(?![\w])", text):
            score += weight
            hits.append(kw)
    return min(score, 100), hits


# ---------- sources ----------

def fetch_greenhouse(slug):
    r = requests.get(
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
        params={"content": "true"}, headers=HEADERS, timeout=25,
    )
    r.raise_for_status()
    for j in r.json().get("jobs", []):
        yield {
            "id": f"gh-{j['id']}",
            "source": "greenhouse",
            "title": j.get("title", ""),
            "company": slug,
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
            "desc": clean_html(j.get("content")),
            "posted": parse_iso(j.get("first_published") or j.get("updated_at")),
        }


def fetch_lever(slug):
    r = requests.get(
        f"https://api.lever.co/v0/postings/{slug}",
        params={"mode": "json"}, headers=HEADERS, timeout=25,
    )
    r.raise_for_status()
    for j in r.json():
        created = j.get("createdAt")
        yield {
            "id": f"lv-{j['id']}",
            "source": "lever",
            "title": j.get("text", ""),
            "company": slug,
            "location": (j.get("categories") or {}).get("location", ""),
            "url": j.get("hostedUrl", ""),
            "desc": j.get("descriptionPlain", ""),
            "posted": datetime.fromtimestamp(created / 1000, tz=UTC) if created else None,
        }


def internshala_age_hours(text):
    t = text.lower()
    m = re.search(r"(\d+)\s*(minute|hour|day|week|month)s?\s+ago", t)
    if m:
        unit = {"minute": 1 / 60, "hour": 1, "day": 24, "week": 168, "month": 720}[m.group(2)]
        return int(m.group(1)) * unit
    if any(w in t for w in ("just now", "today", "few hours", "few minutes")):
        return 1
    return None


def fetch_internshala(slug):
    r = requests.get(f"https://internshala.com/internships/{slug}/", headers=HEADERS, timeout=25)
    r.raise_for_status()
    cards = BeautifulSoup(r.text, "html.parser").select("div.individual_internship")
    print(f"internshala/{slug}: {len(cards)} cards found")  # 0 here means selectors broke
    now = datetime.now(UTC)
    for c in cards:
        a = c.select_one("a.job-title-href") or c.select_one("a[href*='/internship/detail']")
        href = (a.get("href") if a else None) or c.get("data-href")
        if not href:
            continue
        age = internshala_age_hours(c.get_text(" ", strip=True))
        if age is None:
            continue
        company = c.select_one(".company-name")
        loc = c.select_one(".locations")
        yield {
            "id": f"is-{c.get('internshipid') or href}",
            "source": "internshala",
            "title": a.get_text(strip=True) if a else "Internship",
            "company": company.get_text(strip=True) if company else "",
            "location": loc.get_text(" ", strip=True) if loc else "",
            "url": "https://internshala.com" + href if href.startswith("/") else href,
            "desc": c.get_text(" ", strip=True),
            "posted": now - timedelta(hours=age),
        }


def safe(fn, arg):
    try:
        return list(fn(arg))
    except Exception as e:  # one bad company should not kill the run
        print(f"skip {fn.__name__}({arg}): {e}")
        return []


# ---------- optional Gemini check ----------

def gemini_fit(job):
    if not (GEMINI_API_KEY and RESUME_SUMMARY):
        return None, ""
    prompt = (
        f"Candidate:\n{RESUME_SUMMARY}\n\nJob: {job['title']} at {job['company']}\n"
        f"{job['desc'][:5000]}\n\nHow well does this internship fit the candidate? "
        "Reply in exactly this format and nothing else:\nFIT: <0-100> | <one short reason>"
    )
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
            headers={"x-goog-api-key": GEMINI_API_KEY},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=40,
        )
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        m = re.search(r"FIT:\s*(\d+)\s*\|\s*(.+)", text)
        return (int(m.group(1)), m.group(2).strip()) if m else (None, "")
    except Exception as e:
        print(f"gemini skipped: {e}")
        return None, ""


# ---------- alerts + state ----------

def send(text):
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        print("[dry run]\n" + text + "\n")
        return
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True},
        timeout=20,
    )
    r.raise_for_status()


def load_seen():
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(sorted(seen)[-5000:]))


def main():
    cfg = json.loads(COMPANIES_FILE.read_text())
    jobs = []
    for slug in cfg.get("greenhouse", []):
        jobs += safe(fetch_greenhouse, slug)
    for slug in cfg.get("lever", []):
        jobs += safe(fetch_lever, slug)
    for slug in INTERNSHALA_PAGES:
        jobs += safe(fetch_internshala, slug)
    print(f"fetched {len(jobs)} postings")

    seen = load_seen()
    cutoff = datetime.now(UTC) - timedelta(hours=MAX_AGE_HOURS)
    candidates, batch_ids = [], set()

    for job in jobs:
        if job["id"] in seen or job["id"] in batch_ids:
            continue
        batch_ids.add(job["id"])
        if not job["posted"] or job["posted"] < cutoff:
            continue
        title = job["title"].lower()
        if not any(w in title for w in TITLE_MUST) or any(w in title for w in TITLE_BLOCK):
            seen.add(job["id"])
            continue
        score, hits = keyword_score(f"{job['title']} {job['desc']}")
        # Internshala pages are already category-filtered, so skip the threshold there
        if job["source"] != "internshala" and score < MIN_KEYWORD_SCORE:
            seen.add(job["id"])
            continue
        job["score"], job["hits"] = score, hits
        candidates.append(job)

    candidates.sort(key=lambda j: j["score"], reverse=True)
    sent = 0
    for job in candidates:
        if sent >= MAX_ALERTS_PER_RUN:
            break  # not marked seen, so it goes out next run
        fit, reason = gemini_fit(job)
        seen.add(job["id"])
        if fit is not None and fit < GEMINI_MIN_FIT:
            continue
        age_h = int((datetime.now(UTC) - job["posted"]).total_seconds() // 3600)
        lines = [
            job["title"],
            f"{job['company']} | {job['location'] or 'location not listed'}",
            f"Keywords: {', '.join(job['hits'][:6]) or 'none'} ({job['score']}/100)",
        ]
        if fit is not None:
            lines.append(f"Fit: {fit}/100. {reason}")
        lines += [f"Posted ~{age_h}h ago", job["url"]]
        send("\n".join(lines))
        sent += 1

    save_seen(seen)
    print(f"alerts sent: {sent}, still queued: {max(len(candidates) - sent, 0)}")


if __name__ == "__main__":
    main()
