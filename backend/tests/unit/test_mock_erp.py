"""Mock ERP contract, idempotency, and refusal behaviour.

Run in-process with TestClient, so no container is required.

The two groups that matter most:

* `TestPaymentExecutionBoundary` — asserts the architectural commitment that
  this system cannot move money (ADR-011). Verified mechanically so it cannot
  regress silently.
* `TestIdempotency` — a retried post must not create a second bill. A duplicate
  payment authorisation caused by our own retry would be the worst failure this
  system could produce (NFR-7).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.main import app
from app.store import store
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _reset_ledger() -> None:
    """Every test starts from the deterministic seed."""
    store.reset()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _bill_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "DocNumber": "INV-90001",
        "VendorId": "V-1001",
        "TxnDate": "2026-03-01",
        "DueDate": "2026-03-31",
        "TotalAmt": "2400.00",
        "CurrencyCode": "USD",
        "AccountId": "A-4",
        "PurchaseOrderDocNumber": "PO-2001",
        "GoodsReceiptDocNumber": "GRN-3001",
        "PrivateNote": "Clean three-way match. Auto-approved under touchless threshold.",
    }
    return {**payload, **overrides}


# --------------------------------------------------------------------- boundary


class TestPaymentExecutionBoundary:
    def test_health_declares_no_payment_execution(self, client: TestClient) -> None:
        body = client.get("/health").json()
        assert body["supports_payment_execution"] is False

    def test_no_route_can_move_money(self) -> None:
        # The commitment is the *absence* of the capability, asserted here so it
        # cannot be reintroduced without a failing test.
        forbidden = ("/pay", "/disburse", "/remit", "/payments/execute")
        for route in app.routes:
            path = getattr(route, "path", "")
            methods = getattr(route, "methods", set()) or set()
            writes = methods & {"POST", "PUT", "PATCH", "DELETE"}
            assert not (
                writes and any(f in path for f in forbidden)
            ), f"payment-execution route present: {sorted(writes)} {path}"

    def test_payments_endpoint_is_read_only(self, client: TestClient) -> None:
        assert client.get("/payments").status_code == 200
        # No POST handler exists, so FastAPI reports method-not-allowed.
        assert client.post("/payments", json={}).status_code == 405

    def test_cannot_create_a_bill_already_paid(self, client: TestClient) -> None:
        r = client.post(
            "/bills",
            json=_bill_payload(Status="Paid"),
            headers={"Idempotency-Key": "key-paid-attempt"},
        )
        assert r.status_code == 422
        assert "does not execute payments" in r.json()["Fault"]["Message"]


# ------------------------------------------------------------------ seed state


class TestSeedState:
    def test_counts_match_the_documented_seed(self, client: TestClient) -> None:
        counts = client.get("/health").json()["counts"]
        assert counts == {
            "accounts": 8,
            "vendors": 7,
            "purchase_orders": 8,
            "goods_receipts": 7,
            "bills": 5,
            "payments": 4,
            "exceptions": 0,
        }

    def test_chart_of_accounts_is_ordered_by_number(self, client: TestClient) -> None:
        nums = [a["AcctNum"] for a in client.get("/accounts").json()]
        assert nums == sorted(nums)

    def test_confusable_vendors_are_distinct_records(self, client: TestClient) -> None:
        # Guards against fuzzy resolution over-merging: these are genuinely
        # different companies with different tax IDs and bank accounts.
        acme = client.get("/vendors/V-1001").json()
        industries = client.get("/vendors/V-1002").json()

        assert acme["DisplayName"] == "Acme Corporation"
        assert industries["DisplayName"] == "Acme Industries LLC"
        assert acme["PrimaryTaxIdentifier"] != industries["PrimaryTaxIdentifier"]
        assert acme["BankAccountLast4"] != industries["BankAccountLast4"]

    def test_vendor_search_finds_both_acme_records(self, client: TestClient) -> None:
        names = [v["DisplayName"] for v in client.get("/vendors", params={"name": "acme"}).json()]
        assert names == ["Acme Corporation", "Acme Industries LLC"]

    def test_blocked_and_inactive_vendors_are_distinguishable(self, client: TestClient) -> None:
        blocked = client.get("/vendors/V-1006").json()
        inactive = client.get("/vendors/V-1005").json()

        assert blocked["Blocked"] is True and blocked["Active"] is True
        assert inactive["Blocked"] is False and inactive["Active"] is False

    def test_only_last_four_bank_digits_are_exposed(self, client: TestClient) -> None:
        for vendor in client.get("/vendors").json():
            last4 = vendor.get("BankAccountLast4")
            if last4 is not None:
                assert len(last4) <= 4


# ------------------------------------------------------------ three-way match


class TestThreeWayMatchReads:
    def test_clean_po_has_full_receipt(self, client: TestClient) -> None:
        po = client.get("/purchase-orders/PO-2001").json()
        grns = client.get("/purchase-orders/PO-2001/goods-receipts").json()

        assert po["TotalAmt"] == "2400.00"
        assert len(grns) == 1
        assert grns[0]["IsPartial"] is False
        assert Decimal(grns[0]["Line"][0]["QtyReceived"]) == Decimal("120")

    def test_missing_grn_returns_empty_list_not_error(self, client: TestClient) -> None:
        # "Nothing received yet" is the missing-GRN exception the match must
        # detect, so it is a valid answer rather than a 404.
        r = client.get("/purchase-orders/PO-2004/goods-receipts")
        assert r.status_code == 200
        assert r.json() == []

    def test_partial_receipt_is_flagged_and_short(self, client: TestClient) -> None:
        po = client.get("/purchase-orders/PO-2003").json()
        grn = client.get("/purchase-orders/PO-2003/goods-receipts").json()[0]

        ordered = Decimal(po["Line"][0]["Qty"])
        received = Decimal(grn["Line"][0]["QtyReceived"])
        assert grn["IsPartial"] is True
        assert received < ordered
        assert (ordered, received) == (Decimal("100"), Decimal("60"))

    def test_short_receipt_enables_overbilling_case(self, client: TestClient) -> None:
        po = client.get("/purchase-orders/PO-2005").json()
        grn = client.get("/purchase-orders/PO-2005/goods-receipts").json()[0]
        assert Decimal(po["Line"][0]["Qty"]) == Decimal("60")
        assert Decimal(grn["Line"][0]["QtyReceived"]) == Decimal("50")

    def test_threshold_avoidance_po_sits_just_under_a_doa_boundary(
        self, client: TestClient
    ) -> None:
        # 4950 against the 5000 buyer band in the manufacturing pack.
        po = client.get("/purchase-orders/PO-2006").json()
        assert po["TotalAmt"] == "4950.00"
        assert po["VendorRef"]["value"] == "V-1007"

    def test_unknown_po_is_404(self, client: TestClient) -> None:
        assert client.get("/purchase-orders/PO-DOES-NOT-EXIST").status_code == 404


# ---------------------------------------------------------- duplicate history


class TestBillHistory:
    def test_exact_duplicate_target_exists(self, client: TestClient) -> None:
        bills = client.get("/bills", params={"doc_number": "INV-77001"}).json()
        assert len(bills) == 1
        assert bills[0]["VendorRef"]["value"] == "V-1001"
        assert bills[0]["TotalAmt"] == "1500.00"

    def test_fuzzy_duplicate_target_exists(self, client: TestClient) -> None:
        # INV-88001A arriving later is the near-miss exact matching misses.
        bills = client.get("/bills", params={"doc_number": "INV-88001"}).json()
        assert len(bills) == 1
        assert bills[0]["TotalAmt"] == "2750.00"

    def test_history_filters_by_vendor(self, client: TestClient) -> None:
        bills = client.get("/bills", params={"vendor_id": "V-1004"}).json()
        assert {b["DocNumber"] for b in bills} == {"INIT-4410", "INIT-4455"}

    def test_history_filters_by_date(self, client: TestClient) -> None:
        bills = client.get("/bills", params={"since": "2026-02-01"}).json()
        assert all(b["TxnDate"] >= "2026-02-01" for b in bills)
        assert "INV-77001" not in {b["DocNumber"] for b in bills}

    def test_coding_precedent_is_available(self, client: TestClient) -> None:
        # Non-PO GL coding cites precedent; this is that precedent (FR-6.1).
        bills = client.get("/bills", params={"vendor_id": "V-1004"}).json()
        assert all(b["AccountRef"]["name"] == "Professional Services" for b in bills)


# ----------------------------------------------------------------- idempotency


class TestIdempotency:
    def test_write_requires_an_idempotency_key(self, client: TestClient) -> None:
        r = client.post("/bills", json=_bill_payload())
        assert r.status_code == 422  # missing required header

    def test_replay_returns_the_same_bill_and_does_not_duplicate(
        self, client: TestClient
    ) -> None:
        headers = {"Idempotency-Key": "post-bill-once"}
        before = client.get("/health").json()["counts"]["bills"]

        first = client.post("/bills", json=_bill_payload(), headers=headers)
        second = client.post("/bills", json=_bill_payload(), headers=headers)

        assert first.status_code == 201
        assert second.status_code == 200
        assert second.headers.get("Idempotent-Replay") == "true"
        assert first.json()["Id"] == second.json()["Id"]

        after = client.get("/health").json()["counts"]["bills"]
        assert after == before + 1, "a retry must not create a second bill"

    def test_key_reuse_with_different_payload_is_a_conflict(
        self, client: TestClient
    ) -> None:
        headers = {"Idempotency-Key": "reused-key"}
        client.post("/bills", json=_bill_payload(), headers=headers)
        r = client.post(
            "/bills", json=_bill_payload(TotalAmt="9999.00"), headers=headers
        )
        assert r.status_code == 409
        assert "different payload" in r.json()["Fault"]["Message"]

    def test_distinct_keys_create_distinct_bills(self, client: TestClient) -> None:
        a = client.post(
            "/bills",
            json=_bill_payload(DocNumber="INV-A"),
            headers={"Idempotency-Key": "idem-key-a"},
        )
        b = client.post(
            "/bills",
            json=_bill_payload(DocNumber="INV-B"),
            headers={"Idempotency-Key": "idem-key-b"},
        )
        assert a.json()["Id"] != b.json()["Id"]

    def test_hold_is_idempotent(self, client: TestClient) -> None:
        created = client.post(
            "/bills", json=_bill_payload(), headers={"Idempotency-Key": "idem-make-bill"}
        ).json()

        headers = {"Idempotency-Key": "hold-once"}
        body = {"Reason": "price variance exceeds tolerance", "RaisedBy": "policy_engine"}
        first = client.post(f"/bills/{created['Id']}/hold", json=body, headers=headers)
        second = client.post(f"/bills/{created['Id']}/hold", json=body, headers=headers)

        assert first.json()["BillStatus"] == "OnHold"
        assert second.headers.get("Idempotent-Replay") == "true"
        assert first.json()["HoldReason"] == second.json()["HoldReason"]

    def test_exception_creation_is_idempotent(self, client: TestClient) -> None:
        headers = {"Idempotency-Key": "exc-once"}
        body = {
            "Kind": "MissingGoodsReceipt",
            "Detail": "PO-2004 has no goods receipt; cannot complete three-way match.",
            "RaisedBy": "policy_engine",
            "VendorId": "V-1003",
            "PurchaseOrderDocNumber": "PO-2004",
        }
        first = client.post("/exceptions", json=body, headers=headers)
        second = client.post("/exceptions", json=body, headers=headers)

        assert first.status_code == 201
        assert second.status_code == 200
        assert first.json()["Id"] == second.json()["Id"]
        assert len(client.get("/exceptions").json()) == 1


# -------------------------------------------------------------- ledger refusals


class TestLedgerRefusals:
    """The ERP's own guardrails — the hard boundary in defence in depth.

    These refusals hold regardless of what the agent believes, which is the whole
    point of enforcing at the system of action as well as in the harness.
    """

    def test_blocked_vendor_cannot_be_billed(self, client: TestClient) -> None:
        r = client.post(
            "/bills",
            json=_bill_payload(
                VendorId="V-1006",
                PurchaseOrderDocNumber=None,
                GoodsReceiptDocNumber=None,
            ),
            headers={"Idempotency-Key": "blocked-vendor"},
        )
        assert r.status_code == 422
        assert "blocked" in r.json()["Fault"]["Message"].lower()

    def test_inactive_vendor_cannot_be_billed(self, client: TestClient) -> None:
        r = client.post(
            "/bills",
            json=_bill_payload(
                VendorId="V-1005",
                PurchaseOrderDocNumber=None,
                GoodsReceiptDocNumber=None,
            ),
            headers={"Idempotency-Key": "inactive-vendor"},
        )
        assert r.status_code == 422
        assert "inactive" in r.json()["Fault"]["Message"].lower()

    def test_control_account_is_not_a_valid_coding_target(self, client: TestClient) -> None:
        r = client.post(
            "/bills",
            json=_bill_payload(AccountId="A-8"),  # Accounts Payable
            headers={"Idempotency-Key": "ap-coding"},
        )
        assert r.status_code == 422
        assert "control account" in r.json()["Fault"]["Message"]

    def test_po_belonging_to_another_vendor_is_refused(self, client: TestClient) -> None:
        # PO-2003 is Globex; billing it as Acme would pay the wrong party.
        r = client.post(
            "/bills",
            json=_bill_payload(
                VendorId="V-1001",
                PurchaseOrderDocNumber="PO-2003",
                GoodsReceiptDocNumber=None,
            ),
            headers={"Idempotency-Key": "wrong-vendor-po"},
        )
        assert r.status_code == 422
        assert "belongs to vendor" in r.json()["Fault"]["Message"]

    def test_zero_amount_bill_is_rejected(self, client: TestClient) -> None:
        r = client.post(
            "/bills",
            json=_bill_payload(TotalAmt="0.00"),
            headers={"Idempotency-Key": "zero-amount"},
        )
        assert r.status_code == 422

    def test_unknown_field_is_rejected_not_silently_dropped(
        self, client: TestClient
    ) -> None:
        r = client.post(
            "/bills",
            json=_bill_payload(TotlaAmt="2400.00"),  # typo
            headers={"Idempotency-Key": "typo-field"},
        )
        assert r.status_code == 422

    def test_unknown_grn_is_404(self, client: TestClient) -> None:
        r = client.post(
            "/bills",
            json=_bill_payload(GoodsReceiptDocNumber="GRN-NOPE"),
            headers={"Idempotency-Key": "idem-bad-grn"},
        )
        assert r.status_code == 404


# ------------------------------------------------------------- successful post


class TestSuccessfulPost:
    def test_bill_links_po_and_grn(self, client: TestClient) -> None:
        bill = client.post(
            "/bills", json=_bill_payload(), headers={"Idempotency-Key": "idem-linked"}
        ).json()

        linked = {ref["name"] for ref in bill["LinkedTxn"]}
        assert linked == {"PO-2001", "GRN-3001"}

    def test_rationale_is_persisted_in_the_erp(self, client: TestClient) -> None:
        # The agent's reasoning survives in the customer's system, not only ours.
        bill = client.post(
            "/bills", json=_bill_payload(), headers={"Idempotency-Key": "idem-note"}
        ).json()
        assert "three-way match" in bill["PrivateNote"]

    def test_amount_is_exact_not_floating_point(self, client: TestClient) -> None:
        bill = client.post(
            "/bills",
            json=_bill_payload(TotalAmt="1234.56"),
            headers={"Idempotency-Key": "idem-exact"},
        ).json()
        # Serialised as a string, so no float round-trip occurs.
        assert bill["TotalAmt"] == "1234.56"
        assert Decimal(bill["TotalAmt"]) == Decimal("1234.56")

    def test_new_bill_defaults_to_approved_for_payment(self, client: TestClient) -> None:
        bill = client.post(
            "/bills", json=_bill_payload(), headers={"Idempotency-Key": "idem-status"}
        ).json()
        # The terminal state this system can reach.
        assert bill["BillStatus"] == "Approved"
        assert bill["Balance"] == bill["TotalAmt"]


# ------------------------------------------------------------------ admin reset


class TestAdminReset:
    def test_reset_restores_seed_state(self, client: TestClient) -> None:
        client.post("/bills", json=_bill_payload(), headers={"Idempotency-Key": "idem-reset-x"})
        assert client.get("/health").json()["counts"]["bills"] == 6

        assert client.post("/admin/reset").status_code == 204
        assert client.get("/health").json()["counts"]["bills"] == 5

    def test_reset_clears_idempotency_records(self, client: TestClient) -> None:
        headers = {"Idempotency-Key": "survives-reset"}
        client.post("/bills", json=_bill_payload(), headers=headers)
        client.post("/admin/reset")

        # After a reset the key is unseen again, so this creates rather than replays.
        r = client.post("/bills", json=_bill_payload(), headers=headers)
        assert r.status_code == 201
