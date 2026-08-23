"""
Pawstructured backend.
No Arcade, no Google Sheets, no Slack. Just two AI endpoints:
- /extract: turn a free-text pet write-up into structured data
- /caption: turn the same write-up into a ready-to-post social caption

The frontend keeps everything in the browser and lets the user download
a CSV of everything they've generated. Nothing is stored server-side.

SETUP:
1. pip install fastapi uvicorn requests python-dotenv --break-system-packages
2. Put your key in .env: GEMINI_API_KEY="your_key_here"
3. Run: uvicorn arcade_backend_main:app --reload --port 8000
"""

import os
import time
import json as jsonlib
import datetime as _dt

import requests as http_requests
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Simple daily usage cap so a shared/hosted key can't run up unexpected costs.
DAILY_LIMIT = 25
_usage = {"date": None, "count": 0}


def _check_and_count_usage():
    today = _dt.date.today().isoformat()
    if _usage["date"] != today:
        _usage["date"] = today
        _usage["count"] = 0
    if _usage["count"] >= DAILY_LIMIT:
        return False
    _usage["count"] += 1
    return True


def call_gemini(prompt: str) -> dict:
    """Shared helper: call Gemini with retry-on-503, return parsed JSON or {"error": ...}."""
    try:
        resp = None
        for attempt in range(3):
            try:
                resp = http_requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent?key={GEMINI_API_KEY}",
                    json={"contents": [{"parts": [{"text": prompt}]}]},
                    timeout=30,
                )
                resp.raise_for_status()
                break
            except http_requests.exceptions.HTTPError:
                if resp is not None and resp.status_code == 503 and attempt < 2:
                    time.sleep(2)
                    continue
                raise
        data = resp.json()

        candidates = data.get("candidates", [])
        if not candidates:
            feedback = data.get("promptFeedback", {})
            return {"error": f"No candidates returned. Prompt feedback: {feedback}"}

        candidate = candidates[0]
        finish_reason = candidate.get("finishReason", "")
        content = candidate.get("content", {})
        parts = content.get("parts", [])

        if not parts:
            return {"error": f"Empty response from Gemini (finishReason: {finish_reason}). Try again or rephrase."}

        raw_text = parts[0].get("text", "")
        if not raw_text:
            return {"error": f"Gemini returned no text (finishReason: {finish_reason})."}

        clean = raw_text.replace("```json", "").replace("```", "").strip()
        return jsonlib.loads(clean)
    except Exception as e:
        return {"error": str(e)}


class ExtractRequest(BaseModel):
    dog_name: str
    text: str


@app.post("/extract")
def extract_tags(req: ExtractRequest):
    """Free tag extraction using Google Gemini."""
    if not _check_and_count_usage():
        return {"error": f"Daily limit of {DAILY_LIMIT} pets reached. Try again tomorrow, or contact us to raise your limit."}

    system_prompt = """You extract structured intake data from free-text pet descriptions for a rescue organization.
Respond ONLY with valid JSON, no markdown fences, no preamble. Format exactly:
{
  "basics": {"sex":"male|female|unknown","age":"short string like '4 years' or 'unknown'","weight":"short string like '60 lbs' or 'unknown'","breed":"short string or 'unknown'"},
  "tags":[{"label":"short human-readable tag, 2-6 words","type":"hard_requirement"}]
}
Only fill basics fields with what's explicitly stated or clearly implied, use "unknown" if not mentioned, never invent facts.
tags type must be one of: "hard_requirement" (a strict condition a home must meet, e.g. no kids, needs a fenced yard, adult-only home, needs an experienced owner) or "personality" (any other trait, behavior, or note about the pet, e.g. crate trained, nervous in new situations, good with other dogs, loves the outdoors).
Extract 4-8 tags total, grounded only in the text."""

    prompt = f"{system_prompt}\n\nPet name: {req.dog_name}\n\nWrite-up:\n{req.text}"
    return call_gemini(prompt)


class CaptionRequest(BaseModel):
    dog_name: str
    text: str


@app.post("/caption")
def generate_caption(req: CaptionRequest):
    """Generate a ready-to-post Instagram/Facebook caption from the same write-up."""
    if not _check_and_count_usage():
        return {"error": f"Daily limit of {DAILY_LIMIT} pets reached. Try again tomorrow, or contact us to raise your limit."}

    prompt = f"""You write warm, share-ready social media captions for a pet rescue's Instagram/Facebook posts, based on a volunteer's write-up.

Pet name: {req.dog_name}
Write-up: {req.text}

Write ONE caption in the pet's own voice (first person, playful, warm), 2-4 sentences, ending with a call to action to reach out about fostering or adopting. Then, on a completely separate line (use a real newline character, not a space), add 3-5 relevant hashtags (e.g. #AdoptDontShop #RescuePet #FosterFailWelcome plus breed/trait-specific ones). The hashtags must not be on the same line as the caption text.

Respond ONLY with valid JSON, no markdown fences, no preamble. Format exactly:
{{"caption": "the caption body\\n\\n#Hashtag1 #Hashtag2 #Hashtag3"}}"""

    return call_gemini(prompt)


@app.get("/")
def health():
    return {
        "status": "ok",
        "gemini_key_set": bool(GEMINI_API_KEY),
        "daily_limit": DAILY_LIMIT,
        "used_today": _usage["count"],
    }
