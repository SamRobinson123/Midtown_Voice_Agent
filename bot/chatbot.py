"""
UPFH Virtual Front‑Desk Chat‑Bot
────────────────────────────────
• OpenAI tool‑enabled assistant
• Google Calendar real‑time scheduling
• Sliding‑fee calculator
• Gmail confirmations
• TF‑IDF site search + summaries
"""

from __future__ import annotations
# ── stdlib ─────────────────────────────────────────────────────────────
import os, time, json, difflib, logging, base64, email.message, re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from textwrap import wrap
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import urljoin, urldefrag
# ── 3rd‑party ──────────────────────────────────────────────────────────
from dotenv import load_dotenv
import requests, bs4
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize
from openai import OpenAI
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials               # Gmail OAuth
from google.oauth2.service_account import Credentials as SA_Creds  # Calendar SA
# ── logging ────────────────────────────────────────────────────────────
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(levelname)s  %(message)s",
                    force=True)

# ══════════════════════════════════════════════════════════════════════
# 0 ► ENV / GLOBALS
# ══════════════════════════════════════════════════════════════════════
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or ""
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not set.")
openai = OpenAI(api_key=OPENAI_API_KEY)

FROM_ADDR = os.getenv("FROM_EMAIL", "samrobinson290225@gmail.com")
FROM_NAME = "Utah Partners for Health"

# ── Gmail creds ───────────────────────────────────────────────────────
GMAIL_TOKEN = os.getenv("GMAIL_TOKEN_PATH", "gmail_token.json").strip('"')
_GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

def _gmail_service():
    creds = Credentials.from_authorized_user_file(GMAIL_TOKEN, _GMAIL_SCOPES)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)

# ── Google Calendar creds ─────────────────────────────────────────────
from zoneinfo import ZoneInfo

_CAL_SCOPES = ["https://www.googleapis.com/auth/calendar"]
_CAL_ID     = os.getenv("UPFH_CALENDAR_ID", "primary")       # share clinic calendar
_TZ         = "America/Denver"

def _calendar_service():
    """
    Return an authorised Calendar API client.

    Priority:
    1. If GOOGLE_OAUTH_TOKEN is set → use OAuth user creds (personal calendar)
    2. Else use GOOGLE_CALENDAR_KEY service‑account creds (Workspace / robot)
    """
    oauth_path = os.getenv("GOOGLE_OAUTH_TOKEN")
    if oauth_path:
        if not Path(oauth_path).exists():
            raise RuntimeError(f"OAuth token not found at {oauth_path}")
        creds = Credentials.from_authorized_user_file(oauth_path, _CAL_SCOPES)
        logging.info("Calendar auth: OAuth user token")
    else:
        key_path = os.getenv("GOOGLE_CALENDAR_KEY")
        if not key_path or not Path(key_path).exists():
            raise RuntimeError("Neither GOOGLE_OAUTH_TOKEN nor GOOGLE_CALENDAR_KEY found.")
        creds = SA_Creds.from_service_account_file(key_path, scopes=_CAL_SCOPES)
        logging.info("Calendar auth: service‑account key")

    return build("calendar", "v3", credentials=creds, cache_discovery=False)

# ══════════════════════════════════════════════════════════════════════
# 1 ► E‑MAIL HELPER
# ══════════════════════════════════════════════════════════════════════
def send_gmail(
    to_list: List[str], subject: str, body: str,
    attachment: Optional[Tuple[str, bytes]] = None,
    sender: str = f"{FROM_NAME} <{FROM_ADDR}>",
) -> None:
    msg = email.message.EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, ", ".join(to_list), subject
    msg.set_content(body)
    if attachment:
        fname, data = attachment
        msg.add_attachment(data, maintype="application", subtype="octet-stream",
                           filename=fname)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    _gmail_service().users().messages().send(userId="me",
                                             body={"raw": raw}).execute()
    logging.info("Gmail sent → %s", to_list)

# ══════════════════════════════════════════════════════════════════════
# 2 ► GOOGLE CALENDAR HELPERS  (time‑zone safe + auto‑e‑mail)
# ══════════════════════════════════════════════════════════════════════
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta, timezone, date as dt_date
from typing import Dict, Any, List

