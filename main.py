import os
import subprocess
import requests
import warnings
import re
import argparse
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

# Suppress urllib3 NotOpenSSLWarning for launchd logs
try:
    import urllib3
    warnings.filterwarnings("ignore", category=urllib3.exceptions.NotOpenSSLWarning)
except ImportError:
    pass

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = Path(
    os.environ.get(
        "TELEGRAM_ENV_FILE",
        str(Path.home() / ".hermes_local_automation/telegram.env"),
    )
).expanduser()
SCRIPT_PATH = PROJECT_ROOT / "mail_fetcher.applescript"
FIELD_DELIMITER = "__MAIL_DIGEST_FIELD__"
TARGET_EMAIL = "ertugrul@cetinkayalar.com"

TURKISH_MONTHS = {
    "ocak": 1,
    "şubat": 2,
    "mart": 3,
    "nisan": 4,
    "mayıs": 5,
    "haziran": 6,
    "temmuz": 7,
    "ağustos": 8,
    "eylül": 9,
    "ekim": 10,
    "kasım": 11,
    "aralık": 12,
}

ENGLISH_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

MONTHS = {**TURKISH_MONTHS, **ENGLISH_MONTHS}
MONTHS.update({
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10,
    "nov": 11, "dec": 12,
})
MONTH_PATTERN = "|".join(sorted(MONTHS, key=len, reverse=True))

MEETING_KEYWORDS = (
    "toplantı", "toplanti", "meeting", "appointment", "randevu", "görüşme",
    "gorusme", "etkinlik", "event", "conference", "konferans", "seminar",
    "seminar", "webinar", "interview", "mülakat", "mulakat", "invitation",
    "invite", "invited", "calendar", "takvim", "davetiye", "schedule",
    "planlama", "zoom", "webex", "google meet", "microsoft teams",
)

CALENDAR_MARKERS = (
    "when:", "where:", "organizer:", "attendees:", "join meeting",
    "join us", ".ics", "icalendar", "add to calendar", "takvime ekle",
)

TR_OUTPUT_MONTHS = (
    "", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
)

ISO_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>20\d{2})[./-](?P<month>\d{1,2})[./-](?P<day>\d{1,2})(?!\d)"
)
NUMERIC_DATE_RE = re.compile(
    r"(?<!\d)(?P<first>\d{1,2})[./-](?P<second>\d{1,2})"
    r"(?:[./-](?P<year>20\d{2}))?(?!\d)"
)
DAY_MONTH_DATE_RE = re.compile(
    rf"(?<!\w)(?P<day>\d{{1,2}})(?:st|nd|rd|th)?[\s,.-]+"
    rf"(?P<month>{MONTH_PATTERN})(?:[\s,.-]+(?P<year>20\d{{2}}))?(?!\w)",
    re.IGNORECASE,
)
MONTH_DAY_DATE_RE = re.compile(
    rf"(?<!\w)(?P<month>{MONTH_PATTERN})[\s,.-]+"
    rf"(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:[\s,.-]+(?P<year>20\d{{2}}))?(?!\w)",
    re.IGNORECASE,
)
TIME_WITH_COLON_RE = re.compile(
    r"(?<![\d.])(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*"
    r"(?P<ampm>a\.?m\.?|p\.?m\.?)?\b",
    re.IGNORECASE,
)
TIME_WITH_DOT_RE = re.compile(
    r"(?<![\d.])(?P<hour>\d{1,2})\.(?P<minute>\d{2})\s*"
    r"(?P<ampm>a\.?m\.?|p\.?m\.?)?\b",
    re.IGNORECASE,
)
TIME_WITH_AMPM_RE = re.compile(
    r"(?<!\w)(?P<hour>1[0-2]|[1-9])(?:[:.](?P<minute>\d{2}))?\s*"
    r"(?P<ampm>a\.?m\.?|p\.?m\.?)\b",
    re.IGNORECASE,
)
RELATIVE_DATE_RE = re.compile(r"\b(today|today's|tomorrow|bugün|yarın)\b", re.IGNORECASE)
WEEKDAY_NAMES = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "pazartesi": 0, "salı": 1, "sali": 1, "çarşamba": 2, "carsamba": 2,
    "perşembe": 3, "persembe": 3, "cuma": 4, "cumartesi": 5, "pazar": 6,
}
WEEKDAY_RE = re.compile(r"\b(" + "|".join(sorted(WEEKDAY_NAMES, key=len, reverse=True)) + r")\b", re.IGNORECASE)
_DEFAULT_RELATIVE_ANCHOR = object()

