# app.py
from fastapi import FastAPI
from routers import nlp, thesaurus, collocations
from core.thesaurus import load_thesaurus
from core.collocations import load_collocations

app = FastAPI(
    title="LingHub",
    description="Hub service for Slovene/Italian NLP, thesaurus, collocations, and other language resources.",
    version="0.3.0",
)


@app.on_event("startup")
async def startup_event():
    # Eager loading into RAM
    load_thesaurus()
    load_collocations()


@app.get("/")
def root():
    return {"status": "ok", "message": "LingHub is alive."}


app.include_router(nlp.router, prefix="/nlp", tags=["nlp"])
app.include_router(thesaurus.router, prefix="/thesaurus", tags=["thesaurus"])
app.include_router(collocations.router, tags=["collocations"])
