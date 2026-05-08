# MWU / multi-word-unit changes

## Slovenščina

Ta različica popravi glavno težavo pri večbesednih enotah: pravila, ki imajo sprožilec z več besedami, se ne preverjajo več kot navadno enobesedno pravilo.

### Kaj se je spremenilo v `my_lemmatizer`

1. **Ločeni indeksi za enobesedne in večbesedne sprožilce.**

   Prejšnji sistem je vse `lemma` sprožilce shranil v `lemma_index`, vse `surface` sprožilce pa v `surface_index`. To deluje za `abonma`, `otrok`, `lučka`, ne pa za sprožilce, kot so:

   - `z otroci`
   - `odpri lučke`
   - `compact disc`
   - `okoristiti se`

   Zdaj se sprožilci z več besedami shranijo v posebne indekse:

   - `lemma_phrase_index`
   - `surface_phrase_index`
   - `surface_name_index`

2. **Dodano je token-window ujemanje.**

   Sistem zdaj preverja zaporedja tokenov, ne samo posameznih tokenov. Primer:

   ```text
   tokeni:        z | otroci
   površina:      z otroci
   sprožilec:     z otroci
   rezultat:      zadetek čez oba tokena
   ```

   Pri lematskih sprožilcih se primerja zaporedje lem:

   ```text
   tokeni:        okoristil | se
   leme:          okoristiti | se
   sprožilec:     okoristiti se
   rezultat:      zadetek čez oba tokena
   ```

3. **`surface_name` ni več navaden `text.find()`.**

   Toponimi in imena se zdaj iščejo po tokenih in izvornih znakovnih razponih. To je bolj varno, ker ne zadene več sredi besede. Hkrati je bolj tolerantno do velikih/malih črk in odvečnih presledkov.

4. **`/loris/check` ima novo polje `spans`.**

   Stari izhod ostane:

   ```json
   {
     "tokens": [...],
     "issues": [...]
   }
   ```

   Dodano je:

   ```json
   {
     "spans": [
       {
         "type": "multiword_rule",
         "rule_id": "slogovni_z otroci",
         "category": "slogovni",
         "match_on": "surface",
         "trigger": "z otroci",
         "start": 0,
         "end": 8,
         "token_start": 0,
         "token_end": 2,
         "token_indexes": [0, 1],
         "surface": "z otroci",
         "normalized": "z otroci",
         "priority": 70,
         "payload": {...}
       }
     ]
   }
   ```

   `token_end` je ekskluziven, enako kot Python slicing: `tokens[token_start:token_end]`.

5. **Dodano je `/analyze-full`.**

   `/analyze` ostane nespremenjen in vrača samo seznam tokenov. Novi endpoint:

   ```text
   POST /analyze-full
   ```

   vrača:

   ```json
   {
     "tokens": [...],
     "spans": [...]
   }
   ```

   Namenjen je frontendu in LingHubu, kadar potrebujeta samo tokenizacijo + span/MWU plast, ne pa celotnega LORIS seznama opozoril.

### Vpliv na frontend

- `POST /analyze` je nespremenjen.
- `POST /analyze-full` je novo mesto za `tokens` + `spans`.
- `POST /loris/check` še vedno vrača `tokens` in `issues`, zdaj pa tudi `spans`.
- Frontend, ki uporablja samo `issues[*].start` in `issues[*].end`, lahko deluje naprej.
- Novo: nekateri `issues` zdaj pokrivajo več besed, ne samo en token. Označevalnik mora zato podpirati razpone, ki vsebujejo presledke.
- Novo: za boljši prikaz večbesednih pravil naj frontend bere tudi `spans`.
- Za token-chip UI uporabi `spans[*].token_indexes` ali `tokens[span.token_start:span.token_end]`.
- Za klasično označevanje v besedilu uporabi `spans[*].start` in `spans[*].end` oziroma enaka polja v `issues`.

## English

This version fixes the main multi-word-unit problem: triggers containing more than one word are no longer treated as if they were single-token triggers.

### What changed in `my_lemmatizer`

