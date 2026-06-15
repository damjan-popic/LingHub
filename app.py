# app.py
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import nlp, thesaurus, collocations
from core.thesaurus import load_thesaurus
from core.collocations import load_collocations

app = FastAPI(
    title="LingHub",
    description="Hub service for Slovene/Italian NLP, thesaurus, collocations, and other language resources.",
    version="0.4.0",
)

# Browser frontends on another origin need CORS. Keep it explicit: set
# LINGHUB_CORS_ORIGINS="https://frontend.example, http://localhost:5173".
_cors_origins = [
    origin.strip()
    for origin in os.getenv("LINGHUB_CORS_ORIGINS", "").split(",")
    if origin.strip()
]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.on_event("startup")
async def startup_event():
    # Eager loading into RAM
    load_thesaurus()
    load_collocations()


@app.get("/")
def root():
    return {"status": "ok", "message": "LingHub is alive."}


@app.get("/health")
def health():
    return {"status": "ok", "service": "LingHub"}


app.include_router(nlp.router, prefix="/nlp", tags=["nlp"])
app.include_router(thesaurus.router, prefix="/thesaurus", tags=["thesaurus"])
app.include_router(collocations.router, tags=["collocations"])
