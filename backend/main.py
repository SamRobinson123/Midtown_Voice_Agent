import os, re, html, logging
from typing import Any, List, Dict

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from twilio.twiml.voice_response import VoiceResponse
from langdetect import detect
import gradio as gr, uvicorn

try:
    from dotenv import load_dotenv
    load_dotenv()
except ModuleNotFoundError:
    pass

from bot.chatbot import chat
from .gradio_app import build_widget

# ───────────────────────────────────
SPEECH_RATE = "93%"
LOCALE_MAP  = {
    "en": ("en-US", "Polly.Ruth-Neural"),
    "es": ("es-MX", "Polly.Lupe-Neural"),
}

VOICE_WELCOME = {
    "en": ("If this is an emergency, please hang up and dial nine‑one‑one or "
           "go to the nearest emergency room immediately. Welcome to the Utah "
           "Partners for Health virtual front desk. You can book or change an "
           "appointment, estimate costs, or ask about clinic hours and "
           "providers. How can I help you today?"),
    "es": ("Si se trata de una emergencia, por favor cuelgue y llame al "
           "nueve‑uno‑uno o diríjase de inmediato a la sala de emergencias "
           "más cercana. Bienvenido al mostrador virtual de Utah Partners for "
           "Health. Puede reservar o cambiar una cita, estimar costos o "
           "preguntar sobre horarios y proveedores. ¿En qué puedo ayudarle?"),
}

VOICE_SYSTEM_PROMPT = (
    "You are UPFH’s virtual front‑desk assistant **on a VOICE call**.\n"
    "Ask **one question per turn**, then wait for the caller.\n\n"

    # ── original booking steps, amended ──────────────────────────────
    "Booking flow:\n"
    "1. full name\n"
    "2. e‑mail\n"
    "3. phone\n"
    "4. preferred **date or 3–7‑day window** (no time yet)\n"
    "   • Immediately call `check_calendar_availability` for that day / range\n"
    "   • Read back **up to five** free 30‑minute slots (e.g. “Ten‑thirty a.m.”)\n"
    "   • Let the caller choose one, then call `create_calendar_event`\n"
    "5. reason for the visit (if not already gathered)\n"
    "6. ask: “Do you have insurance we can bill?” (yes/no)\n\n"
    "If NO & caller requests prices → ask for procedure → household size → "
    "income, then call `estimate_fee`.\n\n"
    "After all slots, read back a short summary, ask for confirmation, and on "
    "any affirmative reply call `submit_appointment_request` (and "
    "`estimate_fee` if gathered).\n\n"

    # ── language + privacy rules (unchanged + new) ───────────────────
    "Always speak in the caller’s language (English or Spanish).\n"
    "Never reveal internal Google‑Calendar links or event IDs to the caller."
)

if "OPENAI_API_KEY" not in os.environ:
    raise RuntimeError("Set OPENAI_API_KEY in the environment")

app = FastAPI(title="UPFH Front‑Desk – Chat & Voice", version="2.7.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

# ── /chat ──────────────────────────
class ChatTurn(BaseModel):
    user_input: str
    history: List[Any] | None = None
class ChatResp(BaseModel):
    answer: str
@app.post("/chat", response_model=ChatResp)
def chat_json(turn: ChatTurn):
    try:
        return {"answer": chat(turn.user_input, turn.history)}
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc

# ── helpers ────────────────────────
def _ascii(txt: str) -> str:
    return re.sub("[\u2018\u2019\u201c\u201d\u2013\u2014\u2022\u00a0]",
                  lambda m: {"\u2018":"'","\u2019":"'","\u201c":'"',
                             "\u201d":'"',"\u2013":"-","\u2014":"-",
                             "\u2022":"*","\u00a0":" "}[m.group()], txt)

def md_to_plain(md: str) -> str:
    return _ascii(re.sub(r"[_*`>#\\\-•]", " ", html.unescape(md))).strip()

def ssml(text: str, rate: str = SPEECH_RATE) -> str:
    return f'<speak><prosody rate="{rate}">{_ascii(text)}</prosody></speak>'

def detect_lang(txt: str) -> str:
    try:
        return detect(txt)[:2]
    except Exception:
        return "en"

# ── /voice ─────────────────────────
sessions: Dict[str, Dict[str, Any]] = {}
@app.post("/voice", response_class=Response,
          responses={200: {"content": {"text/xml": {}}}})
async def voice(request: Request):
    form  = await request.form()
    sid   = form.get("CallSid") or "x"
    utter = (form.get("SpeechResult") or "").strip()

    sess = sessions.setdefault(
        sid, {"history": [{"role": "system", "content": VOICE_SYSTEM_PROMPT}]}
    )
    hist = sess["history"]

    if not utter:
        lang  = sess.setdefault("lang", "en")
        reply = VOICE_WELCOME[lang]
    else:
        sess.setdefault("lang", detect_lang(utter))
        lang = sess["lang"]
        hist.append({"role": "user", "content": utter})
        reply = chat(utter, [m for m in hist if m["role"] != "tool"])
        hist.append({"role": "assistant", "content": reply})

    locale, voice_id = LOCALE_MAP.get(lang, LOCALE_MAP["en"])

    vr = VoiceResponse()
    g  = vr.gather(
        input="speech",
        action="/voice",
        language=locale,
        speech_timeout="auto",
    )
    g.say(ssml(md_to_plain(reply)), language=locale,
          voice=voice_id, allow_ssml=True)
    return Response(str(vr), media_type="text/xml")

# ── Gradio widget ──────────────────
gr.mount_gradio_app(app, build_widget(), path="/")

if __name__ == "__main__":
    logging.basicConfig(level="INFO", format="%(levelname)s  %(message)s")
    uvicorn.run("backend.main:app", host="0.0.0.0",
                port=int(os.getenv("PORT", 8000)), reload=True)
