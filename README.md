# LingHub

LingHub is the API hub that frontends should call. It proxies NLP/LORIS requests to `my_lemmatizer`, and it serves thesaurus and collocation data from LingHub’s XML resources.

Default deployment port in the examples: `8010`.

Important principle for frontend developers:

```text
Browser/frontend → LingHub → my_lemmatizer
```

Do **not** point the browser directly at `my_lemmatizer` unless you intentionally expose that internal service.

---

## English

## 1. What changed in this version

This version assumes the upgraded `my_lemmatizer` with MWU/span support and the new approved LORIS import.

### Main changes

1. **LingHub forwards the LORIS span layer.**
   `POST /nlp/loris-check` still returns `tokens` and `issues`, but now also returns `spans`.

2. **New proxy endpoint:**

   ```http
   POST /nlp/analyze-full
   ```

   Returns:

   ```json
   {
     "tokens": [...],
     "spans": [...]
   }
   ```

3. **`POST /nlp/lemmas` now returns both shapes.**

   ```json
   {
     "tokens": [
       {"text": "Lev", "lemma": "Lev"},
       {"text": "je", "lemma": "biti"}
     ],
     "lemmas": ["Lev", "biti"]
   }
   ```

4. **`GET /collocations/by-lemma` exists as a documented alias.**
   The old route `GET /collocations` remains valid.

5. **`POST /collocations/phrase` now checks normalized surface and lemma sequence.**
   This helps inflected user phrases match stored collocation component lemmas.

6. **Environment-based lemmatizer URL.**
   `MY_LEMMATIZER_URL` can now be set in systemd or the shell. Default fallback remains the previous hardcoded URL.

7. **Optional CORS support for browser frontends.**
   Set `LINGHUB_CORS_ORIGINS` to a comma-separated list of frontend origins. If this variable is not set, no CORS middleware is enabled.

8. **Deployment helpers added:**

   ```text
   requirements.txt
   deployment/linghub.service
   ```

---

## 2. Frontend developer contract: where to catch data

### Main frontend endpoint for LORIS

```http
POST /nlp/loris-check
```

Request:

```json
{
  "lang": "sl",
  "text": "To smo naredili na štiri roke."
}
```

Response shape:

```json
{
  "tokens": [
    {
      "text": "To",
      "lemma": "ta",
      "pos": "DET",
      "start": 0,
      "end": 2
    }
  ],
  "issues": [
    {
      "rule_id": "paronym_na_štiri_roke",
      "category": "paronym",
      "start": 16,
      "end": 29,
      "priority": 100,
      "payload": {
        "Izhodisce": "na štiri roke",
        "Iscete": "a quattro mani",
        "SteMislili": "štiriročno; dva, dve / v dvoje / oba, obe",
        "Npr": "Na koncertu sta pianistki igrali štiriročno. Pravilnik sta napisali dve članici društva.",
        "Vslo": "SLO leksem štiriročno se uporablja skoraj izključno v glasbi, zlasti pri igranju klavirja."
      }
    }
  ],
  "spans": [
    {
      "type": "multiword_rule",
      "rule_id": "paronym_na_štiri_roke",
      "category": "paronym",
      "match_on": "lemma",
      "trigger": "na štiri roke",
      "start": 16,
      "end": 29,
      "token_start": 3,
      "token_end": 6,
      "token_indexes": [3, 4, 5],
      "surface": "na štiri roke",
      "normalized": "na štiri roke",
      "priority": 100,
      "payload": {
        "Izhodisce": "na štiri roke",
        "Iscete": "a quattro mani",
        "SteMislili": "štiriročno; dva, dve / v dvoje / oba, obe"
      }
    }
  ]
}
```

### What the frontend should use

For legacy warning cards/highlights:

```js
const issues = response.issues ?? [];
```

For rich highlighting and token anchoring:

```js
const spans = response.spans ?? [];
```

Recommended rendering logic: prefer `spans` because it contains the richer structure, but keep the `issues` fallback for older lemmatizer deployments. In this version, `spans` covers both single-token rules (`type: "token_rule"`) and multi-word rules (`type: "multiword_rule"`).

