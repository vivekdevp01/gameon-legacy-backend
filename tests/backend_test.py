"""Backend API tests for Legacy POS Customer Lookup (updated).

Safety: uses a throwaway branch 'QA_TEMP'. Real seeded branches (Jalandhar/Ludhiana)
are never mutated or purged.
"""
import os
from pathlib import Path

import pytest
import requests

# repo root = .../appss  (this file is at appss/backend/tests/backend_test.py)
ROOT = Path(__file__).resolve().parents[2]


def _backend_url():
    """Resolve the API base URL: env var first, then frontend/.env."""
    url = os.environ.get("REACT_APP_BACKEND_URL", "").strip()
    if not url:
        env_file = ROOT / "frontend" / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("REACT_APP_BACKEND_URL="):
                    url = line.split("=", 1)[1].strip().strip('"')
                    break
    return url.rstrip("/")


BASE_URL = _backend_url()
API = f"{BASE_URL}/api"

QA = "QA_TEMP"

FIXTURES = ROOT / "_ref" / "fixtures"
FIXT_CUST = str(FIXTURES / "test_customer.sql")
FIXT_BILL = str(FIXTURES / "test_billing.sql")
FIXT_PAY = str(FIXTURES / "test_payment.sql")


@pytest.fixture(scope="session", autouse=True)
def _cleanup_qa():
    # Pre-clean: ensure QA_TEMP is empty
    requests.delete(f"{API}/purge", params={"branch": QA})
    yield
    # Post-clean
    requests.delete(f"{API}/purge", params={"branch": QA})


# ---------------- Health ----------------
class TestHealth:
    def test_root(self):
        r = requests.get(f"{API}/")
        assert r.status_code == 200
        assert "message" in r.json()

    def test_dashboard(self):
        r = requests.get(f"{API}/dashboard")
        assert r.status_code == 200
        d = r.json()
        for k in ["customers", "billing", "payments", "vip", "branches", "recent_imports"]:
            assert k in d
        assert d["customers"] > 0  # seeded data exists


# ---------------- Lookup (uses real seeded RFID) ----------------
class TestLookup:
    def test_lookup_exact(self):
        r = requests.get(f"{API}/customers/lookup", params={"rfid": "{47BDE10A}"})
        assert r.status_code == 200
        d = r.json()
        assert d["found"] is True
        assert d["normalized"] == "{47BDE10A}"
        assert d["customer"] is not None

    def test_lookup_lowercase_no_braces(self):
        r = requests.get(f"{API}/customers/lookup", params={"rfid": "47bde10a"})
        assert r.status_code == 200
        d = r.json()
        assert d["found"] is True
        assert d["normalized"] == "{47BDE10A}"

    def test_lookup_not_found(self):
        r = requests.get(f"{API}/customers/lookup", params={"rfid": "{DEADBEEF9}"})
        assert r.status_code == 200
        d = r.json()
        assert d["found"] is False
        assert d["count"] == 0


# ---------------- Import (QA_TEMP branch, fixtures) ----------------
class TestImport:
    def test_import_missing_branch(self):
        with open(FIXT_CUST, "rb") as fh:
            r = requests.post(f"{API}/import", data={"branch": "   "}, files={"file": ("c.sql", fh, "text/plain")})
        assert r.status_code in (400, 422)

    def test_import_unsupported_file(self):
        r = requests.post(f"{API}/import", data={"branch": QA},
                          files={"file": ("empty.sql", b"SELECT 1;", "text/plain")})
        assert r.status_code == 400

    def test_import_customers_autodetect(self):
        with open(FIXT_CUST, "rb") as fh:
            r = requests.post(f"{API}/import", data={"branch": QA}, files={"file": ("test_customer.sql", fh, "text/plain")})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["dataset"] == "customer"
        assert d["imported"] == 2

    def test_import_billing_explicit_dataset(self):
        with open(FIXT_BILL, "rb") as fh:
            r = requests.post(f"{API}/import", data={"branch": QA, "dataset": "billing"},
                              files={"file": ("test_billing.sql", fh, "text/plain")})
        assert r.status_code == 200, r.text
        assert r.json()["dataset"] == "billing"

    def test_import_payment(self):
        with open(FIXT_PAY, "rb") as fh:
            r = requests.post(f"{API}/import", data={"branch": QA}, files={"file": ("test_payment.sql", fh, "text/plain")})
        assert r.status_code == 200
        assert r.json()["dataset"] == "payment"

    def test_import_accumulates(self):
        # Re-import same customers file; count should double
        before = requests.get(f"{API}/customers", params={"branch": QA, "page_size": 1}).json()["total"]
        with open(FIXT_CUST, "rb") as fh:
            r = requests.post(f"{API}/import", data={"branch": QA}, files={"file": ("test_customer.sql", fh, "text/plain")})
        assert r.status_code == 200
        after = requests.get(f"{API}/customers", params={"branch": QA, "page_size": 1}).json()["total"]
        assert after == before + 2


