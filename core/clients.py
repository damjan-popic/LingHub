# core/clients.py
import httpx
from .config import MY_LEMMATIZER_URL

async def analyze_text(lang: str, text: str):
    async with httpx.AsyncClient(base_url=MY_LEMMATIZER_URL, timeout=10.0) as client:
        r = await client.post(
            "/analyze",
            json={"lang": lang, "text": text},
        )
        r.raise_for_status()
        return r.json()

async def loris_check(lang: str, text: str):
    async with httpx.AsyncClient(base_url=MY_LEMMATIZER_URL, timeout=10.0) as client:
        r = await client.post(
            "/loris/check",
            json={"lang": lang, "text": text},
        )
        r.raise_for_status()
        return r.json()