```js
const key = item => `${item.rule_id}:${item.start}:${item.end}`;
const seen = new Set();
const warnings = [];

// Prefer rich span objects.
for (const item of response.spans ?? []) {
  const k = key(item);
  if (seen.has(k)) continue;
  seen.add(k);
  warnings.push(item);
}

// Compatibility fallback: add issues only when no equivalent span exists.
for (const item of response.issues ?? []) {
  const k = key(item);
  if (seen.has(k)) continue;
  seen.add(k);
  warnings.push(item);
}

for (const warning of warnings) {
  const start = warning.start;
  const end = warning.end;
  const ruleId = warning.rule_id;
  const category = warning.category;
  const payload = warning.payload ?? {};
}
```

### Important frontend details

- `start` and `end` are offsets into the original input text.
- Use `start`/`end` for text highlighting.
- `token_start` is inclusive.
- `token_end` is exclusive.
- `token_indexes` is a convenience list, e.g. `[3, 4, 5]`.
- `type` can be `token_rule`, `multiword_rule`, or `named_entity`.
- `match_on` can be `lemma`, `surface`, or `surface_name`.
- `payload` is sparse. Do not assume every key exists.
- Blank spreadsheet cells and slash-only values (`/`) are omitted from payload.
- Use `rule_id` as the stable frontend key when possible.
- Use `payload.Izhodisce` for display if you need the original approved term.

### Payload labels currently used by LORIS

For `category: "paronym"`, common payload fields are:

```text
Izhodisce    internal/display source term; not always shown as a labeled field
Iscete       “IŠČETE PREVOD ZA …?”
SteMislili   “STE MORDA MISLILI …?”
Npr          “NA PRIMER …”
Vslo         “KAJ POMENI V SLOVENŠČINI …?”
Link         optional URL, if present
```

The frontend should render only keys that are present and non-empty.

---

## 3. Other frontend-useful endpoints

### `POST /nlp/analyze`

Token-level analysis only.

```json
[
  {"text": "Lev", "lemma": "Lev", "pos": "PROPN", "start": 0, "end": 3}
]
```

### `POST /nlp/analyze-full`

Token-level analysis plus LORIS/MWU spans.

```json
{
  "tokens": [...],
  "spans": [...]
}
```

Use this when you need MWU spans but do not need the simplified `issues` list.

### `POST /nlp/lemmas`

Lightweight lemma response.

```json
{
  "tokens": [
    {"text": "Lev", "lemma": "Lev"}
  ],
  "lemmas": ["Lev"]
}
```

### `GET /thesaurus/synonyms?lemma=...`

Returns synonym senses for one lemma.

### `GET /collocations/by-lemma?lemma=...&limit=10`

Returns collocations for one lemma. Alias of `GET /collocations`.

### `POST /collocations/phrase`

Checks whether a phrase matches stored collocation evidence.

Request:

```json
{
  "lang": "sl",
  "text": "Shakespeareovo tragedijo"
}
```

Response includes:

```json
{
  "input_text": "Shakespeareovo tragedijo",
  "lang": "sl",
  "tokens": [...],
  "phrase_surface_norm": "shakespeareovo tragedijo",
  "phrase_lemma_sequence": ["shakespearov", "tragedija"],
  "phrase_lemma_norm": "shakespearov tragedija",
  "matches": [
    {
      "surface": "shakespearova tragedija",
      "match_type": "lemma_sequence",
      "matched_by": ["lemma_sequence"],
      "lexical_unit_id": 12345,
      "component_lemmas": ["shakespearov", "tragedija"]
    }
  ]
}
```

Possible `match_type` values:

```text
surface
lemma_sequence
surface_and_lemma_sequence
```

Frontend recommendation: when `lexical_unit_id` exists, use it as the stable identity for collocation evidence. Use `surface` only for display.

### `POST /collocations/from-text`

Extracts content lemmas from a text and returns collocations for those lemmas. This is exploratory evidence, not a validator.

---

## 4. Local development with `venv`

LingHub needs the lemmatizer running first.

### Start `my_lemmatizer`

```bash
cd /path/to/my_lemmatizer
source .venv/bin/activate
uvicorn app:app --host 127.0.0.1 --port 8001 --reload
```

