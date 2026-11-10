# Authenticated LingHub dictionary API

Base URL: `https://klikai.jeziknaklik.it/api/linghub/v1/`.
Use the existing application credential in `Authorization: Bearer CLIENT_ID.SECRET`.
Application credentials are separate from a KLIKai browser login. The authenticated
`openapi.json` describes the public routes; `capabilities.application_api` describes
the public dictionary page limits separately from the local exploration limits.

## Collocations: larger pages and whole-result ordering

```text
GET /collocations?lemma=odločitev&limit=100&offset=0&sort=logdice&order=desc
```

`limit` accepts 1–500 occurrences; an omitted limit retains the existing public
default of 8. `offset` is zero based. The database applies all filters and the
requested ordering to the whole matching inventory before selecting the page.
Use `sort=freq` or `sort=logdice`, with `order=asc` or `order=desc`, to change the
whole-result ranking. Sorting only the visible first page in a browser would
not establish a different global ranking.

The additive `pagination` object contains:

| Field | Meaning |
| --- | --- |
| `unit` | `occurrence`; matching text alone does not merge distinct source records |
| `offset`, `limit` | The requested page position and maximum size |
| `total` | Exact count matching the current lemma, kind and other filters |
| `source_total` | The earlier pre-filter source count, also retained as `count_total` |
| `count_returned` | Number of records in this page |
| `has_more` | Whether matching records follow this page |
| `next_offset` | Position for the next page, or `null` when finished |

Keep the same lemma, `kind`, filters, `sort` and `order` while following
`next_offset`. Existing `count_total` keeps its original meaning; for a filtered
interface's page count use `pagination.total`. A page past the end is empty,
with `has_more=false`. Known measurements, including a recorded zero, precede
missing measurements; missing values retain their original nulls and do not
acquire a numeric rank. The existing ranking counts describe the returned page.

All source fields remain available: occurrence/sense/lexical-unit identities,
component forms, lemmas, native MSDs, source attributes and order, preparation
status, frequency, logDice and provenance. `grammaticalCombination` remains
distinct from `collocation`; select `kind=all` explicitly to request both.

Example of collecting the ordered pages in Python:

```python
import json, os
from urllib.parse import urlencode
from urllib.request import Request, urlopen

base = "https://klikai.jeziknaklik.it/api/linghub/v1/collocations"
headers = {"Authorization": "Bearer " + os.environ["LINGHUB_API_TOKEN"]}
params = {"lemma": "odločitev", "limit": 100, "offset": 0,
          "sort": "logdice", "order": "desc"}
rows = []
while True:
    with urlopen(Request(base + "?" + urlencode(params), headers=headers), timeout=40) as response:
        page = json.load(response)
    rows.extend(page["collocations"])
    next_offset = page["pagination"]["next_offset"]
    if next_offset is None:
        break
    params["offset"] = next_offset
```

## Synonyms: retain source entries and sense groups

```text
GET /synonyms?lemma=3D&limit=1&offset=0
```

For synonyms, `limit` and `offset` count source **entries**, not individual
synonym words or relations. The existing public default is 8 entries and the
maximum is 20. A single entry may contain several sense groups and many
synonym/antonym relations; these are not flattened or combined across senses.

`pagination.unit` is `entry`; `total` counts the matching source entries.
`returned_senses` and `returned_relations` count what this page actually contains.
Existing `count_entries`, `count_senses` and `count_relations` continue describing
the original full lookup before the bounded projection. Source-entry metadata,
raw sense metadata, relation types, scores, target IDs and resolution flags are
retained.

The existing separate bounds of 20 senses per entry and 100 relations per sense
remain. `truncated_within_entries=true` identifies those nested omissions;
entry pagination does not retrieve a later slice of a truncated sense or relation
list. `has_more=false` only finishes entry paging and must not be interpreted as
global completeness when that nested flag is true.

## Compatibility and existing limits

Pagination metadata is enabled by default on the authenticated application API.
`include_pagination=false` with `offset=0` retains the previous response shape.
Local versioned dictionary routes enable the metadata explicitly; legacy routes
and KLIKai's existing internal dictionary calls retain their previous defaults.

The response bound remains 2 MiB: use a smaller page if `response_bound` is
returned. Existing application scopes, stable/experimental selection, rate limits
and concurrency policy remain in force. No submitted-text storage, generated
source descriptions or new language-model call is introduced by paging.
