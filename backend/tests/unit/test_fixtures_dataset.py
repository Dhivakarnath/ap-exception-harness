"""Adversarial dataset generator.

The dataset is the yardstick every later accuracy claim is measured against, so
these tests protect three things:

* **Determinism.** If generation is not reproducible, an eval regression cannot
  be told apart from dataset churn.
* **Ground-truth coherence.** A case whose expectation contradicts itself would
  grade the pipeline against an impossible target.
* **Agreement with the mock ERP seed.** The cases reference PO-2001, GRN-3005,
  V-1006 and so on. If the ledger seed drifts, the dataset silently starts
  testing the wrong thing — so drift is asserted, not assumed.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

# The mock ERP seed is importable via tests/conftest.py.
from app import seed as erp_seed
from pypdf import PdfReader

from apfixtures import build as build_mod
from apfixtures.cases import BUILDERS, TENANT_MANUFACTURING, TENANT_RETAIL, all_modes, build_case
from apfixtures.labels import LABEL_VARIANTS, all_known_labels, label_for
from apfixtures.manifest import build_entry, load_manifest, verify_manifest
from apfixtures.render import render_case
from apfixtures.spec import (
    DATASET_TODAY,
    SEED_POS,
    VENDOR_BANK_LAST4,
    VENDOR_NAMES,
    ExpectedRoute,
    ExpectedVerdict,
    FailureMode,
    RenderFormat,
)


def _rng(ordinal: int = 0) -> np.random.Generator:
    return np.random.default_rng(np.random.SeedSequence([12345, ordinal]))


# ------------------------------------------------------------------- coverage


class TestCoverage:
    def test_every_failure_mode_has_a_builder(self) -> None:
        assert set(BUILDERS) == set(FailureMode)

    def test_there_are_at_least_twenty_modes(self) -> None:
        # Breadth matters: a handful of modes would not represent the long tail
        # that actually breaks production pipelines.
        assert len(all_modes()) >= 20

    def test_both_tenants_are_exercised(self) -> None:
        tenants = {build_case(mode, 0).tenant_id for mode in all_modes()}
        assert tenants == {TENANT_MANUFACTURING, TENANT_RETAIL}

    def test_all_three_render_formats_are_exercised(self) -> None:
        formats = {build_case(mode, 0).render_format for mode in all_modes()}
        assert formats == set(RenderFormat)

    def test_case_ids_are_unique_across_a_run(self) -> None:
        ids = [build_case(mode, i).case_id for mode in all_modes() for i in range(5)]
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------- determinism


class TestDeterminism:
    @pytest.mark.parametrize("mode", list(FailureMode))
    def test_case_construction_is_pure(self, mode: FailureMode) -> None:
        assert build_case(mode, 7) == build_case(mode, 7)

    def test_render_is_byte_identical_for_the_same_seed(self) -> None:
        case = build_case(FailureMode.CLEAN_TOUCHLESS, 3)
        a = render_case(case, variant=3, adversarial_labels=False, rng=_rng(3))
        b = render_case(case, variant=3, adversarial_labels=False, rng=_rng(3))
        assert hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest()

    def test_degraded_render_is_byte_identical_for_the_same_seed(self) -> None:
        # The noisy path is the one at risk: unseeded randomness would show up here.
        case = build_case(FailureMode.OCR_NOISE, 4)
        a = render_case(case, variant=4, adversarial_labels=False, rng=_rng(4))
        b = render_case(case, variant=4, adversarial_labels=False, rng=_rng(4))
        assert a == b

    def test_different_seeds_produce_different_noise(self) -> None:
        case = build_case(FailureMode.IMAGE_ONLY, 1)
        a = render_case(case, variant=1, adversarial_labels=False, rng=_rng(1))
        b = render_case(case, variant=1, adversarial_labels=False, rng=_rng(999))
        assert a != b, "noise must actually depend on the seed"

    def test_case_rng_is_independent_of_run_length(self) -> None:
        # Generating 3 cases must produce the same bytes as the first 3 of a
        # 50-case run, so a quick run is a true prefix of a full one.
        assert (
            build_mod._case_rng(11, 5).bytes(16) == build_mod._case_rng(11, 5).bytes(16)
        )
        assert build_mod._case_rng(11, 5).bytes(16) != build_mod._case_rng(11, 6).bytes(16)


# ------------------------------------------------------- expectation coherence


class TestExpectationCoherence:
    @pytest.mark.parametrize("mode", list(FailureMode))
    def test_auto_approve_never_coexists_with_a_failing_check(
        self, mode: FailureMode
    ) -> None:
        case = build_case(mode, 0)
        exp = case.expectation
        if exp.route is ExpectedRoute.AUTO_APPROVE:
            failing = [c.name for c in exp.checks if c.verdict is ExpectedVerdict.FAIL]
            assert not failing, f"{mode.value} auto-approves while {failing} fail"
            assert exp.requires_human_review is False

    @pytest.mark.parametrize("mode", list(FailureMode))
    def test_every_case_states_a_rationale(self, mode: FailureMode) -> None:
        assert build_case(mode, 0).expectation.rationale.strip()

    def test_only_clean_and_tolerated_modes_auto_approve(self) -> None:
        # Anything else auto-approving would mean a control is not being applied.
        auto = {
            mode.value
            for mode in all_modes()
            if build_case(mode, 0).expectation.route is ExpectedRoute.AUTO_APPROVE
        }
        assert auto == {
            FailureMode.CLEAN_TOUCHLESS.value,
            FailureMode.PRICE_VARIANCE_WITHIN_TOLERANCE.value,
            FailureMode.PARTIAL_DELIVERY.value,
            FailureMode.LABEL_VARIATION.value,
            # A logo crowding the header is a rendering challenge, not a control
            # failure: the underlying invoice is clean and in-tolerance, so it
            # must still auto-approve if extraction reads it correctly.
            FailureMode.LOGO_OVERLAP.value,
        }

    def test_rejections_are_reserved_for_unresolvable_cases(self) -> None:
        rejects = {
            mode.value
            for mode in all_modes()
            if build_case(mode, 0).expectation.route is ExpectedRoute.REJECT
        }
        # A duplicate of a paid bill and a blocked vendor have nothing to resolve;
        # everything else should be held or routed, not rejected.
        assert rejects == {
            FailureMode.EXACT_DUPLICATE.value,
            FailureMode.VENDOR_BLOCKED.value,
        }

    def test_non_po_modes_skip_three_way_match(self) -> None:
        # SKIP, not PASS: a reviewer must be able to tell "did not apply" from
        # "was satisfied". Includes the credit note — a reversal has no forward
        # PO quantities to match even though it cites a PO.
        for mode in (
            FailureMode.MISSING_PO_REFERENCE,
            FailureMode.NON_PO_SERVICES,
            FailureMode.VENDOR_BLOCKED,
            FailureMode.VENDOR_INACTIVE,
            FailureMode.CREDIT_NOTE,
        ):
            checks = {c.name: c.verdict for c in build_case(mode, 0).expectation.checks}
            assert checks["three_way_match"] is ExpectedVerdict.SKIP, mode.value

    def test_bank_detail_change_always_forces_review(self) -> None:
        # Unconditional, regardless of amount (FR-4.6).
        for i in range(10):
            case = build_case(FailureMode.BANK_DETAIL_CHANGE, i)
            assert case.expectation.requires_human_review is True
            assert case.expectation.route is not ExpectedRoute.AUTO_APPROVE

    def test_non_po_cases_declare_an_expected_gl_account(self) -> None:
        for i in range(10):
            case = build_case(FailureMode.NON_PO_SERVICES, i)
            assert case.expectation.expected_gl_account is not None


# ------------------------------------------------------ content correctness


class TestGeneratedContent:
    def test_clean_cases_reconcile_arithmetically(self) -> None:
        for i in range(20):
            c = build_case(FailureMode.CLEAN_TOUCHLESS, i).content
            assert c.computed_line_sum == c.subtotal
            assert c.subtotal + (c.tax_amount or Decimal(0)) == c.total_amount

    def test_clean_cases_stay_under_the_manufacturing_ceiling(self) -> None:
        # Otherwise they would route for approval and the case would be mislabelled.
        for i in range(50):
            c = build_case(FailureMode.CLEAN_TOUCHLESS, i).content
            assert c.total_amount <= Decimal("2500.00"), f"case {i} total {c.total_amount}"

    def test_over_threshold_cases_actually_exceed_the_ceiling(self) -> None:
        for i in range(50):
            c = build_case(FailureMode.CLEAN_OVER_THRESHOLD, i).content
            assert c.total_amount > Decimal("2500.00")

    def test_line_sum_mismatch_really_does_not_reconcile(self) -> None:
        for i in range(20):
            c = build_case(FailureMode.LINE_SUM_MISMATCH, i).content
            assert c.computed_line_sum != c.subtotal

    def test_tax_total_mismatch_really_does_not_reconcile(self) -> None:
        for i in range(20):
            c = build_case(FailureMode.TAX_TOTAL_MISMATCH, i).content
            assert c.subtotal + (c.tax_amount or Decimal(0)) != c.total_amount

    def test_price_variance_within_tolerance_is_actually_within(self) -> None:
        po = SEED_POS["PO-2007"]
        for i in range(50):
            c = build_case(FailureMode.PRICE_VARIANCE_WITHIN_TOLERANCE, i).content
            unit = c.lines[0].unit_price
            variance = (unit - po.unit_price) / po.unit_price * 100
            assert Decimal(0) < variance < Decimal("2.0"), f"case {i} variance {variance}%"

    def test_price_variance_exceeding_tolerance_is_actually_outside(self) -> None:
        po = SEED_POS["PO-2008"]
        for i in range(50):
            c = build_case(FailureMode.PRICE_VARIANCE_EXCEEDS_TOLERANCE, i).content
            unit = c.lines[0].unit_price
            variance = (unit - po.unit_price) / po.unit_price * 100
            assert variance > Decimal("2.0"), f"case {i} variance {variance}%"

    def test_overbilled_quantity_exceeds_the_receipt(self) -> None:
        received = SEED_POS["PO-2005"].received_qty
        assert received is not None
        for i in range(50):
            c = build_case(FailureMode.QUANTITY_OVERBILLED, i).content
            assert c.lines[0].quantity > received

    def test_partial_delivery_bills_within_the_receipt(self) -> None:
        received = SEED_POS["PO-2003"].received_qty
        assert received is not None
        for i in range(50):
            c = build_case(FailureMode.PARTIAL_DELIVERY, i).content
            assert c.lines[0].quantity <= received

    def test_missing_po_cases_print_no_po_reference(self) -> None:
        for i in range(10):
            assert build_case(FailureMode.MISSING_PO_REFERENCE, i).content.po_reference is None

    def test_malformed_po_reference_is_present_but_unknown(self) -> None:
        for i in range(10):
            ref = build_case(FailureMode.MALFORMED_PO_REFERENCE, i).content.po_reference
            assert ref is not None
            assert ref not in SEED_POS

    def test_bank_detail_change_differs_from_the_master(self) -> None:
        for i in range(50):
            c = build_case(FailureMode.BANK_DETAIL_CHANGE, i).content
            assert c.remit_to_last4 != VENDOR_BANK_LAST4[c.vendor_id]

    def test_clean_cases_match_the_master_bank_details(self) -> None:
        for i in range(20):
            c = build_case(FailureMode.CLEAN_TOUCHLESS, i).content
            assert c.remit_to_last4 == VENDOR_BANK_LAST4[c.vendor_id]

    def test_currency_mismatch_prints_two_currencies(self) -> None:
        for i in range(10):
            c = build_case(FailureMode.CURRENCY_MISMATCH, i).content
            assert c.line_currency_override is not None
            assert c.line_currency_override != c.currency

    def test_exact_duplicate_reproduces_the_historical_bill(self) -> None:
        c = build_case(FailureMode.EXACT_DUPLICATE, 0).content
        historical = next(b for b in erp_seed.BILLS if b.DocNumber == "INV-77001")
        assert c.invoice_number == historical.DocNumber
        assert c.total_amount == historical.TotalAmt
        assert c.vendor_id == historical.VendorRef.value

    def test_fuzzy_duplicate_is_a_near_miss_not_an_exact_match(self) -> None:
        historical = next(b for b in erp_seed.BILLS if b.DocNumber == "INV-88001")
        for i in range(50):
            c = build_case(FailureMode.FUZZY_DUPLICATE, i).content
            assert c.total_amount == historical.TotalAmt
            assert c.invoice_number.startswith("INV-88001")
            # Index 4 uses a whitespace-only suffix which strips to the exact
            # number — a legitimate near-miss shape, so exact equality is allowed
            # only there.
            if i % 5 != 4:
                assert c.invoice_number != historical.DocNumber

    def test_threshold_avoidance_lands_just_below_the_boundary(self) -> None:
        boundary = Decimal("5000.00")
        for i in range(50):
            c = build_case(FailureMode.THRESHOLD_AVOIDANCE, i).content
            assert c.total_amount < boundary
            assert c.total_amount > boundary * Decimal("0.97"), (
                f"case {i} total {c.total_amount} is not close enough to the boundary "
                "to represent avoidance"
            )

    def test_invoice_dates_are_not_in_the_future(self) -> None:
        for mode in all_modes():
            for i in range(5):
                c = build_case(mode, i).content
                assert c.invoice_date <= DATASET_TODAY, f"{mode.value}-{i}"


# --------------------------------------------------------------- render output


class TestRenderOutput:
    def test_digital_pdf_has_an_extractable_text_layer(self, tmp_path: Path) -> None:
        case = build_case(FailureMode.CLEAN_TOUCHLESS, 0)
        payload = render_case(case, variant=0, adversarial_labels=False, rng=_rng())
        path = tmp_path / case.filename
        path.write_bytes(payload)

        text = PdfReader(path).pages[0].extract_text()
        assert case.content.invoice_number in text
        assert case.content.po_reference is not None
        assert case.content.po_reference in text
        # Rendered with thousands separators, as a real invoice would be.
        assert f"{case.content.total_amount:,.2f}" in text

    def test_scanned_pdf_has_no_text_layer(self, tmp_path: Path) -> None:
        # The point of the OCR mode: Docling cannot read a text layer that is
        # not there, so the OCR path must engage.
        case = build_case(FailureMode.OCR_NOISE, 0)
        payload = render_case(case, variant=0, adversarial_labels=False, rng=_rng())
        path = tmp_path / case.filename
        path.write_bytes(payload)

        assert PdfReader(path).pages[0].extract_text().strip() == ""

    def test_image_only_case_renders_a_png(self) -> None:
        case = build_case(FailureMode.IMAGE_ONLY, 0)
        payload = render_case(case, variant=0, adversarial_labels=False, rng=_rng())
        assert case.filename.endswith(".png")
        assert payload[:8] == b"\x89PNG\r\n\x1a\n"

    def test_noise_measurably_degrades_the_image(self) -> None:
        import io

        from PIL import Image

        clean = build_case(FailureMode.IMAGE_ONLY, 0).model_copy(
            update={"noise_level": 0.0, "rotation_degrees": 0.0}
        )
        noisy = clean.model_copy(update={"noise_level": 0.8})

        def render_array(case) -> np.ndarray:  # type: ignore[no-untyped-def]
            payload = render_case(case, variant=0, adversarial_labels=False, rng=_rng())
            return np.asarray(Image.open(io.BytesIO(payload)), dtype=np.float32)

        a, b = render_array(clean), render_array(noisy)

        # Measure a blank margin strip rather than the whole page. Global std is
        # the wrong metric: heavy degradation also blurs and downsamples, which
        # *lowers* overall variance by softening sharp text edges. The background
        # isolates the added grain from the content.
        bottom_strip = slice(int(a.shape[0] * 0.88), a.shape[0])
        clean_bg, noisy_bg = a[bottom_strip, :], b[bottom_strip, :]

        assert clean_bg.std() < 1.0, "clean render should have a near-uniform background"
        assert noisy_bg.std() > clean_bg.std() + 5.0, "noise_level had no measurable effect"
        assert noisy_bg.mean() < clean_bg.mean(), "degradation should darken the page"

    def test_adversarial_labels_change_the_rendered_text(self, tmp_path: Path) -> None:
        case = build_case(FailureMode.LABEL_VARIATION, 0)
        plain = render_case(case, variant=0, adversarial_labels=False, rng=_rng())
        odd = render_case(case, variant=0, adversarial_labels=True, rng=_rng())

        (tmp_path / "a.pdf").write_bytes(plain)
        (tmp_path / "b.pdf").write_bytes(odd)
        text_a = PdfReader(tmp_path / "a.pdf").pages[0].extract_text()
        text_b = PdfReader(tmp_path / "b.pdf").pages[0].extract_text()

        assert text_a != text_b
        # Values survive the relabelling; only the labels differ.
        assert case.content.invoice_number in text_a
        assert case.content.invoice_number in text_b


# ------------------------------------------------------------------- manifest


class TestManifest:
    def test_entry_records_expected_extraction_exactly(self) -> None:
        case = build_case(FailureMode.CLEAN_TOUCHLESS, 0)
        payload = render_case(case, variant=0, adversarial_labels=False, rng=_rng())
        entry = build_entry(case, payload)

        exp = entry.expected_extraction
        assert exp.invoice_number == case.content.invoice_number
        assert exp.total_amount == format(case.content.total_amount, "f")
        assert exp.expected_vendor_id == case.content.vendor_id
        assert entry.content_sha256 == hashlib.sha256(payload).hexdigest()
        assert entry.size_bytes == len(payload)

    def test_amounts_are_strings_not_floats(self) -> None:
        # A JSON float would reintroduce the rounding error Money exists to avoid.
        case = build_case(FailureMode.CLEAN_TOUCHLESS, 0)
        payload = render_case(case, variant=0, adversarial_labels=False, rng=_rng())
        dumped = build_entry(case, payload).model_dump(mode="json")

        for key in ("subtotal", "total_amount", "tax_amount"):
            value = dumped["expected_extraction"][key]
            assert value is None or isinstance(value, str), f"{key} must be a string"

    def test_generate_and_verify_round_trip(self, tmp_path: Path) -> None:
        build_mod.generate(output_dir=tmp_path, per_mode=1, quiet=True)
        assert build_mod.verify(tmp_path, quiet=True) is True

    def test_verify_detects_a_tampered_file(self, tmp_path: Path) -> None:
        build_mod.generate(output_dir=tmp_path, per_mode=1, quiet=True)
        target = next((tmp_path / build_mod.FILES_SUBDIR).glob("clean_touchless-*.pdf"))
        target.write_bytes(target.read_bytes() + b"% tampered")

        drift = verify_manifest(
            load_manifest(tmp_path / build_mod.MANIFEST_NAME),
            tmp_path / build_mod.FILES_SUBDIR,
        )
        assert drift.is_clean is False
        assert target.name in drift.hash_mismatches

    def test_verify_detects_a_missing_file(self, tmp_path: Path) -> None:
        build_mod.generate(output_dir=tmp_path, per_mode=1, quiet=True)
        target = next((tmp_path / build_mod.FILES_SUBDIR).glob("*.pdf"))
        name = target.name
        target.unlink()

        drift = verify_manifest(
            load_manifest(tmp_path / build_mod.MANIFEST_NAME),
            tmp_path / build_mod.FILES_SUBDIR,
        )
        assert name in drift.missing_files

    def test_missing_manifest_raises_an_actionable_error(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="make dataset"):
            load_manifest(tmp_path / "manifest.json")

    def test_mode_counts_cover_every_mode(self, tmp_path: Path) -> None:
        build_mod.generate(output_dir=tmp_path, per_mode=2, quiet=True)
        manifest = load_manifest(tmp_path / build_mod.MANIFEST_NAME)
        assert set(manifest.mode_counts) == {m.value for m in FailureMode}
        assert all(count == 2 for count in manifest.mode_counts.values())
        assert manifest.total_cases == 2 * len(FailureMode)


# ----------------------------------------------------------------- CLI surface


class TestCli:
    def test_generate_then_verify_via_cli(self, tmp_path: Path) -> None:
        assert build_mod.main(
            ["--per-mode", "1", "--output-dir", str(tmp_path), "--quiet"]
        ) == 0
        assert build_mod.main(["--verify-only", "--output-dir", str(tmp_path), "--quiet"]) == 0

    def test_unknown_mode_is_rejected(self, tmp_path: Path) -> None:
        rc = build_mod.main(
            ["--mode", "not_a_real_mode", "--output-dir", str(tmp_path), "--quiet"]
        )
        assert rc == 2

    def test_single_mode_selection(self, tmp_path: Path) -> None:
        build_mod.main(
            [
                "--mode",
                FailureMode.CLEAN_TOUCHLESS.value,
                "--per-mode",
                "2",
                "--output-dir",
                str(tmp_path),
                "--quiet",
            ]
        )
        manifest = load_manifest(tmp_path / build_mod.MANIFEST_NAME)
        assert set(manifest.mode_counts) == {FailureMode.CLEAN_TOUCHLESS.value}


# ------------------------------------------------------------------- labels


class TestLabels:
    def test_label_selection_is_deterministic_and_wraps(self) -> None:
        pool = LABEL_VARIANTS["total_amount"]
        assert label_for("total_amount", 0) == pool[0]
        assert label_for("total_amount", len(pool)) == pool[0]
        assert label_for("total_amount", 3) == label_for("total_amount", 3)

    def test_adversarial_labels_differ_from_the_common_ones(self) -> None:
        common = set(LABEL_VARIANTS["total_amount"])
        odd = label_for("total_amount", 0, adversarial=True)
        assert odd not in common

    def test_every_canonical_field_has_multiple_spellings(self) -> None:
        # One spelling per field would let a keyword matcher score perfectly
        # while being useless on a second vendor.
        for field, variants in all_known_labels().items():
            assert len(variants) >= 3, f"{field} needs more label variety"

    def test_unknown_field_falls_back_to_a_readable_label(self) -> None:
        assert label_for("some_new_field", 0) == "Some New Field"


# ---------------------------------------------------- drift vs the ERP seed


class TestSeedAgreement:
    """The dataset references the ledger by ID. Drift must fail loudly here."""

    def test_anchor_date_matches(self) -> None:
        assert DATASET_TODAY == erp_seed.TODAY

    def test_vendor_ids_and_names_match(self) -> None:
        actual = {v.Id: v.DisplayName for v in erp_seed.VENDORS}
        assert actual == VENDOR_NAMES

    def test_vendor_bank_last4_matches(self) -> None:
        actual = {v.Id: v.BankAccountLast4 for v in erp_seed.VENDORS}
        assert actual == VENDOR_BANK_LAST4

    def test_referenced_purchase_orders_exist_with_matching_totals(self) -> None:
        erp_pos = {po.DocNumber: po for po in erp_seed.PURCHASE_ORDERS}
        for doc_number, fixture_po in SEED_POS.items():
            assert doc_number in erp_pos, f"{doc_number} missing from the ERP seed"
            erp_po = erp_pos[doc_number]
            assert erp_po.VendorRef.value == fixture_po.vendor_id
            assert erp_po.CurrencyRef.value == fixture_po.currency
            assert erp_po.Line[0].Qty == fixture_po.ordered_qty
            assert erp_po.Line[0].UnitPrice == fixture_po.unit_price

    def test_goods_receipt_expectations_match(self) -> None:
        by_po_id = {g.PurchaseOrderRef.name: g for g in erp_seed.GOODS_RECEIPTS}
        for doc_number, fixture_po in SEED_POS.items():
            grn = by_po_id.get(doc_number)
            if fixture_po.grn_doc_number is None:
                assert grn is None, f"{doc_number} unexpectedly has a receipt"
                continue
            assert grn is not None, f"{doc_number} should have a receipt"
            assert grn.DocNumber == fixture_po.grn_doc_number
            assert grn.Line[0].QtyReceived == fixture_po.received_qty

    def test_historical_duplicate_targets_exist(self) -> None:
        numbers = {b.DocNumber for b in erp_seed.BILLS}
        assert "INV-77001" in numbers
        assert "INV-88001" in numbers

    def test_blocked_and_inactive_vendors_are_as_the_cases_assume(self) -> None:
        by_id = {v.Id: v for v in erp_seed.VENDORS}
        assert by_id["V-1006"].Blocked is True
        assert by_id["V-1005"].Active is False

    def test_expected_gl_accounts_exist_in_the_chart_of_accounts(self) -> None:
        chart = {a.AcctNum for a in erp_seed.ACCOUNTS}
        for i in range(10):
            account = build_case(FailureMode.NON_PO_SERVICES, i).expectation.expected_gl_account
            assert account in chart, f"{account} is not in the chart of accounts"


# ------------------------------------------ real-world layout complexity (INC-006)


class TestRealWorldComplexityContent:
    """The research-grounded document types. Each assertion pins the property
    that makes the type genuinely hard, so a builder change that quietly
    softened the difficulty would fail rather than pass silently."""

    def test_credit_note_amounts_are_negative_and_reconcile(self) -> None:
        for i in range(20):
            c = build_case(FailureMode.CREDIT_NOTE, i).content
            assert c.is_credit_note
            assert c.total_amount < Decimal(0)
            assert c.subtotal < Decimal(0)
            # Signed arithmetic still holds — a credit note is consistent, not broken.
            assert c.subtotal + (c.tax_amount or Decimal(0)) == c.total_amount
            assert c.computed_line_sum == c.subtotal

    def test_credit_note_never_auto_approves(self) -> None:
        for i in range(10):
            exp = build_case(FailureMode.CREDIT_NOTE, i).expectation
            assert exp.route is not ExpectedRoute.AUTO_APPROVE
            assert exp.requires_human_review is True

    def test_multi_line_tax_has_more_than_one_tax_row_that_sums_to_the_scalar(self) -> None:
        for i in range(20):
            c = build_case(FailureMode.MULTI_LINE_TAX, i).content
            assert len(c.tax_lines) >= 2
            rows_sum = sum((t.amount for t in c.tax_lines), Decimal(0))
            assert rows_sum == c.tax_amount
            # And the whole thing still reconciles.
            assert c.subtotal + c.tax_amount == c.total_amount

    def test_reverse_charge_has_zero_tax_and_a_note(self) -> None:
        for i in range(20):
            c = build_case(FailureMode.REVERSE_CHARGE_VAT, i).content
            assert c.tax_amount == Decimal("0.00")
            assert c.subtotal == c.total_amount
            assert c.tax_lines and c.tax_lines[0].is_reverse_charge
            assert c.notes is not None and "reverse charge" in c.notes.lower()

    def test_multi_page_has_enough_lines_to_span_pages(self) -> None:
        for i in range(10):
            c = build_case(FailureMode.MULTI_PAGE, i).content
            assert len(c.lines) >= 38
            # Line sum still reconciles across however many pages it takes.
            assert c.computed_line_sum == c.subtotal

    def test_foreign_currency_is_internally_consistent_non_usd(self) -> None:
        for i in range(20):
            c = build_case(FailureMode.FOREIGN_CURRENCY, i).content
            assert c.currency in ("EUR", "GBP")
            assert c.currency_symbol in ("\u20ac", "\u00a3")
            # Consistent, unlike CURRENCY_MISMATCH: no line-currency override.
            assert c.line_currency_override is None
            assert c.subtotal + (c.tax_amount or Decimal(0)) == c.total_amount

    def test_logo_overlap_flags_a_logo_on_an_otherwise_clean_invoice(self) -> None:
        for i in range(10):
            case = build_case(FailureMode.LOGO_OVERLAP, i)
            assert case.content.has_logo is True
            # The invoice itself is clean and in-tolerance.
            assert case.content.computed_line_sum == case.content.subtotal

    def test_heavy_skew_uses_a_larger_rotation_than_ocr_noise(self) -> None:
        skew_rotations = {abs(build_case(FailureMode.HEAVY_SKEW, i).rotation_degrees) for i in range(5)}
        ocr_rotations = {abs(build_case(FailureMode.OCR_NOISE, i).rotation_degrees) for i in range(5)}
        # Every heavy-skew rotation exceeds the largest ordinary OCR-noise drift.
        assert min(skew_rotations) > max(ocr_rotations)

    def test_tax_lines_inconsistent_with_scalar_is_rejected(self) -> None:
        # The coherence guard: itemised rows must sum to the scalar the manifest
        # stores, or the ground truth would be internally contradictory.
        from apfixtures.spec import InvoiceContent, LineContent, TaxLine

        with pytest.raises(ValueError, match="must equal the printed rows"):
            InvoiceContent(
                invoice_number="INV-1",
                invoice_date=DATASET_TODAY,
                due_date=None,
                vendor_id="V-1001",
                vendor_name_printed="Acme",
                currency="USD",
                subtotal=Decimal("100.00"),
                tax_amount=Decimal("10.00"),
                total_amount=Decimal("110.00"),
                lines=(
                    LineContent(
                        line_number=1,
                        description="x",
                        quantity=Decimal("1"),
                        unit_price=Decimal("100.00"),
                        line_total=Decimal("100.00"),
                    ),
                ),
                tax_lines=(
                    TaxLine(label="A", amount=Decimal("6.00")),
                    TaxLine(label="B", amount=Decimal("3.00")),  # sums to 9, not 10
                ),
            )


class TestRealWorldComplexityRenders:
    def test_multi_page_pdf_has_two_pages(self, tmp_path: Path) -> None:
        case = build_case(FailureMode.MULTI_PAGE, 0)
        payload = render_case(case, variant=0, adversarial_labels=False, rng=_rng())
        path = tmp_path / case.filename
        path.write_bytes(payload)
        assert len(PdfReader(path).pages) >= 2

    def test_credit_note_pdf_titles_itself_a_credit_note(self, tmp_path: Path) -> None:
        case = build_case(FailureMode.CREDIT_NOTE, 0)
        payload = render_case(case, variant=0, adversarial_labels=False, rng=_rng())
        path = tmp_path / case.filename
        path.write_bytes(payload)
        assert "CREDIT NOTE" in PdfReader(path).pages[0].extract_text()

    def test_foreign_currency_pdf_prints_the_symbol(self, tmp_path: Path) -> None:
        case = build_case(FailureMode.FOREIGN_CURRENCY, 0)
        payload = render_case(case, variant=0, adversarial_labels=False, rng=_rng())
        path = tmp_path / case.filename
        path.write_bytes(payload)
        text = PdfReader(path).pages[0].extract_text()
        assert case.content.currency_symbol is not None
        assert case.content.currency_symbol in text

    def test_reverse_charge_pdf_prints_the_note(self, tmp_path: Path) -> None:
        case = build_case(FailureMode.REVERSE_CHARGE_VAT, 0)
        payload = render_case(case, variant=0, adversarial_labels=False, rng=_rng())
        path = tmp_path / case.filename
        path.write_bytes(payload)
        assert "reverse charge" in PdfReader(path).pages[0].extract_text().lower()

    def test_all_new_modes_are_rendered_and_deterministic(self) -> None:
        # Byte-identical on repeat, like every other mode (INC-003).
        for mode in (
            FailureMode.CREDIT_NOTE,
            FailureMode.MULTI_LINE_TAX,
            FailureMode.REVERSE_CHARGE_VAT,
            FailureMode.MULTI_PAGE,
            FailureMode.FOREIGN_CURRENCY,
            FailureMode.LOGO_OVERLAP,
            FailureMode.HEAVY_SKEW,
        ):
            case = build_case(mode, 0)
            a = render_case(case, variant=0, adversarial_labels=False, rng=_rng())
            b = render_case(case, variant=0, adversarial_labels=False, rng=_rng())
            assert hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest(), mode.value