### Prepare LingHub

From the LingHub repo root:

```bash
cd /path/to/LingHub
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip wheel setuptools
pip install -r requirements.txt
```

Make sure the XML data files exist:

```text
LingHub/data/thesaurus.xml
LingHub/data/collocations/collocations_export_*.xml
```

Set the lemmatizer URL:

```bash
export MY_LEMMATIZER_URL=http://127.0.0.1:8001
```

For browser frontend development on another local port, enable CORS explicitly:

```bash
export LINGHUB_CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

Run LingHub:

```bash
uvicorn app:app --host 127.0.0.1 --port 8010 --reload
```

Health check:

```bash
curl http://127.0.0.1:8010/health
```

LORIS test through LingHub:

```bash
curl -s -X POST http://127.0.0.1:8010/nlp/loris-check \
  -H 'Content-Type: application/json' \
  -d '{"lang":"sl","text":"To smo naredili na štiri roke."}' | python -m json.tool
```

---

## 5. Production deployment with `systemctl`

The included template assumes this layout:

```text
/opt/LingHub
/opt/LingHub/.venv
```

It also assumes `my_lemmatizer` is running on:

```text
http://127.0.0.1:8001
```

### Install/update code

```bash
sudo mkdir -p /opt/LingHub
sudo rsync -a --delete ./ /opt/LingHub/
sudo chown -R www-data:www-data /opt/LingHub
```

### Create the virtual environment

```bash
cd /opt/LingHub
sudo -u www-data python3 -m venv .venv
sudo -u www-data .venv/bin/python -m pip install --upgrade pip wheel setuptools
sudo -u www-data .venv/bin/pip install -r requirements.txt
```

### Verify required data files

```bash
sudo test -f /opt/LingHub/data/thesaurus.xml && echo "thesaurus.xml OK"
sudo ls /opt/LingHub/data/collocations/collocations_export_*.xml
```

If those files are missing, LingHub startup will fail because thesaurus and collocations are eager-loaded.

### Install the service

```bash
sudo cp /opt/LingHub/deployment/linghub.service /etc/systemd/system/linghub.service
sudo systemctl daemon-reload
sudo systemctl enable --now linghub.service
```

### Check status and logs

```bash
sudo systemctl status linghub.service
sudo journalctl -u linghub.service -f
```

### Restart after code/config changes

```bash
sudo systemctl restart linghub.service
```

### If you edit the service file

```bash
sudo systemctl daemon-reload
sudo systemctl restart linghub.service
```

### CORS in systemd

If the browser frontend runs on another origin, edit:

```bash
sudo systemctl edit linghub.service
```

Add:

```ini
[Service]
Environment="LINGHUB_CORS_ORIGINS=https://your-frontend.example,http://localhost:5173"
```

Then apply:

```bash
sudo systemctl daemon-reload
sudo systemctl restart linghub.service
```

---

## 6. Reverse proxy note

For production, expose LingHub through Nginx/Caddy/Apache and keep both uvicorn services bound to `127.0.0.1`.

Frontend should call the public reverse-proxy URL, for example:

```text
https://api.example.org/nlp/loris-check
```

The internal services remain:

```text
my_lemmatizer: http://127.0.0.1:8001
LingHub:       http://127.0.0.1:8010
```

---

## Slovenščina

## 1. Kaj se je spremenilo v tej različici

Ta različica predpostavlja nadgrajeni `my_lemmatizer` s podporo za večbesedne razpone in nov uvoz potrjenih LORIS vnosov.

1. **LingHub posreduje novo plast `spans`.**
   `POST /nlp/loris-check` še vedno vrača `tokens` in `issues`, zdaj pa doda še `spans`.

2. **Nov proxy endpoint:**

   ```http
   POST /nlp/analyze-full
   ```

   Vrne:

   ```json
   {
     "tokens": [...],
     "spans": [...]
   }
   ```

3. **`POST /nlp/lemmas` zdaj vrača obe obliki:**

   ```json
   {
     "tokens": [
       {"text": "Lev", "lemma": "Lev"},
       {"text": "je", "lemma": "biti"}
     ],
     "lemmas": ["Lev", "biti"]
   }
   ```

4. **`GET /collocations/by-lemma` je dokumentiran alias.**
   Stari `GET /collocations` ostane delujoč.

5. **`POST /collocations/phrase` preverja normalizirano površino in zaporedje lem.**
   To omogoča, da se sklonjena uporabnikova fraza ujame s shranjenimi lemami komponent.

6. **URL lematizatorja je nastavljiv z okoljsko spremenljivko.**
   Uporabi `MY_LEMMATIZER_URL`.

7. **CORS za browser frontend je ekspliciten.**
   Uporabi `LINGHUB_CORS_ORIGINS`, npr. `http://localhost:5173`. Če spremenljivka ni nastavljena, CORS middleware ni vklopljen.

