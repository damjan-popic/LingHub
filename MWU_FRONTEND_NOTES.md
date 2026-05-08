# MWU and frontend data-catch changes

## Slovensko

LingHub je zdaj pripravljen na to, da lematizator vrača poleg tokenov tudi razpone (`spans`). Glavna sprememba je, da večbesedne enote niso več skrite kot “leme s presledki” ali kot posebni primeri v posameznih modulih, ampak se lahko prek API-ja prenesejo kot samostojni razponi čez tokenizirano besedilo.

### Kaj se je spremenilo v LingHubu

1. **Nov proxy endpoint `/nlp/analyze-full`**

   LingHub kliče `my_lemmatizer` endpoint `/analyze-full` in vrne:

   ```json
   {
     "tokens": [...],
     "spans": [...]
   }
   ```

   Če lematizator še ne bi imel `/analyze-full`, ima klient v `core/clients.py` varnostni fallback na `/analyze`, pri čemer vrne prazen seznam `spans`.

2. **`/nlp/loris-check` zdaj posreduje tudi `spans`**

   Frontend lahko še vedno bere:

   ```js
   response.issues
   ```

   Za večbesedne razpone pa naj bere:

   ```js
   response.spans
   ```

3. **`/nlp/lemmas` je usklajen z dokumentacijo in staro rabo**

   Endpoint zdaj vrača obe obliki:

   ```json
   {
     "tokens": [
       {"text": "Lev", "lemma": "Lev"},
       {"text": "je", "lemma": "biti"}
     ],
     "lemmas": ["Lev", "biti"]
   }
   ```

   Tako ni treba izbrati med staro obliko `tokens` in enostavnim seznamom `lemmas`.

4. **`/collocations/by-lemma` je dodan kot alias**

   Deluje tako:

   ```text
   GET /collocations/by-lemma?lemma=...
   ```

   Stari endpoint ostane:

   ```text
   GET /collocations?lemma=...
   ```

5. **`/collocations/from-text` vrača tudi `tokens`**

   To frontend ali chatbotu omogoča, da vidi, iz katerih tokenov so bile izluščene leme za poizvedbo po kolokacijah.

6. **`/collocations/phrase` zdaj preverja tudi zaporedje lem**

   Prejšnja logika je preverjala predvsem normalizirano površinsko obliko. Zdaj preverja:

   - površinsko ujemanje (`surface`);
   - lemsko zaporedje (`lemma_sequence`);
   - oboje hkrati (`surface_and_lemma_sequence`).

   To pomeni, da lahko uporabnikov sklonjeni vnos zadene kolokacijo, če se njegovo zaporedje lem ujema s komponentami kolokacije.

   Primer odgovora:

   ```json
   {
     "input_text": "...",
     "phrase_surface_norm": "...",
     "phrase_lemma_sequence": ["shakespearov", "tragedija"],
     "phrase_lemma_norm": "shakespearov tragedija",
     "matches": [
       {
         "headword": "shakespearov",
         "rank": 1,
         "match_type": "lemma_sequence",
         "matched_by": ["lemma_sequence"],
         "lexical_unit_id": 123,
         "surface": "shakespearova tragedija",
         "component_lemmas": ["shakespearov", "tragedija"],
         "components": [...]
       }
     ],
     "evidence": {...}
   }
   ```

### Kje naj frontend “ujame” nove podatke

Za LORIS opozorila:

```js
const issues = response.issues ?? [];
const spans = response.spans ?? [];
```

- `issues` so seznam opozoril.
- `spans` so natančni razponi, posebej pomembni pri večbesednih enotah.

Za označevanje v besedilu:

```js
span.start
span.end
```

Za povezovanje s tokeni:

```js
span.token_indexes
span.token_start
span.token_end
```

Za odločitev, zakaj se je span ujel:

```js
span.match_on
// "surface", "lemma", "surface_name"
```

Za kolokacijsko preverjanje fraz:

```js
response.matches[i].match_type
response.matches[i].matched_by
response.matches[i].component_lemmas
response.phrase_lemma_sequence
```

Najpomembnejša sprememba za UI: večbesednih enot ni več treba rekonstruirati iz posameznih tokenov. UI lahko neposredno uporabi `spans` oziroma `matches`.

---

## English

LingHub is now prepared to receive a span layer from the lemmatizer in addition to tokens. The main change is that multi-word units are no longer hidden as “lemmas with spaces” or handled only as special cases inside separate modules. They can now travel through the API as first-class spans over tokenized text.

### What changed in LingHub

1. **New proxy endpoint `/nlp/analyze-full`**

   LingHub calls `my_lemmatizer` endpoint `/analyze-full` and returns:

   ```json
   {
     "tokens": [...],
     "spans": [...]
   }
   ```

   If the lemmatizer has not yet been upgraded, `core/clients.py` falls back to `/analyze` and returns an empty `spans` array.

2. **`/nlp/loris-check` now forwards `spans`**

   Existing frontend code can still read:

   ```js
   response.issues
   ```

   Multi-word spans should be read from:

   ```js
   response.spans
   ```

3. **`/nlp/lemmas` is now both README-compatible and backward-compatible**

   It returns both:

   ```json
   {
     "tokens": [
       {"text": "Lev", "lemma": "Lev"},
       {"text": "je", "lemma": "biti"}
     ],
     "lemmas": ["Lev", "biti"]
   }
   ```

4. **`/collocations/by-lemma` was added as an alias**

   This now works:

   ```text
   GET /collocations/by-lemma?lemma=...
   ```

   The old route remains available:

   ```text
   GET /collocations?lemma=...
   ```

5. **`/collocations/from-text` now returns `tokens`**

   This lets the frontend or chatbot see which token analysis produced the lemma list used for collocation lookup.

6. **`/collocations/phrase` now checks lemma sequences**

   The previous logic mostly checked the normalized surface form. The endpoint now checks:

   - surface match (`surface`);
   - lemma-sequence match (`lemma_sequence`);
   - both (`surface_and_lemma_sequence`).

   This lets an inflected user phrase match a stored collocation when the user phrase lemma sequence equals the collocation component lemma sequence.

### Where the frontend should catch the new data

For LORIS warnings:

```js
const issues = response.issues ?? [];
const spans = response.spans ?? [];
```

- `issues` are the warning list.
- `spans` are the precise text ranges, especially important for MWUs.

For text highlighting:

```js
span.start
span.end
```

For token anchoring:

```js
span.token_indexes
span.token_start
span.token_end
```

For knowing how the span matched:

```js
span.match_on
// "surface", "lemma", "surface_name"
```

For collocation phrase checking:

```js
response.matches[i].match_type
response.matches[i].matched_by
response.matches[i].component_lemmas
response.phrase_lemma_sequence
```

The most important UI change: the frontend no longer has to reconstruct multi-word units from individual tokens. It can use `spans` or `matches` directly.
