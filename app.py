# app.py
from fastapi import FastAPI
from routers import nlp, thesaurus
from core.thesaurus import load_thesaurus

app = FastAPI(
    title="LingHub",
    description="Hub service for Slovene/Italian NLP, thesaurus, and other language resources.",
    version="0.1.0",
)


@app.on_event("startup")
async def startup_event():
    # Eager loading of thesaurus into RAM
    load_thesaurus()


@app.get("/")
def root():
    return {"status": "ok", "message": "LingHub is alive."}


app.include_router(nlp.router, prefix="/nlp", tags=["nlp"])
app.include_router(thesaurus.router, prefix="/thesaurus", tags=["thesaurus"])