_TZ_NAME = "America/Denver"          # clinic zone
_TZ      = ZoneInfo(_TZ_NAME)        # IANA object – handles DST automatically

def _make_iso(date_str: str, hhmm: str) -> str:
    """
    Convert YYYY‑MM‑DD and 'HH:MM' into an RFC‑3339 timestamp that
    carries the correct ‑06:00 / ‑07:00 offset depending on the date.
    """
    naive  = datetime.fromisoformat(f"{date_str}T{hhmm}:00")
    aware  = naive.replace(tzinfo=_TZ)            # localise
    return aware.isoformat(timespec="seconds")    # → '2025‑08‑04T09:00:00‑06:00'

def _iso2dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso)

def _iter_days(start: dt_date, end: dt_date):
    cur = start
    while cur <= end:
        yield cur.isoformat()
        cur += timedelta(days=1)

# ----------------------------------------------------------------------
def check_calendar_availability(
    date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    duration_minutes: int = 30,
    work_start: str = "08:00",
    work_end: str   = "17:30",
) -> Dict[str, Any]:
    """
    Returns ≤10 free slots (ISO) for a single day OR for every day in a range.
    Pass either `date` **or** both `start_date` & `end_date`.
    """
    # --- validate args -------------------------------------------------
    if bool(date) == bool(start_date or end_date):
        raise ValueError("Pass `date` OR (`start_date` and `end_date`).")
    if (start_date and not end_date) or (end_date and not start_date):
        raise ValueError("Both `start_date` and `end_date` are required.")

    # --- enumerate days ------------------------------------------------
    if date:
        days = [date]
    else:
        s = dt_date.fromisoformat(start_date)
        e = dt_date.fromisoformat(end_date)
        if e < s or (e - s).days > 30:
            raise ValueError("Bad date range (max 30 days).")
        days = list(_iter_days(s, e))

    svc = _calendar_service()
    free_by_day: Dict[str, List[str]] = {}

    for d in days:
        start_iso = _make_iso(d, work_start)
        end_iso   = _make_iso(d, work_end)

        fb_req = {
            "timeMin": start_iso,
            "timeMax": end_iso,
            "timeZone": _TZ_NAME,
            "items": [{"id": _CAL_ID}],
        }
        busy = svc.freebusy().query(body=fb_req).execute() \
                     ["calendars"][_CAL_ID]["busy"]
        busy = [(_iso2dt(b["start"]), _iso2dt(b["end"])) for b in busy]

        cursor, close = _iso2dt(start_iso), _iso2dt(end_iso)
        step, span = timedelta(minutes=15), timedelta(minutes=duration_minutes)
        slots = []
        while cursor + span <= close:
            s_end = cursor + span
            if not any(b0 < s_end and cursor < b1 for b0, b1 in busy):
                slots.append(cursor.isoformat())
            cursor += step
        if slots:
            free_by_day[d] = slots[:10]

    return {"free_slots": free_by_day}

# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
def create_calendar_event(
    patient_name: str,
    start: str,            # ISO with offset, e.g. 2025‑08‑04T09:00:00‑06:00
    end: str,              # ISO with offset
    email: str = "",
    phone: str = "",
    reason: str = ""
) -> Dict[str, Any]:
    """
    Books the slot in Google Calendar and returns booking details.
    Confirmation e‑mails will be sent separately by the
    `submit_appointment_request` tool.
    """
    svc = _calendar_service()
    evt = {
        "summary": f"{patient_name} – Clinic Visit",
        "description": f"Reason: {reason}\nPhone: {phone}\nE‑mail: {email}",
        "start": {"dateTime": start, "timeZone": _TZ_NAME},
        "end":   {"dateTime": end,   "timeZone": _TZ_NAME},
        "reminders": {"useDefault": True},
        "attendees": [{"email": email}] if email else [],
    }
    created = svc.events().insert(
        calendarId=_CAL_ID,
        body=evt,
        sendUpdates="all"      # patient gets Google invite if e‑mail provided
    ).execute()

    # Return everything the assistant needs to pass straight into
    # submit_appointment_request without re‑asking the caller.
    return {
        "status":        "booked",
        "event_id":      created["id"],
        "start":         start,
        "end":           end,
        "email":         email,
        "patient_name":  patient_name,
        "phone":         phone,
        "reason":        reason,
   "preferred_date": preferred_date    # ← keep just the date
    }