---

## 2. Kje frontend ujame podatke

Glavni endpoint za LORIS:

```http
POST /nlp/loris-check
```

Frontend naj bere:

```js
const issues = response.issues ?? [];
const spans = response.spans ?? [];
```

Za novi prikaz uporabi predvsem `spans`, ker vsebujejo bogatejšo strukturo. V tej različici `spans` pokrivajo enobesedna pravila (`type: "token_rule"`) in večbesedna pravila (`type: "multiword_rule"`). `issues` ostane varnostni fallback za starejše namestitve:

```js
const key = item => `${item.rule_id}:${item.start}:${item.end}`;
const seen = new Set();
const warnings = [];

for (const item of response.spans ?? []) {
  const k = key(item);
  if (seen.has(k)) continue;
  seen.add(k);
  warnings.push(item);
}

for (const item of response.issues ?? []) {
  const k = key(item);
  if (seen.has(k)) continue;
  seen.add(k);
  warnings.push(item);
}
```

Za označevanje v besedilu:

```js
warning.start
warning.end
```

Za sidranje na tokene:

```js
warning.token_indexes
warning.token_start
warning.token_end // ekskluziven indeks
```

Če prikazuješ tako `spans` kot `issues`, odstrani podvojitve:

```js
const key = item => `${item.rule_id}:${item.start}:${item.end}`;
```

Pomembno:

- `payload` je namenjen prikazu uporabniku.
- Ne predpostavljaj, da obstajajo vsa polja.
- Prazne celice in vrednosti `/` iz preglednice so izpuščene.
- Za stabilen ključ uporabi `rule_id`.
- Za prikaz izhodiščnega izraza uporabi `payload.Izhodisce`, če obstaja.

---

## 3. Lokalni zagon z `venv`

Najprej zaženi `my_lemmatizer` na portu `8001`.

Potem LingHub:

```bash
cd /path/to/LingHub
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip wheel setuptools
pip install -r requirements.txt
export MY_LEMMATIZER_URL=http://127.0.0.1:8001
export LINGHUB_CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
uvicorn app:app --host 127.0.0.1 --port 8010 --reload
```

Preverjanje:

```bash
curl http://127.0.0.1:8010/health
```

---

## 4. Produkcijska namestitev s `systemctl`

Predpostavljena pot:

```text
/opt/LingHub
```

Ukazi:

```bash
sudo mkdir -p /opt/LingHub
sudo rsync -a --delete ./ /opt/LingHub/
sudo chown -R www-data:www-data /opt/LingHub

cd /opt/LingHub
sudo -u www-data python3 -m venv .venv
sudo -u www-data .venv/bin/python -m pip install --upgrade pip wheel setuptools
sudo -u www-data .venv/bin/pip install -r requirements.txt

sudo cp /opt/LingHub/deployment/linghub.service /etc/systemd/system/linghub.service
sudo systemctl daemon-reload
sudo systemctl enable --now linghub.service
sudo systemctl status linghub.service
```

Logi:

```bash
sudo journalctl -u linghub.service -f
```

Če frontend teče na drugi domeni/portu, nastavi CORS:

```bash
sudo systemctl edit linghub.service
```

Dodaj:

```ini
[Service]
Environment="LINGHUB_CORS_ORIGINS=https://tvoj-frontend.example,http://localhost:5173"
```

Uveljavi:

```bash
sudo systemctl daemon-reload
sudo systemctl restart linghub.service
```
