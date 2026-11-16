from pilot_backend import terminology


def hit(status):
    return {"title": "termine → termin", "source_display_name": "Zvezek", "source_slug": "zvezek",
            "external_id": "zvezek:2026-11-11-file-sha:1", "citation_id": "zvezek:2026-11-11-file-sha:1",
            "record_kind": "terminology_concept", "rights_mode": "restricted", "categories": [],
            "subcategories": [], "original_terms": ["termine"], "standardized_terms": [],
            "proposals": ["termin"] if status == "predlog" else [], "rationale": [],
            "recorded_terms": ["termin"] if status is None else [], "record_status": status,
            "record_status_source": [{"label": "Zapisnik", "url": "https://example.test/source"}],
            "release_id": "source-edition-test", "source_revision": "a" * 64,
            "source_date": "2026-11-11", "matched_forms": [{"term": "termine", "field": "term",
            "language": "it", "match_tier": "exact", "ambiguity_cost": 0, "matched_query": "termine"}]}


def test_source_status_is_visible_in_both_languages_without_promotion():
    for locale, absent in [("sl", "ni naveden"), ("it", "non indicato")]:
        proposed = terminology.record_text(hit("predlog"), locale)
        blank = terminology.record_text(hit(None), locale)
        assert "predlog" in proposed and "termin" in proposed
        assert absent in blank and "termin" in blank
        assert "Standardizirane oblike" not in proposed + blank
        assert "Forme standardizzate" not in proposed + blank


def test_frozen_source_reader_keeps_edition_status_and_references():
    data = {"query": "termine", "sources": ["zvezek"], "hits": [hit("predlog")]}
    bundle = terminology.freeze_lookup(data, "termine", "sl")["bundle"]
    eid = terminology.evidence(bundle)[0]["evidence_id"]
    before = terminology.source(bundle, eid, "short")
    data["hits"][0]["record_status"] = "potrjeno"
    after = terminology.source(bundle, eid, "short")
    assert before == after
    assert after["bibliography"]["record_status"] == "predlog"
    assert after["bibliography"]["record_status_source"] == hit("predlog")["record_status_source"]
    assert after["bibliography"]["release_id"] == "source-edition-test"