# ══════════════════════════════════════════════════════════════════════
# 3 ► TF‑IDF SEARCH & SUMMARY (trimmed but full‑function)
# ══════════════════════════════════════════════════════════════════════
MODEL = "gpt-4o-mini"
SEED_URLS = ["https://www.upfh.org/",
             "https://www.upfh.org/locations",
             "https://www.upfh.org/providers",
             "https://www.upfh.org/pharmacy",
             "https://www.upfh.org/dental"]
MAX_PAGES = 500
SITE_CACHE: Dict[str, str] = {}
VECT = DOC_EMB = DOC_URLS = None

def _clean(html_text: str) -> str:
    soup = bs4.BeautifulSoup(html_text, "html.parser")
    for t in soup(["script", "style", "noscript"]): t.decompose()
    txt = soup.get_text(" ", strip=True)
    return re.sub(r"\s{2,}", " ", txt)

def _get(url, tries=6, backoff=1.4):
    hdrs = {"User-Agent": "Mozilla/5.0"}
    for i in range(tries):
        try:  return requests.get(url, headers=hdrs, timeout=10)
        except requests.RequestException:
            if i == tries-1: raise
            time.sleep(backoff*(i+1))

def build_site_cache():
    q, seen = SEED_URLS.copy(), set()
    DOMAIN = "https://www.upfh.org/"
    while q and len(SITE_CACHE) < MAX_PAGES:
        url = q.pop(0)
        if url in seen: continue
        seen.add(url)
        try:
            r = _get(url)
            if r.status_code != 200 or not r.url.startswith(DOMAIN): continue
            SITE_CACHE[url] = _clean(r.text)
            soup = bs4.BeautifulSoup(r.text, "html.parser")
            for a in soup.select("a[href]"):
                link = urljoin(url, urldefrag(a["href"])[0])
                if link.startswith(DOMAIN): q.append(link)
            time.sleep(0.25)
        except requests.RequestException: pass

def _build_index():
    global VECT, DOC_EMB, DOC_URLS
    DOC_URLS = list(SITE_CACHE)
    corpus = [SITE_CACHE[u] for u in DOC_URLS]
    VECT = TfidfVectorizer(ngram_range=(1,2), stop_words="english").fit(corpus)
    DOC_EMB = normalize(VECT.transform(corpus))

def _ensure_index():
    if VECT is None:
        if not SITE_CACHE: build_site_cache()
        _build_index()

def search_upfh(query: str, top_k: int = 30) -> List[Dict]:
    _ensure_index()
    q_vec = normalize(VECT.transform([query.lower() + " doctor provider phone"]))
    scores = (DOC_EMB @ q_vec.T).toarray().ravel()
    idxs = scores.argsort()[::-1][: top_k*4]
    hits=[]
    for i in idxs:
        url, txt = DOC_URLS[i], SITE_CACHE[DOC_URLS[i]]
        pos = txt.lower().find(query.split()[0].lower())
        snippet = txt[max(0,pos-60):pos+180] + "…"
        hits.append({"url": url, "snippet": snippet})
        if len(hits)>=top_k: break
    return hits

def summarise_upfh(query: str, top_k: int = 3) -> Dict[str,Any]:
    hits = search_upfh(query, top_k)
    def _sum(text):
        msgs=[{"role":"system","content":"≤120 words summary."},
              {"role":"user","content":text[:3000]}]
        return openai.chat.completions.create(model=MODEL,
                                              messages=msgs)\
                   .choices[0].message.content.strip()
    return {"query": query,
            "results": [{"url":h["url"],
                         "summary":_sum(SITE_CACHE[h["url"]])} for h in hits]}

build_site_cache(); _build_index()
logging.info("Cached %d UPFH pages", len(SITE_CACHE))