def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)

def load_env():
    env = {}
    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Env file not found at {ENV_FILE}")
    with open(ENV_FILE, "r") as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                env[k] = v.strip("'\"")
    return env

def sanitize(text):
    if text is None:
        return ""
    # Replace CR/LF/TAB with spaces
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    # Remove ASCII control characters (0-31) except space
    text = "".join(ch for ch in text if ord(ch) >= 32 or ch == " ")
    # Remove zero-width/invisible spam padding characters
    # \u200b: Zero Width Space, \u200c: Zero Width Non-Joiner, \u200d: Zero Width Joiner, \uFEFF: BOM
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    # Collapse repeated whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _safe_date(year, month, day):
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _date_hits(text, target_date, relative_date=_DEFAULT_RELATIVE_ANCHOR):
    hits = []

    if relative_date is _DEFAULT_RELATIVE_ANCHOR:
        relative_date = target_date

    def add_hit(match, year, month, day):
        if year is None:
            year = target_date.year
        parsed = _safe_date(int(year), int(month), int(day))
        if parsed is not None:
            hits.append({"date": parsed, "start": match.start(), "end": match.end()})

    for match in ISO_DATE_RE.finditer(text):
        add_hit(match, match.group("year"), match.group("month"), match.group("day"))

    for match in DAY_MONTH_DATE_RE.finditer(text):
        add_hit(
            match,
            match.group("year"),
            MONTHS[match.group("month").casefold()],
            match.group("day"),
        )

    for match in MONTH_DAY_DATE_RE.finditer(text):
        add_hit(
            match,
            match.group("year"),
            MONTHS[match.group("month").casefold()],
            match.group("day"),
        )

    for match in NUMERIC_DATE_RE.finditer(text):
        first = int(match.group("first"))
        second = int(match.group("second"))
        if first > 12 and second <= 12:
            day, month = first, second
        elif second > 12 and first <= 12:
            month, day = first, second
        else:
            # The user's locale is Turkish, so ambiguous numeric dates are D/M/Y.
            day, month = first, second
        add_hit(match, match.group("year"), month, day)

    for match in RELATIVE_DATE_RE.finditer(text):
        if relative_date is None:
            continue
        word = match.group(1).casefold()
        resolved_date = relative_date + timedelta(days=1) if word in {"tomorrow", "yarın"} else relative_date
        hits.append({"date": resolved_date, "start": match.start(), "end": match.end()})

    for match in WEEKDAY_RE.finditer(text):
        if relative_date is None:
            continue
        weekday = WEEKDAY_NAMES[match.group(1).casefold()]
        if weekday == relative_date.weekday():
            hits.append({"date": relative_date, "start": match.start(), "end": match.end()})

    unique = {}
    for hit in hits:
        unique[(hit["date"], hit["start"], hit["end"])] = hit
    return sorted(unique.values(), key=lambda hit: hit["start"])