# ---------------- Listing/filters ----------------
class TestList:
    def test_customers_pagination(self):
        r = requests.get(f"{API}/customers", params={"page": 1, "page_size": 5})
        assert r.status_code == 200
        d = r.json()
        assert d["page"] == 1 and d["page_size"] == 5
        assert len(d["items"]) <= 5

    def test_customers_branch_filter_qa(self):
        r = requests.get(f"{API}/customers", params={"branch": QA, "page_size": 50})
        assert r.status_code == 200
        d = r.json()
        assert d["total"] >= 2
        for it in d["items"]:
            assert it["branch"] == QA

    def test_customers_q_name(self):
        r = requests.get(f"{API}/customers", params={"branch": QA, "q": "Fixture Person"})
        assert r.status_code == 200
        d = r.json()
        assert d["total"] >= 1
        assert any("Fixture" in (it.get("name") or "") for it in d["items"])

    def test_customers_q_rfid_normalized(self):
        r = requests.get(f"{API}/customers", params={"branch": QA, "q": "testaaaa"})
        assert r.status_code == 200
        assert r.json()["total"] >= 1

    def test_customers_vip_only(self):
        r = requests.get(f"{API}/customers", params={"branch": QA, "vip_only": True})
        assert r.status_code == 200
        for it in r.json()["items"]:
            assert int(it.get("is_vip") or 0) == 1

    def test_customers_date_filter_created(self):
        r = requests.get(f"{API}/customers", params={"branch": QA, "created_from": "2025-10-01", "created_to": "2025-10-01"})
        assert r.status_code == 200
        assert r.json()["total"] >= 2

    def test_customers_date_filter_active(self):
        r = requests.get(f"{API}/customers", params={"branch": QA, "active_from": "2025-10-02"})
        assert r.status_code == 200
        assert r.json()["total"] >= 1  # only Fixture Person has last_activity 2025-10-02

    def test_billing_list(self):
        r = requests.get(f"{API}/billing", params={"branch": QA, "page_size": 25})
        assert r.status_code == 200
        assert r.json()["total"] >= 2

    def test_payments_list(self):
        r = requests.get(f"{API}/payments", params={"branch": QA, "page_size": 25})
        assert r.status_code == 200
        assert r.json()["total"] >= 1

    def test_billing_date_filter(self):
        r = requests.get(f"{API}/billing", params={"branch": QA, "created_from": "2025-10-01", "created_to": "2025-10-01"})
        assert r.status_code == 200
        assert r.json()["total"] >= 2


# ---------------- History linkage ----------------
class TestHistory:
    def test_history_via_guid(self):
        # Find Fixture Person in QA_TEMP
        r = requests.get(f"{API}/customers", params={"branch": QA, "q": "Fixture Person"})
        items = r.json()["items"]
        assert items, "Fixture Person not found"
        cid = items[0]["id"]
        h = requests.get(f"{API}/customers/{cid}/history").json()
        assert h["customer"]["id"] == cid
        assert h["billing"]["total"] >= 2
        assert h["payments"]["total"] >= 1
        s = h["summary"]
        assert s["games_played"] >= 2
        assert s["total_spent"] >= 250  # 100 + 150
        assert s["total_tokens_earned"] >= 8  # 5+3
        assert s["recharges"] >= 1
        assert s["total_recharged"] >= 500
        assert s["total_collected"] >= 500

    def test_history_404(self):
        r = requests.get(f"{API}/customers/does-not-exist/history")
        assert r.status_code == 404


# ---------------- CRUD ----------------
class TestCRUD:
    created_id = None

    def test_create_missing_branch(self):
        r = requests.post(f"{API}/customers", json={"name": "TEST_x"})
        assert r.status_code == 400

    def test_create_success(self):
        payload = {"branch": QA, "name": "TEST_Dan", "rfid": "cafe0001", "phone": "7000000001", "is_vip": 0}
        r = requests.post(f"{API}/customers", json=payload)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["name"] == "TEST_Dan"
        assert d["rfid_norm"] == "{CAFE0001}"
        TestCRUD.created_id = d["id"]

    def test_get(self):
        r = requests.get(f"{API}/customers/{TestCRUD.created_id}")
        assert r.status_code == 200
        assert r.json()["name"] == "TEST_Dan"

    def test_update_persists(self):
        r = requests.put(f"{API}/customers/{TestCRUD.created_id}", json={"name": "TEST_Dan_Upd", "rfid": "{beef0002}"})
        assert r.status_code == 200
        g = requests.get(f"{API}/customers/{TestCRUD.created_id}").json()
        assert g["name"] == "TEST_Dan_Upd"
        assert g["rfid_norm"] == "{BEEF0002}"

    def test_delete(self):
        r = requests.delete(f"{API}/customers/{TestCRUD.created_id}")
        assert r.status_code == 200
        r2 = requests.get(f"{API}/customers/{TestCRUD.created_id}")
        assert r2.status_code == 404

    def test_get_missing(self):
        assert requests.get(f"{API}/customers/nope").status_code == 404

    def test_delete_missing(self):
        assert requests.delete(f"{API}/customers/nope").status_code == 404
        assert requests.delete(f"{API}/billing/nope").status_code == 404
        assert requests.delete(f"{API}/payments/nope").status_code == 404

    def test_delete_single_billing_row(self):
        r = requests.get(f"{API}/billing", params={"branch": QA, "page_size": 1})
        items = r.json()["items"]
        assert items
        bid = items[0]["id"]
        assert requests.delete(f"{API}/billing/{bid}").status_code == 200


# ---------------- Import batches ----------------
class TestBatches:
    def test_batches_lists_qa(self):
        r = requests.get(f"{API}/import-batches")
        assert r.status_code == 200
        d = r.json()
        assert isinstance(d, list)
        assert any(b["branch"] == QA for b in d)


# ---------------- Purge (only QA_TEMP - NEVER global) ----------------
class TestPurge:
    def test_purge_branch_qa(self):
        r = requests.delete(f"{API}/purge", params={"branch": QA})
        assert r.status_code == 200
        d = r.json()
        assert d["branch"] == QA
        for k in ["customers", "billing", "payments", "import_batches"]:
            assert k in d
        # Verify empty
        assert requests.get(f"{API}/customers", params={"branch": QA}).json()["total"] == 0
        assert requests.get(f"{API}/billing", params={"branch": QA}).json()["total"] == 0
        assert requests.get(f"{API}/payments", params={"branch": QA}).json()["total"] == 0