1. **Single-token and multi-token triggers are indexed separately.**

   The old system placed all `lemma` triggers in `lemma_index` and all `surface` triggers in `surface_index`. That works for `abonma`, `otrok`, `lučka`, but not for triggers such as:

   - `z otroci`
   - `odpri lučke`
   - `compact disc`
   - `okoristiti se`

   Multi-word triggers now go into dedicated indexes:

   - `lemma_phrase_index`
   - `surface_phrase_index`
   - `surface_name_index`

2. **Token-window matching was added.**

   The matcher now checks sequences of tokens, not only individual tokens. Example:

   ```text
   tokens:        z | otroci
   surface span:  z otroci
   trigger:       z otroci
   result:        one match covering both tokens
   ```

   Lemma triggers compare lemma sequences:

   ```text
   tokens:        okoristil | se
   lemmas:        okoristiti | se
   trigger:       okoristiti se
   result:        one match covering both tokens
   ```

3. **`surface_name` no longer uses raw `text.find()`.**

   Toponyms and names are matched by token windows and original character spans. This avoids accidental matches inside longer words and is more tolerant of casing and whitespace.

4. **`/loris/check` now includes a `spans` field.**

   The old output remains:

   ```json
   {
     "tokens": [...],
     "issues": [...]
   }
   ```

   The new field is:

   ```json
   {
     "spans": [
       {
         "type": "multiword_rule",
         "rule_id": "slogovni_z otroci",
         "category": "slogovni",
         "match_on": "surface",
         "trigger": "z otroci",
         "start": 0,
         "end": 8,
         "token_start": 0,
         "token_end": 2,
         "token_indexes": [0, 1],
         "surface": "z otroci",
         "normalized": "z otroci",
         "priority": 70,
         "payload": {...}
       }
     ]
   }
   ```

   `token_end` is exclusive, just like Python slicing: `tokens[token_start:token_end]`.

5. **`/analyze-full` was added.**

   `/analyze` remains unchanged and still returns only the token list. The new endpoint:

   ```text
   POST /analyze-full
   ```

   returns:

   ```json
   {
     "tokens": [...],
     "spans": [...]
   }
   ```

   It is meant for the frontend and LingHub when they need tokenization plus the span/MWU layer, but not the full LORIS warning list.

### Frontend data-contract impact

- `POST /analyze` is unchanged.
- `POST /analyze-full` is the new catch point for `tokens` + `spans`.
- `POST /loris/check` still returns `tokens` and `issues`, and now also returns `spans`.
- A frontend that only reads `issues[*].start` and `issues[*].end` can continue working.
- New: some `issues` now cover several words, not just one token. The highlighter must support character ranges that include spaces.
- New: for richer MWU display, read `spans` as well.
- For token-chip UI, use `spans[*].token_indexes` or `tokens[span.token_start:span.token_end]`.
- For plain text highlighting, use `spans[*].start` and `spans[*].end`, or the same fields in `issues`.

---

# LingHub-specific MWU changes

## Slovenščina

### Kaj se je spremenilo v `LingHub`

1. **`/nlp/loris-check` zdaj posreduje tudi `spans`.**

   LingHub ne spreminja večbesednih zadetkov iz lematizatorja, ampak jih posreduje frontendu. Če `my_lemmatizer` vrne:

   ```json
   {
     "tokens": [...],
     "issues": [...],
     "spans": [...]
   }
   ```

   potem `POST /nlp/loris-check` v LingHubu vrne isto strukturo.

2. **`/collocations/phrase` zdaj preverja tudi zaporedje lem.**

   Prejšnja logika je preverjala predvsem normalizirano površinsko obliko. To je krhko pri pregibanju.

   Zdaj endpoint primerja:

   - normalizirano površino uporabnikove fraze,
   - zaporedje lem uporabnikove fraze,
   - zaporedje lem komponent v kolokacijskem XML.

   Zato lahko pregibana vhodna fraza zadene kolokacijo, tudi če površina ni popolnoma enaka shranjeni površini.

3. **`matches[*]` ima nova polja.**

   Primer:

   ```json
   {
     "match_type": "lemma_sequence",
     "matched_by": ["lemma_sequence"],
     "component_lemmas": ["shakespearov", "tragedija"],
     "components": [...],
     "lexical_unit_id": 12345
   }
   ```

   Vrednosti `match_type`:

   - `surface` — zadetek po normalizirani površini,
   - `lemma_sequence` — zadetek po zaporedju lem,
   - `surface_and_lemma_sequence` — oba načina sta se ujemala.