# ══════════════════════════════════════════════════════════════════════
# 4 ► SLIDING‑FEE CALCULATOR
# ══════════════════════════════════════════════════════════════════════
SLIDING_ROWS = [
    {"tier": "A", "min_pct":   0, "max_pct": 100},
    {"tier": "B", "min_pct": 101, "max_pct": 125},
    {"tier": "C", "min_pct": 126, "max_pct": 150},
    {"tier": "D", "min_pct": 151, "max_pct": 175},
    {"tier": "E", "min_pct": 176, "max_pct": 200},
]

# --- FEE_TABLE unchanged; cut for brevity ----------------------------------
FEE_TABLE = {
    "UPFH Medical Fee":                            {"A":  35, "B":  45, "C":  60, "D":  70, "E":  80, "F": "Full charge"},
    "UPFH Counseling":                             {"A":  20, "B":  25, "C":  35, "D":  40, "E":  50, "F": "Full charge"},
    "UPFH Group Counseling":                       {"A":  10, "B":  15, "C":  20, "D":  25, "E":  30, "F": "Full charge"},
    "UPFH Psychiatric Services":                   {"A":  40, "B":  50, "C":  60, "D":  70, "E":  80, "F": "Full charge"},
    "MOBILE Medical":                              {"A":  35, "B":  45, "C":  60, "D":  70, "E":  80, "F": "Full charge"},
    "Inhouse Vision Exam":                         {"A":  20, "B":  30, "C":  40, "D":  50, "E":  70, "F": "Full charge"},
    "Replacement Glasses":                         {"A":   5, "B":   8, "C":  11, "D":  14, "E":  17, "F": "Full charge"},
    "MOBILE EYE Exam":                             {"A":   5, "B":   7, "C":   8, "D":  10, "E":  15, "F": "Full charge"},
    "MOBILE EYE - Single Lens Glasses":            {"A":  20, "B":  30, "C":  40, "D":  50, "E":  60, "F": "Full charge"},
    "MOBILE EYE - Bifocal Lens Glasses":           {"A":  30, "B":  35, "C":  45, "D":  55, "E":  65, "F": "Full charge"},
    "MVHC Pharmacy Fill Fee":                      {"A":   3, "B":   5, "C":   7, "D":   8, "E":   9, "F": "Full charge"},
    "MA Visit - Labs outside of 7 day global":     {"A":  20, "B":  25, "C":  30, "D":  30, "E":  35, "F": "Full charge"},
    "MA Visit - VACCINES":                         {"A":  20, "B":  20, "C":  20, "D":  20, "E":  20, "F": "Full charge"},
    "Nuvaring":                                    {"A":   5, "B":   6, "C":   7, "D":   8, "E":  30, "F": "Full charge"},
    "Sprintec":                                    {"A":  10, "B":  11, "C":  15, "D":  20, "E":  25, "F": "Full charge"},
    "Loestrin":                                    {"A":  10, "B":  11, "C":  20, "D":  25, "E":  30, "F": "Full charge"},
    "Ella":                                        {"A":  10, "B":  11, "C":  25, "D":  30, "E":  35, "F": "Full charge"},
    "Depo-Provera":                                {"A":  15, "B":  20, "C":  25, "D":  30, "E":  35, "F": "Full charge"},
    "Nexplanon":                                   {"A":  50, "B":  60, "C": 100, "D": 125, "E": 150, "F": "Full charge"},
    "Mirena":                                      {"A":  75, "B":  85, "C": 150, "D": 175, "E": 200, "F": "Full charge"},
    "Liletta":                                     {"A":  75, "B":  85, "C": 150, "D": 175, "E": 200, "F": "Full charge"},
    "Paragard (limited supply)":                   {"A":  75, "B":  85, "C": 150, "D": 175, "E": 200, "F": "Full charge"},
    "IUD Removal Fee":                             {"A":  35, "B":  45, "C":  50, "D":  55, "E":  60, "F": "Full charge"},
    "Implanon Removal":                            {"A":  35, "B":  45, "C":  50, "D":  55, "E":  60, "F": "Full charge"},
    "Vaccine Administration Fee":                  {"A":   0, "B":   3, "C":   4, "D":   5, "E":   5, "F": "Full charge"},
    "STD Testing PLUS OFFICE VISIT":               {"A":  85, "B":  86, "C":  87, "D":  88, "E":  89, "F": "Full charge"},
    "Steroid Injection – Kenalog PLUS OFFICE VISIT":{"A":  20, "B":  25, "C":  30, "D":  35, "E":  40, "F": "Full charge"},
    "Hep A":                                      {"A":  45, "B":  46, "C":  47, "D":  48, "E":  49, "F": "Full charge"},
    "Hep B":                                      {"A":  50, "B":  51, "C":  52, "D":  53, "E":  54, "F": "Full charge"},
    "HIB":                                        {"A":  30, "B":  31, "C":  32, "D":  33, "E":  34, "F": "Full charge"},
    "HPV":                                        {"A": 330, "B": 335, "C": 335, "D": 335, "E": 335, "F": "Full charge"},
    "Influenza/Flu":                              {"A":  18, "B":  19, "C":  20, "D":  21, "E":  22, "F": "Full charge"},
    "Child Flu":                                  {"A":  18, "B":  19, "C":  20, "D":  21, "E":  22, "F": "Full charge"},
    "Pneumovax":                                   {"A": 106, "B": 107, "C": 108, "D": 109, "E": 110, "F": "Full charge"},
    "Prevnar 13":                                 {"A": 190, "B": 191, "C": 192, "D": 193, "E": 194, "F": "Full charge"},
    "Adult Menactra":                             {"A": 120, "B": 121, "C": 122, "D": 123, "E": 124, "F": "Full charge"},
    "MMR":                                         {"A":  75, "B":  76, "C":  77, "D":  78, "E":  79, "F": "Full charge"},
    "TD-Tetanus":                                 {"A":  35, "B":  36, "C":  37, "D":  38, "E":  39, "F": "Full charge"},
    "Adult TDAP (Boostrix)":                      {"A":  52, "B":  53, "C":  54, "D":  55, "E":  56, "F": "Full charge"},
    "Polio/IPV":                                  {"A":  35, "B":  36, "C":  37, "D":  38, "E":  39, "F": "Full charge"},
    "TB":                                          {"A":  10, "B":  11, "C":  12, "D":  13, "E":  14, "F": "Full charge"},
    "Adult Varicella":                            {"A": 130, "B": 131, "C": 132, "D": 133, "E": 134, "F": "Full charge"},
    "B-12":                                        {"A":  15, "B":  16, "C":  17, "D":  18, "E":  20, "F": "Full charge"},
    "Wedge Toenail Removal (Global 10 days)":     {"A":  75, "B":  80, "C":  85, "D":  90, "E":  95, "F": "Full charge"},
    "Toenail Removal":                            {"A": 100, "B": 105, "C": 110, "D": 115, "E": 120, "F": "Full charge"},
    "Endometrial Biopsy (Same Day Add-on)":       {"A": 105, "B": 110, "C": 115, "D": 115, "E": 115, "F": "Full charge"},
    "Endometrial Biopsy (Follow-up visit)":       {"A":  35, "B":  45, "C":  60, "D":  70, "E":  80, "F": "Full charge"},
    "Ear Lavage":                                 {"A":  10, "B":  11, "C":  12, "D":  13, "E":  14, "F": "Full charge"},
    "PT/INR (outside 14-day window)":             {"A":  15, "B":  16, "C":  17, "D":  18, "E":  20, "F": "Full charge"},
}

