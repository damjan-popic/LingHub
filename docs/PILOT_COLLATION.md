# Task-relevant tool evidence

The existing composer accepts an explicitly requested operation and one quoted
focus. The existing text-language selector is also available for reference
questions; answer locale does not choose the analyzer language. Missing language
or an ambiguous focus produces a scoped clarification. Ordinary reference
questions keep their reference route. The deterministic Terminology mode remains
unchanged and does not queue a writer.

Supported operation wording covers terminology/equivalents, morphology/inflection,
LORIS, synonyms and collocations in Slovenian and Italian. Only those requested
operations are called. This is transparent operational routing, not a linguistic
ontology assignment or a classifier based on evaluation question IDs.

CPU calls use the accepted exact terminology lookup and LingHub `/v1/explore`.
Full CPU captures and hashes are frozen once. Resource identity is checked before
and after collection. The KLIKai private gateway validates and signs the typed
records alongside the family’s pinned reference evidence. Source access continues
to require family ownership. Existing families and their bodies are not modified.

Term records remain separate, including domain, originals, standardized forms and
proposals. Lexical morphology remains candidate evidence with native MSD tags.
LORIS advice addresses its exact original span. Synonyms retain directed relation
and sense groups; collocations retain their separate dictionary identity and
recorded statistics. Equal IDs across dictionaries do not establish sense or
domain alignment. A writer may explain source-supported distinctions and must
ask for context when the evidence cannot resolve the intended sense.

A read-only demonstration, run from this checkout with its existing pilot Python:

```bash
/data/envs/lingstack/pilot/bin/python -m pilot_backend.collation \
  --question 'Per il termine «delibere», trova gli equivalenti sloveni e verifica con la morfologia la forma base italiana.' \
  --input-language it --locale it --output /private/new-directory/term-evidence.json
```

The output parent must already be private. This command writes only its new
receipt, calls no model, changes no database and does not activate the adapter.
The full integrated model/browser check remains a separate release gate.
