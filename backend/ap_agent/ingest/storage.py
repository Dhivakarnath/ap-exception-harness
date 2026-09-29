"""Content-addressed document storage.

Documents are stored under their SHA-256, sharded two levels deep. Three
consequences, all wanted:

* **Writes are naturally idempotent.** Re-storing identical bytes is a no-op, so
  a retried ingest cannot produce a second copy.
* **The path is a proof of content.** A stored file can be verified against the
  identifier used to fetch it, which matters when the bytes are the evidence
  behind a payment authorisation.
* **Directory fan-out stays bounded.** Sharding avoids a single directory with
  hundreds of thousands of entries.

Local filesystem here; the `DocumentStore` protocol is what an S3 implementation
would satisfy in v2 without touching callers.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import Protocol

from ap_agent.errors import ErrorContext, ParsingError

_EXTENSION_BY_MEDIA_TYPE: dict[str, str] = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/tiff": ".tiff",
}


def extension_for(media_type: str) -> str:
    return _EXTENSION_BY_MEDIA_TYPE.get(media_type, ".bin")


class DocumentStore(Protocol):
    """Where raw documents live.

    Deliberately minimal. An S3-backed implementation in v2 satisfies this
    without callers changing.
    """

    def put(self, content: bytes, *, media_type: str) -> str:
        """Store bytes, returning a storage reference. Idempotent."""
        ...

    def get(self, reference: str) -> bytes:
        """Retrieve bytes by reference."""
        ...

    def exists(self, reference: str) -> bool: ...

    def path_for(self, reference: str) -> Path | None:
        """Local path, when one exists.

        Docling reads from a path, so a store that can offer one avoids a
        round-trip through a temporary file. Returning None is legitimate for
        remote stores.
        """
        ...


class FilesystemDocumentStore:
    """Content-addressed store on the local filesystem."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def _resolve(self, digest: str, extension: str) -> Path:
        # Two-level shard keeps any single directory small.
        return self._root / digest[:2] / digest[2:4] / f"{digest}{extension}"

    def put(self, content: bytes, *, media_type: str) -> str:
        digest = hashlib.sha256(content).hexdigest()
        extension = extension_for(media_type)
        target = self._resolve(digest, extension)

        if target.exists():
            # Same content hash and same bytes: nothing to do. This is what makes
            # a retried ingest safe.
            return f"{digest}{extension}"

        target.parent.mkdir(parents=True, exist_ok=True)

        # Write to a temporary file in the same directory, then rename. An
        # interrupted write must never leave a truncated file at a path whose
        # name asserts a content hash.
        with tempfile.NamedTemporaryFile(
            dir=target.parent, delete=False, suffix=".partial"
        ) as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        temp_path.replace(target)

        return f"{digest}{extension}"

    def get(self, reference: str) -> bytes:
        path = self.path_for(reference)
        if path is None or not path.is_file():
            raise ParsingError(
                f"Document {reference!r} is not present in the store.",
                context=ErrorContext(stage="storage.get", inputs={"reference": reference}),
            )
        return path.read_bytes()

    def exists(self, reference: str) -> bool:
        path = self.path_for(reference)
        return path is not None and path.is_file()

    def path_for(self, reference: str) -> Path | None:
        digest, _, extension = reference.partition(".")
        if len(digest) != 64:
            raise ParsingError(
                f"Malformed storage reference {reference!r}: expected a 64-character "
                "SHA-256 digest followed by an extension.",
                context=ErrorContext(stage="storage.path_for", inputs={"reference": reference}),
            )
        return self._resolve(digest, f".{extension}" if extension else "")

    def verify(self, reference: str) -> bool:
        """Re-hash stored bytes and compare against the reference.

        The reference *claims* a content hash; this checks it. Cheap insurance
        against silent corruption of the evidence behind a payment decision.
        """
        digest = reference.partition(".")[0]
        try:
            content = self.get(reference)
        except ParsingError:
            return False
        return hashlib.sha256(content).hexdigest() == digest


class InMemoryDocumentStore:
    """In-memory store for tests.

    Satisfies the same protocol, so tests exercise real calling code. `path_for`
    returns None, which also keeps the "no local path available" branch honest.
    """

    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    def put(self, content: bytes, *, media_type: str) -> str:
        digest = hashlib.sha256(content).hexdigest()
        reference = f"{digest}{extension_for(media_type)}"
        self._blobs.setdefault(reference, content)
        return reference

    def get(self, reference: str) -> bytes:
        try:
            return self._blobs[reference]
        except KeyError as exc:
            raise ParsingError(
                f"Document {reference!r} is not present in the store.",
                context=ErrorContext(stage="storage.get", inputs={"reference": reference}),
            ) from exc

    def exists(self, reference: str) -> bool:
        return reference in self._blobs

    def path_for(self, reference: str) -> Path | None:
        return None