def poverty_percent(income: float, family_size: int) -> float:
    base = 15_060 + (family_size-1)*5_380
    return 100*income/base

def _norm_proc(raw:str)->str|None:
    t = raw.strip().lower()
    if not t: return None
    for k in FEE_TABLE:
        if t==k.lower() or t in k.lower(): return k
    close = difflib.get_close_matches(t,[k.lower() for k in FEE_TABLE],n=1,cutoff=.5)
    return next((k for k in FEE_TABLE if k.lower()==close[0]),None) if close else None

def estimate_fee(income:float,family_size:int,procedure:str="Office Visit")->Dict[str,Any]:
    pct=round(poverty_percent(income,family_size),1)
    tier=next((r["tier"] for r in SLIDING_ROWS if r["min_pct"]<=pct<=r["max_pct"]),"F")
    key=_norm_proc(procedure)
    fee=FEE_TABLE.get(key,{}).get(tier,"Full charge")
    return {"procedure":key or procedure,"tier":tier,
            "poverty_percent":pct,"estimated_fee":fee}

def list_upfh_services()->List[str]:
    return sorted(FEE_TABLE.keys())

# ══════════════════════════════════════════════════════════════════════
# 5 ► LOCATION LOOK‑UP
# ══════════════════════════════════════════════════════════════════════
UPFH_LOCATIONS = {
    "family_clinic": {
        "name": "UPFH Family Clinic – West Jordan",
        "address": "9103 S 1300 W Suite 102, West Jordan, UT 84088",
        "hours": {
            "Mon": "8 am – 5 30 pm",
            "Tue": "8 am – 5 30 pm",
            "Wed": "8 am – 5 30 pm",
            "Thu": "8 am – 8 pm",
            "Fri": "8 am – 5 30 pm",
            "Sat": "9 am – 1 pm",
            "Sun": "Closed",
        },
        "phone": "801‑417‑0131",
    },

    "mid_valley": {
        "name": "UPFH Mid‑Valley Clinic",
        "address": "8446 S Harrison Street, Midvale, UT 84047",
        "hours": {
            "Mon": "8 am – 5 pm",
            "Tue": "8 am – 5 pm",
            "Wed": "8 am – 5 pm",
            "Thu": "12 pm – 8 pm",
            "Fri‑Sun": "Closed",
        },
        "phone": "801‑417‑0131",
    },

    "dental": {
        "name": "UPFH Dental",
        "address": "7651 S Main Street, Midvale, UT 84047",
        "hours": {
            "Mon": "8 am – 12 pm • 1 pm – 5 pm",
            "Tue": "8 am – 12 pm • 1 pm – 5 pm",
            "Wed": "8 am – 12 pm • 1 pm – 5 pm",
            "Thu": "8 am – 12 pm • 1 pm – 5 pm",
            "Fri": "9 am – 12 pm • 1 pm – 4 pm",
            "Sat‑Sun": "Closed",
        },
        "phone": "801‑417‑0131",
    },

    "pharmacy": {
        "name": "UPFH Pharmacy",
        "address": "9103 S 1300 W Suite 102, West Jordan, UT 84088",
        "hours": {
            "Mon": "8 30 am – 5 30 pm",
            "Tue": "8 30 am – 5 30 pm",
            "Wed": "8 30 am – 5 30 pm",
            "Thu": "8 30 am – 8 pm",
            "Sat": "9 am – 1 pm",
            "Sun": "Closed",
        },
        "phone": "801‑417‑0131",
    },

    "mobile_medical": {
        "name": "Mobile Medical Clinic (weekly schedule)",
        "schedule": [
            {"date": "07/15/2025", "site": "UNP Hartland, 1578 W 1700 S, Salt Lake City UT 84104", "start": "8 30 am"},
            {"date": "07/16/2025", "site": "UNP Hartland, 1578 W 1700 S, Salt Lake City UT 84104", "start": "8 30 am"},
            {"date": "07/17/2025", "site": "Orange Street Clinic, 80 N Orange Street, Salt Lake City UT 84116", "start": "1 00 pm"},
        ],
        "phone": "801‑417‑0131 ext 123",
    },
}

