# Tomorrow LingHub deployment note

This bundle differs from the raw repo in one important safety setting:

- `LINGHUB_ENABLE_COLLOCATIONS=0` disables eager loading of the large collocation XML shards.
- `LINGHUB_ENABLE_COLLOCATIONS=1` enables collocation loading after the files are present.

Deploy code first with collocations disabled. Push XML shards. Then enable collocations and restart LingHub.
