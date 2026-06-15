import os

# LingHub calls my_lemmatizer through this base URL.
# Server default is local because my_lemmatizer runs on the same droplet.
MY_LEMMATIZER_URL = os.getenv("MY_LEMMATIZER_URL", "http://127.0.0.1:8001").rstrip("/")

# Collocation XML loading is potentially heavy. Keep disabled by default so
# LingHub can be deployed safely before data/collocations/*.xml is installed.
LINGHUB_ENABLE_COLLOCATIONS = os.getenv("LINGHUB_ENABLE_COLLOCATIONS", "0").strip().lower() in {
    "1", "true", "yes", "on"
}