def parse_received_date(value):
    """Parse the date string emitted by Apple Mail's date as string."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = sanitize(value)
    if not text:
        return None

    try:
        parsed = parsedate_to_datetime(text)
        if parsed is not None:
            return parsed.date()
    except (TypeError, ValueError, OverflowError):
        pass

    fallback = datetime.now().date()
    explicit_hits = _date_hits(text, fallback, relative_date=None)
    return explicit_hits[0]["date"] if explicit_hits else None


def _time_hits(text):
    matches = []
    for pattern in (TIME_WITH_COLON_RE, TIME_WITH_DOT_RE, TIME_WITH_AMPM_RE):
        matches.extend(pattern.finditer(text))

    unique = {}
    for match in matches:
        hour = int(match.group("hour"))
        minute = int(match.group("minute") or 0)
        ampm = (match.group("ampm") or "").replace(".", "").lower()

        if ampm:
            if hour == 12:
                hour = 0
            if ampm == "pm":
                hour += 12
        if hour > 23 or minute > 59:
            continue

        # Do not interpret the middle of a numeric date such as 2026.08.14 as a time.
        if match.start() > 0 and text[match.start() - 1].isdigit():
            continue
        if match.end() < len(text) and text[match.end()] == ".":
            following = text[match.end() + 1:match.end() + 5]
            if following.isdigit() and len(following) == 4:
                continue

        unique[(match.start(), match.end())] = {
            "start": match.start(),
            "end": match.end(),
            "minutes": hour * 60 + minute,
            "label": f"{hour:02d}:{minute:02d}",
        }
    return sorted(unique.values(), key=lambda item: item["start"])


def _time_for_date(text, date_hit):
    window_start = max(0, date_hit["start"] - 260)
    window_end = min(len(text), date_hit["end"] + 260)
    nearby = [
        item for item in _time_hits(text[window_start:window_end])
        if abs((item["start"] + window_start) - date_hit["start"]) <= 260
    ]
    if not nearby:
        return None

    for item in nearby:
        item["start"] += window_start
        item["end"] += window_start

    after_date = [item for item in nearby if item["start"] >= date_hit["end"]]
    selected = after_date[:2] if after_date else nearby[-2:]
    if not selected:
        return None

    label = selected[0]["label"]
    if len(selected) > 1:
        between = text[selected[0]["end"]:selected[1]["start"]].casefold()
        if re.search(r"[-–—]|\b(to|until|ile|kadar)\b", between):
            label = f"{label}–{selected[1]['label']}"
    return {"label": label, "minutes": selected[0]["minutes"]}


def _is_meeting_message(subject, content):
    haystack = f"{subject}\n{content}".casefold()
    return any(keyword in haystack for keyword in MEETING_KEYWORDS + CALENDAR_MARKERS)


def extract_meeting(record, target_date):
    subject = record.get("subject", "")
    content = record.get("content", record.get("snippet", ""))
    if not _is_meeting_message(subject, content):
        return None

    if "received_date" in record:
        received_date = record.get("received_date")
        if not isinstance(received_date, (date, datetime)):
            received_date = parse_received_date(received_date)
    elif "date" in record:
        received_date = parse_received_date(record.get("date"))
    else:
        # Preserve helper behavior for synthetic records without transport
        # metadata; Apple Mail records always carry date.
        received_date = target_date
    if isinstance(received_date, datetime):
        received_date = received_date.date()

    text = f"{subject}\n{content}"
    matching_dates = [
        hit for hit in _date_hits(text, target_date, relative_date=received_date)
        if hit["date"] == target_date
    ]
    if not matching_dates:
        return None

    candidates = []
    for date_hit in matching_dates:
        time_info = _time_for_date(text, date_hit)
        candidates.append((time_info is None, date_hit["start"], date_hit, time_info))

    _, _, _, time_info = min(candidates, key=lambda item: (item[0], item[1]))
    return {
        "subject": subject or "Başlıksız toplantı",
        "sender": record.get("sender", "") or "Bilinmeyen gönderen",
        "date": target_date,
        "time": time_info["label"] if time_info else "Saat belirtilmemiş",
        "sort_minutes": time_info["minutes"] if time_info else 24 * 60,
    }

def fetch_mail():
    try:
        result = subprocess.run(
            ["osascript", str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
            check=False,
        )
        
        # Combine stdout and stderr because 'log' in AppleScript often goes to stderr
        raw_output = result.stdout + "\n" + result.stderr
        
        records = []
        for line in raw_output.splitlines():
            line = line.strip()
            if not line or FIELD_DELIMITER not in line:
                continue
            
            # Remove AppleScript 'log:' prefix if present
            if line.startswith("log:"):
                line = line[4:].strip()
            
            parts = line.split(FIELD_DELIMITER)
            if len(parts) >= 6:
                received_text = sanitize(parts[4])
                records.append({
                    "account": sanitize(parts[0]),
                    "mailbox": sanitize(parts[1]),
                    "sender": sanitize(parts[2]),
                    "subject": sanitize(parts[3]),
                    "date": received_text,
                    "received_date": parse_received_date(received_text),
                    "content": sanitize(parts[5]),
                })

        if result.returncode != 0 and not records:
            log(f"AppleScript error: {sanitize(result.stderr)}")
            return None
        
        return records
    except Exception as e:
        log(f"Error fetching mail: {e}")
        return None

def send_telegram(message):
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        log("Missing Telegram credentials in env file.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = [message[i:i+3500] for i in range(0, len(message), 3500)]
    
    success = True
    for idx, chunk in enumerate(chunks):
        payload = {"chat_id": chat_id, "text": chunk}
        try:
            response = requests.post(url, json=payload, timeout=15)
            if response.status_code != 200:
                log(f"Telegram Error (Chunk {idx+1}/{len(chunks)}): {response.status_code}")
                success = False
        except Exception as e:
            log(f"Network error sending chunk {idx+1}: {e}")
            success = False
    return success

def format_date(target_date):
    return f"{target_date.day} {TR_OUTPUT_MONTHS[target_date.month]} {target_date.year}"


def format_digest(records, target_date=None):
    target_date = target_date or datetime.now().date()
    meetings = []
    for record in records:
        meeting = extract_meeting(record, target_date)
        if meeting:
            meetings.append(meeting)

    unique_meetings = {}
    for meeting in meetings:
        key = (meeting["date"], meeting["time"], meeting["subject"].casefold())
        unique_meetings[key] = meeting
    meetings = sorted(unique_meetings.values(), key=lambda item: (item["sort_minutes"], item["subject"].casefold()))

    date_label = format_date(target_date)
    if not meetings:
        return f"📅 {date_label}\nBugün toplantı yok."

    lines = [f"📅 Bugünkü toplantılar — {date_label}", ""]
    for meeting in meetings:
        subject = meeting["subject"][:100]
        sender = meeting["sender"][:80]
        lines.append(f"• {meeting['time']} — {subject}")
        lines.append(f"  Gönderen: {sender}")
        lines.append("")
    return "\n".join(lines).rstrip()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Fetch and format but do not send")
    args = parser.parse_args()

    log("START daily meeting digest")
    
    log("Fetching recent meeting candidates")
    records = fetch_mail()
    if records is None:
        log("Failed to retrieve data.")
        log("DONE daily meeting digest (FAILED)")
        return 1

    log(f"Processed {len(records)} messages for {TARGET_EMAIL}")

    today = datetime.now().date()
    message = format_digest(records, today)
    meeting_count = sum(1 for record in records if extract_meeting(record, today))
    log(f"Found {meeting_count} meetings for {today.isoformat()}")
    
    if args.dry_run:
        log("Dry-run mode: printing digest to stdout")
        print("\n--- DRY RUN DIGEST START ---\n")
        print(message)
        print("\n--- DRY RUN DIGEST END ---")
    else:
        log("Sending digest to Telegram")
        if send_telegram(message):
            log("Telegram send success")
        else:
            log("Telegram send failure")
            log("DONE daily meeting digest (FAILED)")
            return 1
        
    log("DONE daily meeting digest")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