def lookup_location(keyword:str)->Dict[str,Any]:
    kw=keyword.lower().strip()
    for k,v in UPFH_LOCATIONS.items():
        if kw in k or kw in v["name"].lower(): return v
    for v in UPFH_LOCATIONS.values():
        if any(w in v["name"].lower() for w in kw.split()): return v
    return {}

# ══════════════════════════════════════════════════════════════════════
# 6 ► TOOL SCHEMAS
# ══════════════════════════════════════════════════════════════════════
calendar_avail_tool = {
    "name": "check_calendar_availability",
    "description": (
        "Return ≤10 free 30‑minute slots for a specific date **or** each day "
        "in a date range.  Supply *either* `date` (YYYY‑MM‑DD) *or* both "
        "`start_date` and `end_date`."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "date":       {"type": "string", "description": "YYYY‑MM‑DD"},
            "start_date": {"type": "string", "description": "YYYY‑MM‑DD"},
            "end_date":   {"type": "string", "description": "YYYY‑MM‑DD"},
            "duration_minutes": {
                "type": "integer", "default": 30,
                "description": "Length of each suggested slot"
            }
        }
        # no 'required', no 'oneOf' — OpenAI‑compliant
    }
}

calendar_create_tool = {
    "name": "create_calendar_event",
    "description": (
        "Book the chosen slot in Google Calendar.  "
        "Returns `status`, `event_id`, `start`, and `end` "
        "but **never** the private htmlLink."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "patient_name": {"type": "string"},
            "email":        {"type": "string"},
            "phone":        {"type": "string"},
            "reason":       {"type": "string"},
            "start":        {"type": "string", "description": "ISO dateTime"},
            "end":          {"type": "string", "description": "ISO dateTime"}
        },
        "required": ["patient_name", "start", "end"]
    }
}

