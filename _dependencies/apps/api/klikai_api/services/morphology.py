"""Read-only native Python access to immutable SL/IT morphology lexicons."""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import sqlite3
import unicodedata
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

from klikai_api.schemas.morphology import (
    InflectionGroup,
    LemmaCandidate,
    MorphologyLanguage,
    MorphologyRelease,
    MorphSignature,
    MorphTokenDetail,
)

LEXICON_RELEASE_SCHEMA = "klikai.lexicon-release.v1"
LEXICON_FINGERPRINT_SCHEMA = "klikai.lexicon-release-fingerprint.v1"
LEXICON_SCHEMA_VERSION = "1"
NORMALIZATION_VERSION = "nfkc-lower-ws-v1"
SUPPORTED_LANGUAGES = frozenset({"sl", "it"})
TOKEN_RE = re.compile(
    r"https?://[^\s]+|[^\s@]+@[^\s@]+\.[^\s@]+|[^\W_]+(?:[-’'][^\W_]+)*",
    re.UNICODE,
)
GENERIC_SINGLE_TOKENS = frozenset(
    {
        "kaj",
        "kako",
        "je",
        "po",
        "se",
        "pomeni",
        "prevedi",
        "reče",
        "rece",
        "slovensko",
        "italijansko",
    }
)
REQUIRED_METADATA = frozenset(
    {
        "schema_version",
        "normalization_version",
        "language",
        "source_name",
        "source_version",
        "source_identifier_or_handle",
        "source_url_or_pinned_commit",
        "source_file_sha256",
        "source_file_md5_if_published",
        "source_license",
        "builder_version",
        "source_rows_seen",
        "rows_inserted",
        "exact_duplicate_rows_deduplicated",
        "distinct_forms",
        "distinct_lemmas",
        "built_at_utc",
    }
)


class MorphologyError(RuntimeError):
    """Base class for morphology artifact failures."""


class MorphologyUnavailableError(MorphologyError):
    """Raised when a required release is absent."""


class MorphologyValidationError(MorphologyError):
    """Raised when an existing release is corrupt, stale or incompatible."""


def normalize_text(value: str) -> str:
    """Match Node: NFKC, Unicode lowercase, trim and whitespace collapse."""

    return " ".join(unicodedata.normalize("NFKC", value).lower().split())


