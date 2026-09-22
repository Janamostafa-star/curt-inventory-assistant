"""
Check which Gemini models are actually available and responding right now,
using your GEMINI_API_KEY. Run this standalone, no FastAPI/uvicorn needed.

Usage:
    python check_gemini_models.py
"""

import os
from dotenv import load_dotenv
from google import genai
from google.genai import errors

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing. Check your .env file.")

client = genai.Client(api_key=API_KEY)

# Candidates worth checking — add/remove names as you hear about new ones
CANDIDATE_MODELS = [
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gemini-3.6-flash",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]


def list_available_models():
    print("=" * 60)
    print("Models your API key can actually see (via client.models.list()):")
    print("=" * 60)
    try:
        names = []
        for m in client.models.list():
            # only show ones that support generateContent, since that's what we need
            methods = getattr(m, "supported_actions", None) or getattr(m, "supported_generation_methods", None)
            names.append(m.name)
            print(f"  {m.name}   (methods: {methods})")
        return names
    except Exception as e:
        print(f"  FAILED to list models: {e}")
        return []


def test_model(model_name: str):
    try:
        response = client.models.generate_content(
            model=model_name,
            contents="Say 'ok' and nothing else.",
        )
        text = (response.text or "").strip()
        print(f"  ✅ {model_name:30s} -> responded: {text!r}")
        return True
    except errors.ClientError as e:
        # 404 = model doesn't exist / not available to you; 429 = quota; 400 = bad request
        print(f"  ❌ {model_name:30s} -> ClientError: {e}")
        return False
    except errors.ServerError as e:
        # 503 = overloaded/congested — model exists but is temporarily down
        print(f"  ⚠️  {model_name:30s} -> ServerError (likely temporary): {e}")
        return False
    except Exception as e:
        print(f"  ❌ {model_name:30s} -> Unexpected error: {type(e).__name__}: {e}")
        return False


if __name__ == "__main__":
    list_available_models()

    print()
    print("=" * 60)
    print("Testing each candidate model with a real generate_content call:")
    print("=" * 60)
    working = []
    for name in CANDIDATE_MODELS:
        if test_model(name):
            working.append(name)

    print()
    print("=" * 60)
    if working:
        print(f"Working models: {working}")
        print(f"Recommend setting GEMINI_MODEL={working[0]}")
    else:
        print("No candidate models worked. Check your API key / quota / region.")
    print("=" * 60)