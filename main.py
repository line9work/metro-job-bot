# main.py

import os
import re
import logging
import hashlib
import urllib3
from time import sleep
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from config import BOT_TOKEN, CHAT_ID, URLS

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ======================================
# LOGGING
# ======================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8")
    ]
)

log = logging.getLogger(__name__)

# ======================================
# CONSTANTS
# ======================================

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

OLD_JOBS_FILE = "old.txt"
REQUEST_TIMEOUT = 40
RETRY_ATTEMPTS = 3
RETRY_DELAY = 3
MAX_JOBS_PER_SITE = 5

DATE_PATTERNS = [
    r"\d{2}-\d{2}-\d{4}",
    r"\d{2}\.\d{2}\.\d{4}",
    r"\d{2}/\d{2}/\d{4}",
    r"\d{1,2}\s+[A-Za-z]+\s+\d{4}"
]

GOOD_WORDS = [
    "recruitment",
    "vacancy",
    "notification",
    "advertisement",
    "post advertised",
    "post advertised for",
    "current openings",
    "current opening",
    "engineer",
    "manager",
    "assistant manager",
    "assistant",
    "executive",
    "officer",
    "technician",
    "maintainer",
    "supervisor",
    "signalling",
    "telecom",
    "electrical",
    "civil",
    "operations",
    "rolling stock",
    "deputation",
    "direct recruitment"
]

BAD_WORDS = [
    "contact us",
    "phone number",
    "helpline",
    "annual report",
    "project report",
    "mobile app",
    "ticket",
    "fare",
    "station",
    "metro map",
    "route map",
    "press release",
    "newsletter",
    "brochure",
    "photo gallery",
    "project summary",
    "about us",
    "gallery",
    "video gallery",
    "faq",
    "support",
    "how to apply",
    "disclaimer",
    "payment gateway",
    "application fee",
    "beware",
    "fake job",
    "general public",
    "service rules"
]


# ======================================
# TELEGRAM
# ======================================

def split_message(text, limit=3800):
    lines = text.split("\n")
    chunks = []
    current = ""

    for line in lines:
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            if current:
                current += "\n" + line
            else:
                current = line

    if current:
        chunks.append(current)

    return chunks


def send_message(text):
    if not BOT_TOKEN or not CHAT_ID:
        log.error("BOT_TOKEN / CHAT_ID missing")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    for chunk in split_message(text):
        try:
            requests.post(
                url,
                data={
                    "chat_id": CHAT_ID,
                    "text": chunk
                },
                timeout=15
            )
        except Exception as e:
            log.error(f"Telegram Error: {e}")


# ======================================
# FILES
# ======================================

def load_old_jobs():
    if not os.path.exists(OLD_JOBS_FILE):
        return set()

    with open(OLD_JOBS_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())


def save_jobs(jobs):
    with open(OLD_JOBS_FILE, "w", encoding="utf-8") as f:
        for item in sorted(jobs):
            f.write(item + "\n")


def normalize_text(text):
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def hash_job(site, text):
    raw = f"{site}::{normalize_text(text)}"
    return hashlib.md5(raw.encode()).hexdigest()


# ======================================
# FETCH PAGE
# ======================================

def fetch_page(url):
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
                verify=False
            )

            response.encoding = response.apparent_encoding
            response.raise_for_status()

            return BeautifulSoup(response.text, "html.parser")

        except Exception as e:
            log.warning(f"Attempt {attempt} failed: {url} -> {e}")

            if attempt < RETRY_ATTEMPTS:
                sleep(RETRY_DELAY)

    log.error(f"Failed after retries: {url}")
    return None


# ======================================
# DATE EXTRACTION
# ======================================

def extract_dates(text):
    found = []

    for pattern in DATE_PATTERNS:
        matches = re.findall(pattern, text)

        for m in matches:
            if m not in found:
                found.append(m)

    publish_date = "Not found"
    last_date = "Not found"

    if len(found) >= 1:
        publish_date = found[0]

    if len(found) >= 2:
        last_date = found[-1]

    return publish_date, last_date


def parse_date_for_sort(date_text):
    formats = [
        "%d-%m-%Y",
        "%d.%m.%Y",
        "%d/%m/%Y"
    ]

    for fmt in formats:
        try:
            return datetime.strptime(date_text, fmt)
        except:
            pass

    return datetime(2000, 1, 1)


# ======================================
# FILTER
# ======================================

def is_valid_job_text(text):
    text = text.strip()

    if len(text) < 20:
        return False

    if len(text) > 500:
        return False

    lower = text.lower()

    for bad in BAD_WORDS:
        if bad in lower:
            return False

    for good in GOOD_WORDS:
        if good in lower:
            return True

    return False


# ======================================
# CLEAN JOB TITLE
# ======================================

def extract_job_title(text):
    text = re.sub(r"\s+", " ", text).strip()

    keywords = [
        "manager",
        "engineer",
        "officer",
        "assistant manager",
        "executive",
        "supervisor",
        "technician",
        "maintainer"
    ]

    for word in keywords:
        if word.lower() in text.lower():
            return text[:200]

    return text[:150]


# ======================================
# EXTRACT JOBS
# ======================================

def get_jobs_from_url(site, url):
    soup = fetch_page(url)

    if not soup:
        return []

    jobs = []
    seen = set()

    for item in soup.find_all(["tr", "li", "a", "td", "p", "span", "div"]):
        text = item.get_text(" ", strip=True)

        if not is_valid_job_text(text):
            continue

        clean = normalize_text(text)

        if clean in seen:
            continue

        seen.add(clean)

        job_title = extract_job_title(text)
        publish_date, last_date = extract_dates(text)

        jobs.append({
            "title": job_title,
            "publish_date": publish_date,
            "last_date": last_date,
            "raw": text
        })

    jobs.sort(
        key=lambda x: parse_date_for_sort(x["publish_date"]),
        reverse=True
    )

    return jobs[:MAX_JOBS_PER_SITE]


# ======================================
# MESSAGE FORMAT
# ======================================

def build_message(grouped_jobs):
    now = datetime.now().strftime("%d-%m-%Y %I:%M %p")

    msg = "🚨 METRO JOB ALERT 🚨\n"
    msg += f"🕒 Updated: {now}\n\n"

    for site, jobs in grouped_jobs.items():
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"🏢 {site}\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n\n"

        for i, job in enumerate(jobs, start=1):
            msg += f"📌 Vacancy #{i}\n"
            msg += f"Post: {job['title']}\n"
            msg += f"Published: {job['publish_date']}\n"
            msg += f"Last Date to Apply: {job['last_date']}\n\n"

        msg += "\n"

    return msg


# ======================================
# MAIN
# ======================================

def main():
    log.info("Starting scraper...")

    old_hashes = load_old_jobs()
    current_hashes = set(old_hashes)
    grouped_jobs = {}

    for site, url in URLS.items():
        log.info(f"Checking: {site}")

        jobs = get_jobs_from_url(site, url)

        for job in jobs:
            unique_text = f"{job['title']} {job['publish_date']} {job['last_date']}"
            job_hash = hash_job(site, unique_text)

            current_hashes.add(job_hash)

            if job_hash not in old_hashes:
                if site not in grouped_jobs:
                    grouped_jobs[site] = []

                grouped_jobs[site].append(job)

    if grouped_jobs:
        message = build_message(grouped_jobs)
        send_message(message)
        log.info("Telegram alert sent")
    else:
        log.info("No new jobs found")

    save_jobs(current_hashes)
    log.info("Done")


if __name__ == "__main__":
    main()