def fold_diacritics(value: str) -> str:
    """Match Node: NFKD, drop all combining marks, then NFC."""

    return unicodedata.normalize(
        "NFC",
        "".join(
            character
            for character in unicodedata.normalize("NFKD", value)
            if not unicodedata.category(character).startswith("M")
        ),
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def release_fingerprint_inputs(manifest: Mapping[str, Any]) -> dict[str, object]:
    """Select the canonical immutable inputs that identify built lexicon bytes."""

    upstream = manifest.get("upstream")
    languages = manifest.get("languages")
    if not isinstance(upstream, Mapping) or not isinstance(languages, Mapping):
        raise MorphologyValidationError("Morphology manifest lacks fingerprint inputs")
    language_inputs: dict[str, object] = {}
    for language in sorted(SUPPORTED_LANGUAGES):
        item = languages.get(language)
        if not isinstance(item, Mapping):
            raise MorphologyValidationError(f"Missing {language} fingerprint input")
        source = item.get("source")
        if not isinstance(source, Mapping):
            raise MorphologyValidationError(f"Missing {language} source identity")
        language_inputs[language] = {
            "source": dict(source),
            "sqlite": {
                "sha256": item.get("sha256"),
                "size_bytes": item.get("size_bytes"),
            },
        }
    return {
        "fingerprint_schema_version": LEXICON_FINGERPRINT_SCHEMA,
        "release_schema_version": manifest.get("schema_version"),
        "sqlite_schema_version": manifest.get("sqlite_schema_version"),
        "normalization_version": manifest.get("normalization_version"),
        "patched_upstream_tree_sha256": upstream.get("patched_tree_sha256"),
        "upstream_patch_sha256": upstream.get("upstream_patch_sha256"),
        "languages": language_inputs,
    }


def calculate_release_fingerprint(manifest: Mapping[str, Any]) -> str:
    """Return the SHA-256 of canonical release inputs, independent of JSON layout."""

    canonical = json.dumps(
        release_fingerprint_inputs(manifest),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def release_id_from_fingerprint(fingerprint: str) -> str:
    """Derive the immutable directory/release identifier from a full fingerprint."""

    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ValueError("Release fingerprint must be a lowercase SHA-256")
    return f"lexicons-{NORMALIZATION_VERSION}-{fingerprint[:24]}"


def tokenize(value: str) -> tuple[str, ...]:
    return tuple(match.group(0) for match in TOKEN_RE.finditer(value))


def query_spans(
    value: str,
    *,
    max_spans: int,
    max_words: int,
) -> tuple[str, ...]:
    """Return deterministic longest-first token spans with safe caps."""

    tokens = tokenize(value)
    spans: list[str] = []
    for size in range(min(max_words, len(tokens)), 0, -1):
        for start in range(0, len(tokens) - size + 1):
            span = " ".join(tokens[start : start + size])
            normalized = normalize_text(span)
            if size == 1 and normalized in GENERIC_SINGLE_TOKENS:
                continue
            if normalized and normalized not in spans:
                spans.append(normalized)
            if len(spans) >= max_spans:
                return tuple(spans)
    return tuple(spans)


class MorphologyService:
    """Validated short-lived SQLite queries over an immutable lexicon release."""

    def __init__(
        self,
        release_dir: str | Path,
        *,
        required: bool = False,
        max_token_candidates: int = 4,
        max_signatures_per_phrase: int = 32,
        max_query_spans: int = 64,
        max_words_per_span: int = 6,
        require_directory_identity: bool = True,
    ) -> None:
        self.release_dir = Path(release_dir)
        self.required = required
        self.max_token_candidates = max_token_candidates
        self.max_signatures_per_phrase = max_signatures_per_phrase
        self.max_query_spans = max_query_spans
        self.max_words_per_span = max_words_per_span
        self.require_directory_identity = require_directory_identity
        for name, value in (
            ("max_token_candidates", max_token_candidates),
            ("max_signatures_per_phrase", max_signatures_per_phrase),
            ("max_query_spans", max_query_spans),
            ("max_words_per_span", max_words_per_span),
        ):
            if value < 1:
                raise ValueError(f"{name} must be positive")
        self._release: MorphologyRelease | None = None
        self._deep_validated_fingerprint: str | None = None

    def _missing(self, message: str) -> MorphologyRelease | None:
        if self.required:
            raise MorphologyUnavailableError(message)
        return None

    def _clear_cached_release(self) -> None:
        """Discard every cache whose contents depend on the active release."""

        self._release = None
        self._deep_validated_fingerprint = None
        self._lemmatize_normalized.cache_clear()

    def load_release(self, *, refresh: bool = False) -> MorphologyRelease | None:
        """Load cached runtime metadata without hashing or scanning large databases."""

        if refresh:
            previous_fingerprint = (
                self._release.release_fingerprint if self._release is not None else None
            )
            self._release = None
            try:
                refreshed = self.load_release()
            except Exception:
                self._clear_cached_release()
                raise
            if refreshed is None:
                self._clear_cached_release()
                return None
            if previous_fingerprint != refreshed.release_fingerprint:
                self._clear_cached_release()
                self._release = refreshed
            return refreshed
        if self._release is not None and not refresh:
            return self._release
        root = self.release_dir.resolve()
        manifest_path = root / "release-manifest.json"
        if not manifest_path.is_file():
            return self._missing(f"Morphology release manifest is absent: {manifest_path}")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise MorphologyValidationError(f"Cannot read morphology manifest: {error}") from error
        if manifest.get("schema_version") != LEXICON_RELEASE_SCHEMA:
            raise MorphologyValidationError(
                f"Unsupported morphology release schema: {manifest.get('schema_version')}"
            )
        if manifest.get("sqlite_schema_version") != LEXICON_SCHEMA_VERSION:
            raise MorphologyValidationError(
                f"Unsupported SQLite lexicon schema: {manifest.get('sqlite_schema_version')}"
            )
        if manifest.get("normalization_version") != NORMALIZATION_VERSION:
            raise MorphologyValidationError(
                f"Unsupported morphology normalization: {manifest.get('normalization_version')}"
            )
        release_id = manifest.get("release_id")
        languages = manifest.get("languages")
        if not isinstance(release_id, str) or not release_id:
            raise MorphologyValidationError("Morphology manifest has no release_id")
        if not isinstance(languages, dict) or set(languages) != SUPPORTED_LANGUAGES:
            raise MorphologyValidationError("Morphology manifest must contain exactly sl and it")

        fingerprint_item = manifest.get("release_fingerprint")
        if not isinstance(fingerprint_item, dict):
            raise MorphologyValidationError("Morphology manifest has no release fingerprint")
        fingerprint = fingerprint_item.get("sha256")
        recorded_inputs = fingerprint_item.get("inputs")
        expected_inputs = release_fingerprint_inputs(manifest)
        if recorded_inputs != expected_inputs:
            raise MorphologyValidationError("Morphology release fingerprint inputs mismatch")
        calculated_fingerprint = calculate_release_fingerprint(manifest)
        if fingerprint != calculated_fingerprint:
            raise MorphologyValidationError(
                "Morphology release fingerprint mismatch: "
                f"expected {fingerprint}, calculated {calculated_fingerprint}"
            )
        expected_release_id = release_id_from_fingerprint(calculated_fingerprint)
        if release_id != expected_release_id:
            raise MorphologyValidationError(
                f"Morphology release_id {release_id!r} does not match fingerprint-derived "
                f"identity {expected_release_id!r}"
            )
        if self.require_directory_identity and root.name != release_id:
            raise MorphologyValidationError(
                f"Immutable release directory {root.name!r} does not match "
                f"release_id {release_id!r}"
            )

        language_paths: dict[str, str] = {}
        for language in sorted(SUPPORTED_LANGUAGES):
            item = languages.get(language)
            if not isinstance(item, dict):
                raise MorphologyValidationError(f"Invalid {language} manifest entry")
            relative_path = item.get("path")
            expected_sha = item.get("sha256")
            expected_size = item.get("size_bytes")
            if (
                not isinstance(relative_path, str)
                or not isinstance(expected_sha, str)
                or not isinstance(expected_size, int)
            ):
                raise MorphologyValidationError(f"Incomplete {language} file identity")
            database_path = (root / relative_path).resolve()
            if root != database_path and root not in database_path.parents:
                raise MorphologyValidationError(f"Unsafe {language} database path")
            if not database_path.is_file():
                raise MorphologyValidationError(f"Missing {language} database: {database_path}")
            if database_path.stat().st_size != expected_size:
                raise MorphologyValidationError(f"Size mismatch for {language} database")
            self._validate_database_runtime(database_path, language, item)
            language_paths[language] = str(database_path)

        self._release = MorphologyRelease(
            release_id=release_id,
            release_fingerprint=calculated_fingerprint,
            release_fingerprint_inputs=expected_inputs,
            root=str(root),
            normalization_version=NORMALIZATION_VERSION,
            language_paths=language_paths,
            manifest=cast(dict[str, object], manifest),
        )
        return self._release

    @staticmethod
    def _database_metadata(
        connection: sqlite3.Connection,
        path: Path,
        language: str,
        manifest_item: dict[str, Any],
    ) -> dict[str, str]:
        metadata = {
            row["key"]: row["value"]
            for row in connection.execute("SELECT key, value FROM build_metadata ORDER BY key")
        }
        missing = sorted(REQUIRED_METADATA - metadata.keys())
        if missing:
            raise MorphologyValidationError(
                f"{language} database metadata missing: {', '.join(missing)}"
            )
        if metadata["schema_version"] != LEXICON_SCHEMA_VERSION:
            raise MorphologyValidationError(
                f"{language} schema mismatch: {metadata['schema_version']}"
            )
        if metadata["normalization_version"] != NORMALIZATION_VERSION:
            raise MorphologyValidationError(
                f"{language} normalization mismatch: {metadata['normalization_version']}"
            )
        if metadata["language"] != language:
            raise MorphologyValidationError(
                f"Language mismatch for {path}: {metadata['language']}"
            )
        source = manifest_item.get("source")
        if not isinstance(source, dict):
            raise MorphologyValidationError(f"Missing {language} source provenance")
        required_source = {"name", "version", "identifier", "url", "sha256", "license"}
        if any(not isinstance(source.get(key), str) or not source[key] for key in required_source):
            raise MorphologyValidationError(f"Incomplete {language} source provenance")
        if metadata["source_file_sha256"] != source.get("sha256"):
            raise MorphologyValidationError(f"{language} source checksum metadata mismatch")
        return cast(dict[str, str], metadata)

    @classmethod
    def _validate_database_runtime(
        cls, path: Path, language: str, manifest_item: dict[str, Any]
    ) -> None:
        """Perform bounded structural checks suitable for request readiness."""

        uri = f"file:{quote(str(path), safe='/:')}?mode=ro&immutable=1"
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            cls._database_metadata(connection, path, language, manifest_item)
            connection.execute("SELECT 1 FROM lexicon LIMIT 1").fetchone()
            indexes = {
                str(row[1])
                for row in connection.execute("PRAGMA index_list('lexicon')")
            }
            required_indexes = {
                "ix_lexicon_form_normalized",
                "ix_lexicon_form_folded",
                "ix_lexicon_lemma_normalized",
            }
            if not required_indexes <= indexes:
                raise MorphologyValidationError(
                    f"{language} database indexes missing: "
                    f"{', '.join(sorted(required_indexes - indexes))}"
                )
        except sqlite3.Error as error:
            raise MorphologyValidationError(
                f"Cannot load {language} lexicon {path}: {error}"
            ) from error
        finally:
            if connection is not None:
                connection.close()

    @classmethod
    def _validate_database_deep(
        cls, path: Path, language: str, manifest_item: dict[str, Any]
    ) -> None:
        """Run the full database scan reserved for validation and promotion."""

        uri = f"file:{quote(str(path), safe='/:')}?mode=ro&immutable=1"
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            metadata = cls._database_metadata(connection, path, language, manifest_item)
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise MorphologyValidationError(
                    f"{language} SQLite integrity_check failed: {integrity}"
                )
            count = connection.execute("SELECT count(*) FROM lexicon").fetchone()[0]
            if count != int(metadata["rows_inserted"]):
                raise MorphologyValidationError(f"{language} row count metadata mismatch")
            plans = {
                "form_normalized": "ix_lexicon_form_normalized",
                "form_folded": "ix_lexicon_form_folded",
                "lemma_normalized": "ix_lexicon_lemma_normalized",
            }
            for column, index in plans.items():
                detail = " | ".join(
                    str(row[3])
                    for row in connection.execute(
                        "EXPLAIN QUERY PLAN SELECT * FROM lexicon "
                        f"INDEXED BY {index} WHERE {column} = ?",
                        ("fixture",),
                    )
                )
                if index not in detail:
                    raise MorphologyValidationError(
                        f"{language} query plan does not use {index}: {detail}"
                    )
        except sqlite3.Error as error:
            raise MorphologyValidationError(
                f"Cannot validate {language} lexicon {path}: {error}"
            ) from error
        finally:
            if connection is not None:
                connection.close()

    def validate_release(self, *, refresh: bool = False) -> MorphologyRelease | None:
        """Deeply validate hashes, SQLite integrity, metadata, counts and query plans."""

        release = self.load_release(refresh=refresh)
        if release is None:
            return None
        if self._deep_validated_fingerprint == release.release_fingerprint and not refresh:
            return release
        languages = cast(dict[str, Any], release.manifest["languages"])
        for language in sorted(SUPPORTED_LANGUAGES):
            item = cast(dict[str, Any], languages[language])
            path = Path(release.language_paths[language])
            expected_sha = cast(str, item["sha256"])
            actual_sha = sha256_file(path)
            if actual_sha != expected_sha:
                raise MorphologyValidationError(
                    f"Checksum mismatch for {language}: expected {expected_sha}, got {actual_sha}"
                )
            self._validate_database_deep(path, language, item)
        self._deep_validated_fingerprint = release.release_fingerprint
        return release

    @contextmanager
    def _connection(self, language: str) -> Iterator[sqlite3.Connection]:
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError(f"Unsupported morphology language: {language}")
        release = self.load_release()
        if release is None:
            raise MorphologyUnavailableError("Optional morphology release is unavailable")
        path = release.language_paths[language]
        uri = f"file:{quote(path, safe='/:')}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def lemmatize_token(self, language: str, token: str) -> tuple[LemmaCandidate, ...]:
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError(f"Unsupported morphology language: {language}")
        normalized = normalize_text(token)
        if not normalized:
            return ()
        return self._lemmatize_normalized(language, normalized)

    @lru_cache(maxsize=8_192)
    def _lemmatize_normalized(
        self, language: str, normalized: str
    ) -> tuple[LemmaCandidate, ...]:
        with self._connection(language) as connection:
            rows = connection.execute(
                "SELECT lemma, lemma_normalized, msd, pos_family "
                "FROM lexicon WHERE form_normalized = ? "
                "ORDER BY lemma_normalized, lemma, msd, pos_family",
                (normalized,),
            ).fetchall()
            matched_by: str = "exact"
            if not rows:
                rows = connection.execute(
                    "SELECT lemma, lemma_normalized, msd, pos_family "
                    "FROM lexicon WHERE form_folded = ? "
                    "ORDER BY lemma_normalized, lemma, msd, pos_family",
                    (fold_diacritics(normalized),),
                ).fetchall()
                matched_by = "folded"

        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = grouped.setdefault(
                row["lemma_normalized"],
                {
                    "lemma": row["lemma"],
                    "msds": set(),
                    "pos_families": set(),
                },
            )
            if row["msd"]:
                item["msds"].add(row["msd"])
            if row["pos_family"]:
                item["pos_families"].add(row["pos_family"])
        return tuple(
            LemmaCandidate(
                language=cast(MorphologyLanguage, language),
                lemma=item["lemma"],
                lemma_normalized=lemma_normalized,
                msds=tuple(sorted(item["msds"])),
                pos_families=tuple(sorted(item["pos_families"])),
                matched_by=cast(Any, matched_by),
            )
            for lemma_normalized, item in list(grouped.items())[
                : self.max_token_candidates
            ]
        )

    def inflection_group(
        self,
        language: str,
        lemma: str,
        *,
        pos: str | None = None,
        limit: int = 40,
    ) -> InflectionGroup:
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError(f"Unsupported morphology language: {language}")
        if limit < 1 or limit > 10_000:
            raise ValueError("limit must be between 1 and 10000")
        if pos is not None and not re.fullmatch(r"[\w-]+", pos, re.UNICODE):
            raise ValueError("invalid POS filter")
        normalized = normalize_text(lemma)
        if not normalized:
            return InflectionGroup(cast(MorphologyLanguage, language), "", (), False)
        with self._connection(language) as connection:
            rows = connection.execute(
                "SELECT min(form) AS form, form_normalized FROM lexicon "
                "WHERE lemma_normalized = ? "
                "AND (? IS NULL OR pos_family = ? OR msd LIKE ? || '%') "
                "GROUP BY form_normalized "
                "ORDER BY CASE WHEN form_normalized = ? THEN 0 ELSE 1 END, "
                "length(form_normalized), form_normalized, form LIMIT ?",
                (normalized, pos, pos, pos, normalized, limit + 1),
            ).fetchall()
        forms = tuple(row["form"] for row in rows[:limit])
        return InflectionGroup(
            language=cast(MorphologyLanguage, language),
            lemma=normalized,
            forms=forms,
            truncated=len(rows) > limit,
        )

    @staticmethod
    def _protected_token(token: str, normalized: str) -> bool:
        return (
            len(normalized) == 1
            or any(character.isdigit() for character in token)
            or token.startswith(("http://", "https://"))
            or "@" in token
            or (len(token) >= 2 and token.isupper())
        )

    @classmethod
    def is_protected_token(cls, token: str) -> bool:
        """Return whether a token must bypass lexicon expansion."""

        return cls._protected_token(token, normalize_text(token))

    def phrase_signatures(self, language: str, phrase: str) -> tuple[MorphSignature, ...]:
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError(f"Unsupported morphology language: {language}")
        raw_tokens = tokenize(phrase)
        if not raw_tokens:
            return ()
        normalized_tokens = tuple(normalize_text(token) for token in raw_tokens)
        token_details = tuple(
            MorphTokenDetail(
                token=token,
                token_normalized=normalized,
                selected=normalized,
                lemma_candidates=(),
                matched_by=None,
                protected=self._protected_token(token, normalized),
            )
            for token, normalized in zip(raw_tokens, normalized_tokens, strict=True)
        )
        tokenized = " ".join(normalized_tokens)
        signatures = [
            MorphSignature(
                language=cast(MorphologyLanguage, language),
                signature_normalized=tokenized,
                signature_folded=fold_diacritics(tokenized),
                source_span=phrase,
                kind="tokenized",
                changed_tokens=0,
                ambiguity_cost=0,
                token_details=token_details,
            )
        ]

        choices: list[list[tuple[str, LemmaCandidate | None, bool]]] = []
        for token, normalized in zip(raw_tokens, normalized_tokens, strict=True):
            if self._protected_token(token, normalized):
                choices.append([(normalized, None, False)])
                continue
            candidates = self.lemmatize_token(language, token)
            if not candidates:
                choices.append([(normalized, None, False)])
                continue
            choices.append(
                [
                    (
                        candidate.lemma_normalized,
                        candidate,
                        candidate.lemma_normalized != normalized,
                    )
                    for candidate in candidates
                ]
            )

        seen = {tokenized}
        for combination in itertools.product(*choices):
            selected = tuple(item[0] for item in combination)
            changed_tokens = sum(int(item[2]) for item in combination)
            if changed_tokens == 0:
                continue
            signature = " ".join(selected)
            if signature in seen:
                continue
            seen.add(signature)
            ambiguity_cost = sum(
                max(0, len(options) - 1) for options in choices
            )
            details = tuple(
                MorphTokenDetail(
                    token=token,
                    token_normalized=normalized,
                    selected=selected_value,
                    lemma_candidates=tuple(option[0] for option in options),
                    matched_by=candidate.matched_by if candidate else None,
                    protected=self._protected_token(token, normalized),
                )
                for token, normalized, selected_value, candidate, options in (
                    (
                        raw_tokens[index],
                        normalized_tokens[index],
                        combination[index][0],
                        combination[index][1],
                        choices[index],
                    )
                    for index in range(len(raw_tokens))
                )
            )
            signatures.append(
                MorphSignature(
                    language=cast(MorphologyLanguage, language),
                    signature_normalized=signature,
                    signature_folded=fold_diacritics(signature),
                    source_span=phrase,
                    kind="lemma",
                    changed_tokens=changed_tokens,
                    ambiguity_cost=ambiguity_cost,
                    token_details=details,
                )
            )
            if len(signatures) >= self.max_signatures_per_phrase:
                break
        return tuple(signatures)

    def spans(self, query: str) -> tuple[str, ...]:
        return query_spans(
            query,
            max_spans=self.max_query_spans,
            max_words=self.max_words_per_span,
        )

    def health(self) -> dict[str, object]:
        try:
            release = self.load_release()
        except MorphologyError as error:
            if self.required:
                raise
            return {"available": False, "required": False, "error": str(error)}
        if release is None:
            return {
                "available": False,
                "required": self.required,
                "error": "release_missing",
            }
        return {
            "available": True,
            "required": self.required,
            "release_id": release.release_id,
            "release_fingerprint": release.release_fingerprint,
            "languages": sorted(release.language_paths),
        }

    def close(self) -> None:
        """Compatibility hook; short-lived connections leave nothing cached."""

        self._lemmatize_normalized.cache_clear()


@lru_cache(maxsize=8)
def cached_morphology_service(
    release_dir: str,
    required: bool,
    max_token_candidates: int,
    max_signatures_per_phrase: int,
    max_query_spans: int,
    max_words_per_span: int,
    require_directory_identity: bool = True,
) -> MorphologyService:
    return MorphologyService(
        release_dir,
        required=required,
        max_token_candidates=max_token_candidates,
        max_signatures_per_phrase=max_signatures_per_phrase,
        max_query_spans=max_query_spans,
        max_words_per_span=max_words_per_span,
        require_directory_identity=require_directory_identity,
    )
