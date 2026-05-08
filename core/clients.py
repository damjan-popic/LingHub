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


async def analyze_full_text(lang: str, text: str):
    """
    Token analysis plus the span/MWU layer from my_lemmatizer.
    Falls back to /analyze if the lemmatizer has not yet been upgraded.
    """
    async with httpx.AsyncClient(base_url=MY_LEMMATIZER_URL, timeout=10.0) as client:
        r = await client.post(
            "/analyze-full",
            json={"lang": lang, "text": text},
        )
        if r.status_code == 404:
            fallback = await client.post(
                "/analyze",
                json={"lang": lang, "text": text},
            )
            fallback.raise_for_status()
            return {"tokens": fallback.json(), "spans": []}
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
