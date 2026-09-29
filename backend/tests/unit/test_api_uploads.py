"""`POST /runs/upload` — the real (non-scripted) "run my own document" path.

Fast/offline coverage here is the validation boundary: `DocumentPayload`'s own
rules (size, media type, non-empty) reject a bad upload *before* anything
touches Postgres or Bedrock, so those cases are ordinary unit tests. The full
accept -> parse -> live-extract -> stream path needs a reachable Postgres (the
ingress gateway persists a `Document` row) and, past that, a real Bedrock call
— those are `integration`/`bedrock`-marked and live in
`tests/integration/test_upload_live.py`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ap_agent.api.main import app

_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "datasets"
    / "generated"
    / "invoices"
    / "clean_touchless-000.pdf"
)


class TestUploadValidation:
    """Rejections that never need a database or a model call."""

    def test_empty_file_is_rejected(self) -> None:
        client = TestClient(app)
        r = client.post(
            "/runs/upload",
            files={"file": ("empty.pdf", b"", "application/pdf")},
        )
        assert r.status_code == 422
        assert "empty" in r.json()["error"].lower()

    def test_oversized_file_is_rejected(self) -> None:
        client = TestClient(app)
        # One byte over the 25 MiB limit `DocumentPayload` enforces.
        oversized = b"0" * (25 * 1024 * 1024 + 1)
        r = client.post(
            "/runs/upload",
            files={"file": ("big.pdf", oversized, "application/pdf")},
        )
        assert r.status_code == 422
        assert "byte limit" in r.json()["error"]

    def test_unsupported_media_type_is_rejected(self) -> None:
        client = TestClient(app)
        r = client.post(
            "/runs/upload",
            files={"file": ("invoice.docx", b"not a real docx", "application/msword")},
        )
        assert r.status_code == 422
        assert "unsupported media type" in r.json()["error"].lower()

    def test_missing_content_type_is_treated_as_unsupported(self) -> None:
        client = TestClient(app)
        # Some browsers/clients omit Content-Type; the endpoint must still
        # reject cleanly rather than crash on a None media type.
        r = client.post(
            "/runs/upload",
            files={"file": ("invoice.xyz", b"some bytes", "")},
        )
        assert r.status_code == 422


class TestUploadDocumentImage:
    """The upload document-image endpoint for a run the server never saw."""

    def test_unknown_run_is_404(self) -> None:
        client = TestClient(app)
        r = client.get("/runs/not-a-real-run-id/document/image")
        assert r.status_code == 404
        assert "no uploaded document" in r.json()["error"].lower()


class TestMediaTypeForReference:
    """`_media_type_for_reference` inverts `extension_for` exactly."""

    def test_recovers_pdf(self) -> None:
        from ap_agent.api.uploads import _media_type_for_reference

        assert _media_type_for_reference("abc123.pdf") == "application/pdf"

    def test_recovers_image_types(self) -> None:
        from ap_agent.api.uploads import _media_type_for_reference

        assert _media_type_for_reference("abc123.png") == "image/png"
        assert _media_type_for_reference("abc123.jpg") == "image/jpeg"
        assert _media_type_for_reference("abc123.tiff") == "image/tiff"

    def test_unknown_extension_falls_back_to_pdf(self) -> None:
        from ap_agent.api.uploads import _media_type_for_reference

        assert _media_type_for_reference("abc123.bin") == "application/pdf"
