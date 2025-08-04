"""
calendar_tools.py  –  thin synchronous helpers for the chatbot
--------------------------------------------------------------
• available_slots(...)    → returns {"slots": [ "09:00" , "09:30" , … ]}
• book_calendar_event(...)→ returns {"status": "booked",
                                      "start_iso": "2025‑08‑12T15:00:00‑06:00",
                                      "end_iso":   "2025‑08‑12T15:30:00‑06:00",
                                      "htmlLink":  "https://calendar.google.com/event?eid=…"}
"""

from __future__ import annotations
import os, datetime as _dt, itertools, logging
from typing import List, Dict

from google.oauth2.service_account import Credentials as SA_Creds
from googleapiclient.discovery import build   as gbuild
from googleapiclient.errors    import HttpError

# ── one Calendar service shared by all calls ──────────────────────────
_GCAL = None
def _gcal():
    global _GCAL
    if _GCAL is None:
        cred_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS",
                              "backend/google_creds.json")
        creds = SA_Creds.from_service_account_file(
            cred_file,
            scopes=["https://www.googleapis.com/auth/calendar"]
        )
        _GCAL = gbuild("calendar", "v3", credentials=creds,
                       cache_discovery=False)
    return _GCAL

CAL_ID = os.getenv("UPFH_GCAL_ID")          # required
if not CAL_ID:
    raise RuntimeError("Set UPFH_GCAL_ID in the environment")

# ──────────────────────────────────────────────────────────────────────────
# Helper – split a day into 30‑min slots minus busy blocks
# ──────────────────────────────────────────────────────────────────────────
_SLOT = _dt.timedelta(minutes=30)

def _day_slots(date: _dt.date) -> List[_dt.datetime]:
    start = _dt.datetime.combine(date, _dt.time(8, 0))   # clinic opens 08:00
    end   = _dt.datetime.combine(date, _dt.time(17, 30)) # close 17:30
    out   = []
    while start + _SLOT <= end:
        out.append(start)
        start += _SLOT
    return out

def _busy_blocks(date_from: str, date_to: str) -> List[Dict]:
    """Return busy ranges (ISO) from Google freebusy."""
    body = {
        "timeMin": f"{date_from}T00:00:00Z",
        "timeMax": f"{date_to}T23:59:59Z",
        "items":   [{"id": CAL_ID}],
    }
    try:
        resp = _gcal().freebusy().query(body=body).execute()
        return resp["calendars"][CAL_ID].get("busy", [])
    except HttpError as e:
        logging.exception("GCal freebusy failed: %s", e)
        return []

# ──────────────────────────────────────────────────────────────────────────
# Public functions ‑ exactly the names referenced in chatbot.py
# ──────────────────────────────────────────────────────────────────────────
def available_slots(date_from: str,
                    date_to:   str,
                    slot_minutes: int = 30) -> Dict:
    """
    Return {"slots": ["2025‑08‑12T09:00", …]}  – ISO **without** timezone
    so the assistant can append “am / pm” as needed.
    """
    if slot_minutes != 30:
        return {"error": "Only 30‑minute slots supported"}

    d0 = _dt.datetime.fromisoformat(date_from).date()
    d1 = _dt.datetime.fromisoformat(date_to).date()
    all_days   = [d0 + _dt.timedelta(days=i)
                  for i in range((d1 - d0).days + 1)]

    busy_raw   = _busy_blocks(date_from, date_to)
    busy_pts   = [( _dt.datetime.fromisoformat(b["start"][:-1]),
                    _dt.datetime.fromisoformat(b["end"][:-1]) )
                  for b in busy_raw]

    slots = []
    for day in all_days:
        for s in _day_slots(day):
            e = s + _SLOT
            if any(bs <= s < be or bs < e <= be for bs, be in busy_pts):
                continue          # overlaps a busy block
            slots.append(s.strftime("%Y-%m-%dT%H:%M"))

    return {"slots": slots}

def book_calendar_event(start_iso: str,
                        end_iso:   str,
                        patient_name: str,
                        email: str,
                        reason: str = "Appointment") -> Dict:
    """
    Create the event in GCal and return confirmation data.
    """
    evt = {
        "summary":     f"UPFH – {patient_name}",
        "description": f"Email: {email}\nReason: {reason}",
        "start": {"dateTime": start_iso, "timeZone": "America/Denver"},
        "end":   {"dateTime": end_iso,   "timeZone": "America/Denver"},
        "attendees": [{"email": email}],
    }
    try:
        created = _gcal().events().insert(
            calendarId=CAL_ID, body=evt, sendUpdates="all"
        ).execute()
        return {
            "status":     "booked",
            "start_iso":  start_iso,
            "end_iso":    end_iso,
            "htmlLink":   created.get("htmlLink", "")
        }
    except HttpError as e:
        logging.exception("GCal insert failed: %s", e)
        return {"error": str(e)}