location_tool = {
    "name":"upfh_location_lookup",
    "description":"Return address, phone & hours for a UPFH location.",
    "parameters":{"type":"object","properties":{"keyword":{"type":"string"}},
                 "required":["keyword"]},
}
site_tool = {
    "name":"upfh_site_search",
    "description":"Keyword search across upfh.org.",
    "parameters":{"type":"object",
        "properties":{"query":{"type":"string"},
                      "top_k":{"type":"integer","default":30}},
        "required":["query"]},
}
site_summary_tool = {
    "name":"upfh_site_summary",
    "description":"Return concise summaries of upfh.org pages.",
    "parameters":{"type":"object",
        "properties":{"query":{"type":"string"},
                      "top_k":{"type":"integer","default":3}},
        "required":["query"]},
}
services_tool = {"name":"list_upfh_services",
                 "description":"All services on sliding‑fee schedule.",
                 "parameters":{"type":"object","properties":{},"required":[]}}
sliding_fee_tool = {
    "name":"estimate_fee",
    "description":"Estimate visit cost (needs income, family_size, procedure).",
    "parameters":{"type":"object",
        "properties":{"income":{"type":"number"},
                      "family_size":{"type":"integer"},
                      "procedure":{"type":"string"}},
        "required":["income","family_size","procedure"]},
}
email_tool = {
    "name":"submit_appointment_request",
    "description":"Send confirmation e‑mails to patient + staff.",
    "parameters":{"type":"object",
        "properties":{"email":{"type":"string"},
                      "patient_name":{"type":"string"},
                      "phone":{"type":"string"},
                      "preferred_date":{"type":"string"},
                      "preferred_time":{"type":"string"},
                      "reason":{"type":"string"},
                      "has_insurance":{"type":"boolean"}},
        "required":["email"]},
}

TOOLS = [
    {"type":"function","function":location_tool},
    {"type":"function","function":site_tool},
    {"type":"function","function":site_summary_tool},
    {"type":"function","function":services_tool},
    {"type":"function","function":sliding_fee_tool},
    # calendar tools
    {"type":"function","function":calendar_avail_tool},
    {"type":"function","function":calendar_create_tool},
    # e‑mail
    {"type":"function","function":email_tool},
]

# ══════════════════════════════════════════════════════════════════════
# 7 ► SYSTEM PROMPT
# ══════════════════════════════════════════════════════════════════════
SYSTEM_PROMPT = (
"### UPFH Virtual Front‑Desk Assistant\n"
"#### TOOLS\n"
"• upfh_location_lookup – address & hours\n"
"• upfh_site_search / upfh_site_summary – website info\n"
"• list_upfh_services / estimate_fee – sliding‑fee\n"
"• check_calendar_availability – list free slots\n"
"• create_calendar_event – book slot\n"
"• submit_appointment_request – e‑mail confirmation\n"
"#### BOOKING FLOW\n"
"1. Gather name, e‑mail, phone, preferred **date**.\n"
"2. Call check_calendar_availability; propose ≤5 slots.\n"
"3. On selection, confirm start/end → call create_calendar_event.\n"
"4. Ask if patient has insurance; if not, offer sliding‑fee estimate.\n"
"5. Summarise & on any 'yes' call submit_appointment_request.\n"
"#### STYLE\n"
"- Warm, concise, HIPAA‑compliant.\n"
"- Detect language; respond in Spanish if user is >60 % Spanish.\n"
)