4. **`/collocations/by-lemma` je dodan kot alias.**

   Stari endpoint `GET /collocations?lemma=...` še vedno deluje. Dodan je še dokumentacijsko bolj jasen alias:

   ```text
   GET /collocations/by-lemma?lemma=...
   ```

5. **`/nlp/lemmas` zdaj vrača obe obliki.**

   Prejšnja koda je vračala `tokens`, README pa je opisoval `lemmas`. Zdaj endpoint vrača oboje:

   ```json
   {
     "tokens": [
       {"text": "Lev", "lemma": "Lev"}
     ],
     "lemmas": ["Lev"]
   }
   ```

### Vpliv na frontend v LingHubu

Najpomembnejše mesto za frontend je:

```text
POST /nlp/loris-check
```

Tu naj UI še naprej bere:

```text
issues[*].start
issues[*].end
issues[*].payload
```

Za večbesedne zadetke pa naj dodatno bere:

```text
spans[*].start
spans[*].end
spans[*].token_indexes
spans[*].match_on
spans[*].trigger
```

Drugo pomembno mesto je:

```text
POST /collocations/phrase
```

Tu frontend/chatbot lahko zdaj razlikuje:

```text
matches[*].match_type = surface
matches[*].match_type = lemma_sequence
matches[*].match_type = surface_and_lemma_sequence
```

Priporočilo: če obstaja `lexical_unit_id`, ga uporabi kot stabilen ključ za kolokacijski zadetek. Ne uporabljaj samo površinske oblike kot identitete, ker se površinske oblike lahko razlikujejo po pregibanju, velikosti črk ali normalizaciji.

## English

### What changed in `LingHub`

1. **`/nlp/loris-check` now forwards `spans`.**

   LingHub does not reinterpret the lemmatizer's multi-word matches. It forwards them to the frontend. If `my_lemmatizer` returns:

   ```json
   {
     "tokens": [...],
     "issues": [...],
     "spans": [...]
   }
   ```

   then `POST /nlp/loris-check` in LingHub returns the same structure.

2. **`/collocations/phrase` now checks lemma sequences.**

   The previous logic mainly checked normalized surface strings. That is brittle for inflected input.

   The endpoint now compares:

   - the normalized surface of the user's phrase,
   - the lemma sequence of the user's phrase,
   - the lemma sequence of the XML collocation components.

   This lets an inflected user phrase match a collocation even when the stored surface is not identical.

3. **`matches[*]` has new fields.**

   Example:

   ```json
   {
     "match_type": "lemma_sequence",
     "matched_by": ["lemma_sequence"],
     "component_lemmas": ["shakespearov", "tragedija"],
     "components": [...],
     "lexical_unit_id": 12345
   }
   ```

   `match_type` values:

   - `surface` — matched by normalized surface,
   - `lemma_sequence` — matched by lemma sequence,
   - `surface_and_lemma_sequence` — both methods matched.

4. **`/collocations/by-lemma` was added as an alias.**

   The old endpoint `GET /collocations?lemma=...` still works. A clearer documented alias was added:

   ```text
   GET /collocations/by-lemma?lemma=...
   ```

5. **`/nlp/lemmas` now returns both shapes.**

   The code used to return `tokens`, while the README described `lemmas`. The endpoint now returns both:

   ```json
   {
     "tokens": [
       {"text": "Lev", "lemma": "Lev"}
     ],
     "lemmas": ["Lev"]
   }
   ```

### Frontend data-contract impact in LingHub

The main frontend endpoint is:

```text
POST /nlp/loris-check
```

The UI can keep reading:

```text
issues[*].start
issues[*].end
issues[*].payload
```

For multi-word matches, additionally read:

```text
spans[*].start
spans[*].end
spans[*].token_indexes
spans[*].match_on
spans[*].trigger
```

The second important endpoint is:

```text
POST /collocations/phrase
```

The frontend/chatbot can now distinguish:

```text
matches[*].match_type = surface
matches[*].match_type = lemma_sequence
matches[*].match_type = surface_and_lemma_sequence
```

Recommendation: when `lexical_unit_id` exists, use it as the stable key for a collocation match. Do not use only the surface form as the identity, because surface forms can vary by inflection, casing, or normalization.
