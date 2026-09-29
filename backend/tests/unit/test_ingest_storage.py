"""Content-addressed storage.

The properties under test are the ones that make a retried ingest safe and make
stored bytes verifiable against the identifier used to fetch them.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ap_agent.errors import ParsingError
from ap_agent.ingest.storage import (
    FilesystemDocumentStore,
    InMemoryDocumentStore,
    extension_for,
)

CONTENT = b"%PDF-1.4 invoice body"


class TestExtensionMapping:
    def test_known_types(self) -> None:
        assert extension_for("application/pdf") == ".pdf"
        assert extension_for("image/png") == ".png"
        assert extension_for("image/jpeg") == ".jpg"

    def test_unknown_type_falls_back_to_bin(self) -> None:
        assert extension_for("application/x-mystery") == ".bin"


class TestFilesystemStore:
    def test_put_returns_a_content_addressed_reference(self, tmp_path: Path) -> None:
        store = FilesystemDocumentStore(tmp_path)
        ref = store.put(CONTENT, media_type="application/pdf")
        assert ref == f"{hashlib.sha256(CONTENT).hexdigest()}.pdf"

    def test_round_trip(self, tmp_path: Path) -> None:
        store = FilesystemDocumentStore(tmp_path)
        ref = store.put(CONTENT, media_type="application/pdf")
        assert store.get(ref) == CONTENT
        assert store.exists(ref) is True

    def test_put_is_idempotent(self, tmp_path: Path) -> None:
        # This is what makes a retried ingest safe: identical bytes cannot
        # produce a second stored copy.
        store = FilesystemDocumentStore(tmp_path)
        first = store.put(CONTENT, media_type="application/pdf")
        second = store.put(CONTENT, media_type="application/pdf")

        assert first == second
        files = [p for p in tmp_path.rglob("*") if p.is_file()]
        assert len(files) == 1

    def test_paths_are_sharded_two_levels_deep(self, tmp_path: Path) -> None:
        # Keeps any single directory small at high volume.
        store = FilesystemDocumentStore(tmp_path)
        ref = store.put(CONTENT, media_type="application/pdf")
        digest = ref.partition(".")[0]

        path = store.path_for(ref)
        assert path is not None
        assert path.parent.name == digest[2:4]
        assert path.parent.parent.name == digest[:2]

    def test_no_partial_files_are_left_behind(self, tmp_path: Path) -> None:
        # Bytes are written to a temp file then renamed, so an interrupted write
        # never leaves a truncated file at a path whose name asserts a hash.
        store = FilesystemDocumentStore(tmp_path)
        store.put(CONTENT, media_type="application/pdf")
        assert list(tmp_path.rglob("*.partial")) == []

    def test_verify_confirms_stored_bytes_match_the_reference(self, tmp_path: Path) -> None:
        store = FilesystemDocumentStore(tmp_path)
        ref = store.put(CONTENT, media_type="application/pdf")
        assert store.verify(ref) is True

    def test_verify_detects_corruption(self, tmp_path: Path) -> None:
        store = FilesystemDocumentStore(tmp_path)
        ref = store.put(CONTENT, media_type="application/pdf")
        path = store.path_for(ref)
        assert path is not None
        path.write_bytes(b"tampered")

        assert store.verify(ref) is False

    def test_verify_returns_false_for_a_missing_object(self, tmp_path: Path) -> None:
        store = FilesystemDocumentStore(tmp_path)
        absent = f"{'0' * 64}.pdf"
        assert store.verify(absent) is False

    def test_get_missing_object_fails_loudly(self, tmp_path: Path) -> None:
        store = FilesystemDocumentStore(tmp_path)
        with pytest.raises(ParsingError, match="not present in the store"):
            store.get(f"{'a' * 64}.pdf")

    def test_malformed_reference_is_rejected(self, tmp_path: Path) -> None:
        # A short digest means the caller invented a reference; that is a bug, not
        # a missing file.
        store = FilesystemDocumentStore(tmp_path)
        with pytest.raises(ParsingError, match="Malformed storage reference"):
            store.path_for("nope.pdf")

    def test_distinct_content_produces_distinct_objects(self, tmp_path: Path) -> None:
        store = FilesystemDocumentStore(tmp_path)
        a = store.put(b"one", media_type="application/pdf")
        b = store.put(b"two", media_type="application/pdf")
        assert a != b
        assert store.get(a) == b"one"
        assert store.get(b) == b"two"


class TestInMemoryStore:
    def test_satisfies_the_same_contract(self) -> None:
        store = InMemoryDocumentStore()
        ref = store.put(CONTENT, media_type="application/pdf")
        assert store.get(ref) == CONTENT
        assert store.exists(ref) is True
        assert store.put(CONTENT, media_type="application/pdf") == ref

    def test_reports_no_local_path(self) -> None:
        # Keeps the "no local path available" branch honest for remote stores.
        store = InMemoryDocumentStore()
        ref = store.put(CONTENT, media_type="application/pdf")
        assert store.path_for(ref) is None

    def test_missing_object_fails_loudly(self) -> None:
        with pytest.raises(ParsingError, match="not present in the store"):
            InMemoryDocumentStore().get("missing.pdf")