WELCOME_BUBBLE = (
"👋 **Welcome to the UPFH Virtual Front Desk!**\n\n"
"• Book or reschedule an appointment (with live calendar)\n"
"• Estimate costs on our sliding‑fee scale\n"
"• Clinic hours, locations & provider info\n\n"
"_How can I help you today?_"
)

# ══════════════════════════════════════════════════════════════════════
# 8 ► TOOL ROUTER
# ══════════════════════════════════════════════════════════════════════
def _handle_tool_call(msg):
    results=[]
    for call in msg.tool_calls or []:
        fn, raw = call.function.name, call.function.arguments or "{}"
        try: args=json.loads(raw)
        except Exception as exc:
            logging.exception("Bad JSON for %s: %s",fn,exc); args={}
        try:
            if fn=="upfh_location_lookup":
                res=lookup_location(**args)
            elif fn=="upfh_site_search":
                res=search_upfh(**args)
            elif fn=="upfh_site_summary":
                res=summarise_upfh(**args)
            elif fn=="list_upfh_services":
                res=list_upfh_services()
            elif fn=="estimate_fee":
                res=estimate_fee(**args)
            elif fn=="check_calendar_availability":
                res=check_calendar_availability(**args)
            elif fn=="create_calendar_event":
                res=create_calendar_event(**args)
            elif fn=="submit_appointment_request":
                send_appt_email(args)
                res={"status":"submitted"}
            else:
                res={"error":f"unknown tool {fn}"}
        except Exception as exc:
            logging.exception("%s failed: %s",fn,exc)
            res={"error":str(exc)}
        results.append({"role":"tool","tool_call_id":call.id,
                        "content":json.dumps(res,ensure_ascii=False)})
    return results

# ══════════════════════════════════════════════════════════════════════
# 9 ► MAIN CHAT LOOP (Gradio compatible)
# ══════════════════════════════════════════════════════════════════════
def chat(user_input:str, history:Optional[List[Any]]=None)->str:
    msgs=[{"role":"system","content":SYSTEM_PROMPT}]
    if not history:
        msgs.append({"role":"assistant","content":WELCOME_BUBBLE})
    if history:
        for turn in history:
            if isinstance(turn,(list,tuple)):
                if turn and turn[0]: msgs.append({"role":"user","content":turn[0]})
                if len(turn)>1 and turn[1]: msgs.append({"role":"assistant","content":turn[1]})
            elif isinstance(turn,dict): msgs.append(turn)
    msgs.append({"role":"user","content":user_input})

    resp=openai.chat.completions.create(model=MODEL,messages=msgs,tools=TOOLS)
    msg=resp.choices[0].message

    if resp.choices[0].finish_reason=="tool_calls":
        tool_msgs=_handle_tool_call(msg)
        msgs += [msg]+tool_msgs
        resp=openai.chat.completions.create(model=MODEL,messages=msgs)
        msg=resp.choices[0].message

    return msg.content

# ══════════════════════════════════════════════════════════════════════
# 10 ► E‑MAIL ACKNOWLEDGEMENT
# ══════════════════════════════════════════════════════════════════════
def send_appt_email(payload: Dict[str, Any]) -> None:
    """Compose and send patient + staff confirmation e‑mails."""
    to_addr = payload.get("email")
    if not to_addr:
        return

    name   = payload.get("patient_name", "Valued Patient")
    date   = payload.get("preferred_date", "TBD")
    time_  = payload.get("preferred_time") or ""   # may be blank
    when   = f"{date} {time_}".strip()             # collapses double space
    reason = payload.get("reason", "General appointment")

    # Patient copy
    body_p = (
        f"Hi {name},\n\n"
        f"We received your appointment request for {when} "
        f"(Reason: {reason}). We’ll confirm soon.\n\n"
        "Thank you,\nUPFH"
    )
    # Staff alert
    body_s = (
        "NEW REQUEST\n"
        f"Patient: {name}\n"
        f"Email: {to_addr}\n"
        f"Date/time: {when}\n"
        f"Reason: {reason}"
    )

    send_gmail([to_addr],   "We received your appointment request – UPFH", body_p)
    send_gmail([FROM_ADDR], "NEW appointment request – action needed",     body_s)
