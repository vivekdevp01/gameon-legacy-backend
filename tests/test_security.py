# """Security tests for the hardened legacy app. Run with an in-memory Mongo:

#     pip install pytest httpx mongomock-motor
#     cd backend && python -m pytest tests/test_security.py -v

# No real database is touched (mongomock replaces the Mongo driver).
# """
# import base64
# import hashlib
# import hmac
# import json
# import os
# import sys
# import time
# from pathlib import Path

# import pytest

# sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# os.environ.update({
#     "MONGO_URL": "mongodb://unused",
#     "JWT_SECRET": "test-jwt-secret-0123456789-abcdefghij",
#     "MIGRATION_SIGNING_SECRET": "test-signing-secret-0123456789-abcdef",
#     "OWNER_USERNAME": "boss",
#     "OWNER_PASSWORD": "OwnerPass#123",
#     "CORS_ORIGINS": "https://lookup.example.com",
#     "STRICT_SCANNER_INPUT": "0",
#     "SOURCE_LABEL": "funsquare",
# })

# import motor.motor_asyncio as _motor
# from mongomock_motor import AsyncMongoMockClient
# _motor.AsyncIOMotorClient = AsyncMongoMockClient  # before database is imported

# from fastapi.testclient import TestClient  # noqa: E402
# import server  # noqa: E402
# import auth  # noqa: E402

# SECRET = os.environ["MIGRATION_SIGNING_SECRET"]


# def b64d(s):
#     return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# def verify_like_gameon(code, secret=SECRET, now=None):
#     """Mirror of GameOn's verifyLegacyCode (signature + expiry + 60s skew)."""
#     parts = code.split(".")
#     if len(parts) != 3 or parts[0] != "GOL1":
#         return None
#     exp_sig = base64.urlsafe_b64encode(hmac.new(secret.encode(), f"GOL1.{parts[1]}".encode(), hashlib.sha256).digest()).decode().rstrip("=")
#     if not hmac.compare_digest(exp_sig, parts[2]):
#         return None
#     p = json.loads(b64d(parts[1]))
#     if (now or time.time()) > p["exp"] + 60:
#         return None
#     return p


# @pytest.fixture()
# def client():
#     auth.limiter.reset()
#     with TestClient(server.app) as c:
#         # fresh data every test
#         import asyncio
#         loop = asyncio.new_event_loop()
#         async def reset():
#             for coll in (server.customers, server.billing, server.payment, server.app_users, server.audit_log, server.migration_codes):
#                 await coll.delete_many({})
#             await auth.bootstrap_owner()
#             await auth.create_user("cash1", "CashierPass#1", "cashier", "Cash One")
#         loop.run_until_complete(reset())
#         loop.close()
#         yield c


# def login(c, u, p):
#     r = c.post("/api/auth/login", json={"username": u, "password": p})
#     return r


# def hdr(c, u, p):
#     r = login(c, u, p)
#     assert r.status_code == 200, r.text
#     return {"Authorization": "Bearer " + r.json()["token"]}


# def seed(rows):
#     import asyncio
#     loop = asyncio.new_event_loop()
#     loop.run_until_complete(server.customers.insert_many(rows))
#     loop.close()


# def cust(**kw):
#     base = {"id": "x", "branch": "CENTER_1", "guid": "g1", "original_id": 1, "name": "Rahul Sharma",
#             "phone": "9876543210", "rfid": "{47FCC963}", "rfid_norm": "{47FCC963}",
#             "balance_main": 500, "balance_bonus": 100, "total_token": 40,
#             "last_activity": "2026-10-01 10:00:00"}
#     base.update(kw)
#     return base


# OWNER = ("boss", "OwnerPass#123")
# CASH = ("cash1", "CashierPass#1")


# # ------------------------------------------------------------ locking
# def test_every_route_needs_login_except_public(client):
#     public_ok = {("GET", "/api/"), ("GET", "/api/health"), ("POST", "/api/auth/login")}
#     for route in server.app.routes:
#         path = getattr(route, "path", "")
#         if not path.startswith("/api"):
#             continue
#         for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:
#             if (method, path) in public_ok:
#                 continue
#             url = path.replace("{cid}", "x").replace("{bid}", "x").replace("{pid}", "x").replace("{uid}", "x")
#             r = client.request(method, url)
#             assert r.status_code == 401, f"{method} {path} -> {r.status_code}"


# def test_cashier_blocked_from_owner_routes(client):
#     h = hdr(client, *CASH)
#     for method, url in [("GET", "/api/customers"), ("GET", "/api/dashboard"), ("GET", "/api/branches"),
#                         ("DELETE", "/api/purge"), ("POST", "/api/customers"), ("DELETE", "/api/customers/x"),
#                         ("DELETE", "/api/billing/x"), ("DELETE", "/api/payments/x"), ("GET", "/api/billing"),
#                         ("GET", "/api/payments"), ("GET", "/api/users"), ("GET", "/api/audit"),
#                         ("GET", "/api/import-batches"), ("POST", "/api/import"), ("GET", "/api/migration/codes")]:
#         r = client.request(method, url, headers=h)
#         assert r.status_code == 403, f"{method} {url} -> {r.status_code}"


# def test_docs_are_off_and_cors_is_restricted(client):
#     assert client.get("/docs").status_code == 404
#     assert client.get("/openapi.json").status_code == 404
#     r = client.options("/api/health", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
#     assert "access-control-allow-origin" not in r.headers
#     r = client.options("/api/health", headers={"Origin": "https://lookup.example.com", "Access-Control-Request-Method": "GET"})
#     assert r.headers.get("access-control-allow-origin") == "https://lookup.example.com"


# # ------------------------------------------------------------ login
# def test_login_lock_after_five_failures(client):
#     for _ in range(5):
#         assert login(client, "cash1", "wrong").status_code == 401
#     assert login(client, "cash1", "CashierPass#1").status_code == 423


# def test_bad_and_unknown_login_same_message(client):
#     a = login(client, "nobody", "x")
#     b = login(client, "cash1", "x")
#     assert a.status_code == b.status_code == 401
#     assert a.json()["detail"] == b.json()["detail"]


# def test_deactivated_user_loses_session(client):
#     h_owner = hdr(client, *OWNER)
#     h_c = hdr(client, *CASH)
#     assert client.get("/api/auth/me", headers=h_c).status_code == 200
#     uid = [u for u in client.get("/api/users", headers=h_owner).json() if u["username"] == "cash1"][0]["id"]
#     assert client.patch(f"/api/users/{uid}", json={"active": False}, headers=h_owner).status_code == 200
#     assert client.get("/api/auth/me", headers=h_c).status_code == 401


# def test_cannot_remove_last_owner(client):
#     h = hdr(client, *OWNER)
#     me = client.get("/api/auth/me", headers=h).json()["user"]
#     assert client.patch(f"/api/users/{me['id']}", json={"active": False}, headers=h).status_code == 400


# def test_tampered_token_rejected(client):
#     h = hdr(client, *CASH)
#     tok = h["Authorization"].split(" ")[1]
#     body, sig = tok.split(".")
#     payload = json.loads(b64d(body))
#     payload["role"] = "owner"
#     fake_body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
#     r = client.get("/api/customers", headers={"Authorization": f"Bearer {fake_body}.{sig}"})
#     assert r.status_code == 401


# # ------------------------------------------------------------ cashier view
# def test_cashier_lookup_is_masked(client):
#     seed([cust()])
#     h = hdr(client, *CASH)
#     r = client.get("/api/customers/lookup", params={"rfid": "47FCC963"}, headers=h).json()
#     assert r["found"] and r["customer"]["phone_masked"] == "98XXXXX210"
#     text = json.dumps(r)
#     assert "9876543210" not in text and "balance" not in text and "matches" not in text
#     ro = client.get("/api/customers/lookup", params={"rfid": "47FCC963"}, headers=hdr(client, *OWNER)).json()
#     assert ro["customer"]["phone"] == "9876543210"


# # ------------------------------------------------------------ codes
# def issue(client, h, rfid="{47FCC963}", src="wedge"):
#     return client.post("/api/migration/codes", json={"rfid": rfid, "inputSource": src}, headers=h)


# def test_code_roundtrip_and_tamper(client):
#     seed([cust()])
#     r = issue(client, hdr(client, *CASH))
#     assert r.status_code == 200, r.text
#     body = r.json()
#     assert "amounts" not in body  # cashier never sees amounts
#     p = verify_like_gameon(body["code"])
#     assert p and p["main"] == 500 and p["bonus"] == 100 and p["tickets"] == 40
#     assert p["source"] == "funsquare" and p["recordKey"] == "CENTER_1|g1"
#     assert p["phoneLast3"] == "210" and p["phoneHash"] and not p["flags"]["junkPhone"]
#     assert "9876543210" not in json.dumps(p)
#     # edit the amount -> signature breaks
#     parts = body["code"].split(".")
#     forged = json.loads(b64d(parts[1])); forged["main"] = 5000
#     nb = base64.urlsafe_b64encode(json.dumps(forged).encode()).decode().rstrip("=")
#     assert verify_like_gameon(f"GOL1.{nb}.{parts[2]}") is None
#     # wrong secret
#     assert verify_like_gameon(body["code"], secret="another-secret-another-secret-123") is None
#     # expiry (+16 min)
#     assert verify_like_gameon(body["code"], now=time.time() + 16 * 60 + 61) is None


# def test_phone_hash_matches_gameon_formula(client):
#     seed([cust()])
#     p = verify_like_gameon(issue(client, hdr(client, *CASH)).json()["code"])
#     expect = hmac.new(SECRET.encode(), b"phone:9876543210", hashlib.sha256).hexdigest()
#     assert p["phoneHash"] == expect


# def test_junk_phone_and_negative_and_zero(client):
#     seed([cust(phone="12345", balance_main=-1, balance_bonus=50, total_token=0)])
#     p = verify_like_gameon(issue(client, hdr(client, *CASH)).json()["code"])
#     assert p["flags"]["junkPhone"] and p["phoneHash"] is None and p["main"] == 0 and p["bonus"] == 50
#     import asyncio
#     loop = asyncio.new_event_loop(); loop.run_until_complete(server.customers.delete_many({})); loop.close()
#     seed([cust(balance_main=0, balance_bonus=0, total_token=0)])
#     assert issue(client, hdr(client, *CASH)).status_code == 422


# def test_duplicate_rfid_latest_valued_and_ambiguous(client):
#     seed([
#         cust(id="a", guid="old", balance_main=100, last_activity="2025-01-01 00:00:00"),
#         cust(id="b", guid="new", balance_main=300, last_activity="2026-06-01 00:00:00"),
#         cust(id="c", guid="zero", balance_main=0, balance_bonus=0, total_token=0, last_activity="2026-10-10 00:00:00"),
#     ])
#     r = issue(client, hdr(client, *CASH)).json()
#     p = verify_like_gameon(r["code"])
#     assert p["recordKey"] == "CENTER_1|new" and p["main"] == 300 and p["flags"]["ambiguous"] is True


# def test_new_10_char_uid_rejected(client):
#     seed([cust()])
#     assert issue(client, hdr(client, *CASH), rfid="{0123456789}").status_code == 400
#     assert issue(client, hdr(client, *CASH), rfid="{47FCC9}").status_code == 400


# def test_strict_scanner_blocks_typed_for_cashier(client, monkeypatch):
#     seed([cust()])
#     monkeypatch.setattr(server, "STRICT_SCANNER_INPUT", True)
#     assert issue(client, hdr(client, *CASH), src="typed").status_code == 403
#     assert issue(client, hdr(client, *CASH), src="wedge").status_code == 200


# def test_misses_are_limited_and_audited(client):
#     h = hdr(client, *CASH)
#     codes = [issue(client, h, rfid="{AAAAAAA%d}" % (i % 10)).status_code for i in range(12)]
#     assert 429 in codes
#     ev = [a["event"] for a in client.get("/api/audit", headers=hdr(client, *OWNER)).json()["items"]]
#     assert "lookup_not_found" in ev


# def test_audit_never_contains_code_or_password(client):
#     seed([cust()])
#     r = issue(client, hdr(client, *CASH)).json()
#     login(client, "cash1", "NotThePassword")
#     dump = json.dumps(client.get("/api/audit", headers=hdr(client, *OWNER)).json())
#     assert r["code"] not in dump and "CashierPass#1" not in dump and "NotThePassword" not in dump


# # ------------------------------------------------------------ statement
# def test_cashier_can_show_statement_for_tapped_card(client):
#     seed([cust()])
#     import asyncio
#     loop = asyncio.new_event_loop()
#     loop.run_until_complete(server.billing.insert_one({"id": "b1", "branch": "CENTER_1", "guid": "g1", "customer_id": 1, "amount": 50, "created_at": "2026-10-01 10:00:00", "log_game": "Air Hockey"}))
#     loop.run_until_complete(server.payment.insert_one({"id": "p1", "branch": "CENTER_1", "guid": "g1", "customer_id": 1, "recharge_amount": 500, "created_at": "2026-09-01 10:00:00"}))
#     loop.close()
#     h = hdr(client, *CASH)
#     r = client.get("/api/customers/statement", params={"rfid": "{47FCC963}", "inputSource": "wedge"}, headers=h)
#     assert r.status_code == 200, r.text
#     d = r.json()["customers"][0]
#     assert d["balance_main"] == 500 and d["phone_masked"] == "98XXXXX210"
#     assert d["billing"][0]["log_game"] == "Air Hockey" and d["payments"][0]["recharge_amount"] == 500
#     assert "9876543210" not in json.dumps(r.json())
#     ev = [a["event"] for a in client.get("/api/audit", headers=hdr(client, *OWNER)).json()["items"]]
#     assert "statement_viewed" in ev


# def test_statement_needs_login_and_blocks_typed_when_strict(client, monkeypatch):
#     seed([cust()])
#     assert client.get("/api/customers/statement", params={"rfid": "{47FCC963}"}).status_code == 401
#     monkeypatch.setattr(server, "STRICT_SCANNER_INPUT", True)
#     h = hdr(client, *CASH)
#     assert client.get("/api/customers/statement", params={"rfid": "{47FCC963}", "inputSource": "typed"}, headers=h).status_code == 403


# def test_email_style_username_works(client):
#     import asyncio
#     loop = asyncio.new_event_loop()
#     loop.run_until_complete(auth.create_user("Alok.P@Example.com", "Another#Pass1", "cashier"))
#     loop.close()
#     assert login(client, "alok.p@example.com", "Another#Pass1").status_code == 200

"""Security tests for the hardened legacy app. Run with an in-memory Mongo:

    pip install pytest httpx mongomock-motor
    cd backend && python -m pytest tests/test_security.py -v

No real database is touched (mongomock replaces the Mongo driver).
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.update({
    "MONGO_URL": "mongodb://unused",
    "JWT_SECRET": "test-jwt-secret-0123456789-abcdefghij",
    "MIGRATION_SIGNING_SECRET": "test-signing-secret-0123456789-abcdef",
    "OWNER_USERNAME": "boss",
    "OWNER_PASSWORD": "OwnerPass#123",
    "CORS_ORIGINS": "https://lookup.example.com",
    "STRICT_SCANNER_INPUT": "0",
    "SOURCE_LABEL": "funsquare",
})

import motor.motor_asyncio as _motor
from mongomock_motor import AsyncMongoMockClient
_motor.AsyncIOMotorClient = AsyncMongoMockClient  # before database is imported

from fastapi.testclient import TestClient  # noqa: E402
import server  # noqa: E402
import auth  # noqa: E402

SECRET = os.environ["MIGRATION_SIGNING_SECRET"]


def b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify_like_gameon(code, secret=SECRET, now=None):
    """Mirror of GameOn's verifyLegacyCode (signature + expiry + 60s skew)."""
    parts = code.split(".")
    if len(parts) != 3 or parts[0] != "GOL1":
        return None
    exp_sig = base64.urlsafe_b64encode(hmac.new(secret.encode(), f"GOL1.{parts[1]}".encode(), hashlib.sha256).digest()).decode().rstrip("=")
    if not hmac.compare_digest(exp_sig, parts[2]):
        return None
    p = json.loads(b64d(parts[1]))
    if (now or time.time()) > p["exp"] + 60:
        return None
    return p


@pytest.fixture()
def client():
    auth.limiter.reset()
    with TestClient(server.app) as c:
        # fresh data every test
        import asyncio
        loop = asyncio.new_event_loop()
        async def reset():
            for coll in (server.customers, server.billing, server.payment, server.app_users, server.audit_log, server.migration_codes):
                await coll.delete_many({})
            await auth.bootstrap_owner()
            await auth.create_user("cash1", "CashierPass#1", "cashier", "Cash One")
            await auth.create_user("mgr1", "ManagerPass#1", "manager", "Manager One")
        loop.run_until_complete(reset())
        loop.close()
        yield c


def login(c, u, p):
    r = c.post("/api/auth/login", json={"username": u, "password": p})
    return r


def hdr(c, u, p):
    r = login(c, u, p)
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def seed(rows):
    import asyncio
    loop = asyncio.new_event_loop()
    loop.run_until_complete(server.customers.insert_many(rows))
    loop.close()


def cust(**kw):
    base = {"id": "x", "branch": "CENTER_1", "guid": "g1", "original_id": 1, "name": "Rahul Sharma",
            "phone": "9876543210", "phone_norm": "9876543210", "rfid": "{47FCC963}", "rfid_norm": "{47FCC963}",
            "balance_main": 500, "balance_bonus": 100, "total_token": 40,
            "last_activity": "2026-10-01 10:00:00"}
    base.update(kw)
    return base


OWNER = ("boss", "OwnerPass#123")
CASH = ("cash1", "CashierPass#1")
MGR = ("mgr1", "ManagerPass#1")


# ------------------------------------------------------------ locking
def test_every_route_needs_login_except_public(client):
    public_ok = {("GET", "/api/"), ("GET", "/api/health"), ("POST", "/api/auth/login")}
    for route in server.app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api"):
            continue
        for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:
            if (method, path) in public_ok:
                continue
            url = path.replace("{cid}", "x").replace("{bid}", "x").replace("{pid}", "x").replace("{uid}", "x")
            r = client.request(method, url)
            assert r.status_code == 401, f"{method} {path} -> {r.status_code}"


def test_cashier_blocked_from_owner_routes(client):
    h = hdr(client, *CASH)
    for method, url in [("GET", "/api/customers"), ("GET", "/api/dashboard"), ("GET", "/api/branches"),
                        ("DELETE", "/api/purge"), ("POST", "/api/customers"), ("DELETE", "/api/customers/x"),
                        ("DELETE", "/api/billing/x"), ("DELETE", "/api/payments/x"), ("GET", "/api/billing"),
                        ("GET", "/api/payments"), ("GET", "/api/users"), ("GET", "/api/audit"),
                        ("GET", "/api/import-batches"), ("POST", "/api/import"), ("GET", "/api/migration/codes")]:
        r = client.request(method, url, headers=h)
        assert r.status_code == 403, f"{method} {url} -> {r.status_code}"


def test_docs_are_off_and_cors_is_restricted(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    r = client.options("/api/health", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in r.headers
    r = client.options("/api/health", headers={"Origin": "https://lookup.example.com", "Access-Control-Request-Method": "GET"})
    assert r.headers.get("access-control-allow-origin") == "https://lookup.example.com"


# ------------------------------------------------------------ login
def test_login_lock_after_five_failures(client):
    for _ in range(5):
        assert login(client, "cash1", "wrong").status_code == 401
    assert login(client, "cash1", "CashierPass#1").status_code == 423


def test_bad_and_unknown_login_same_message(client):
    a = login(client, "nobody", "x")
    b = login(client, "cash1", "x")
    assert a.status_code == b.status_code == 401
    assert a.json()["detail"] == b.json()["detail"]


def test_deactivated_user_loses_session(client):
    h_owner = hdr(client, *OWNER)
    h_c = hdr(client, *CASH)
    assert client.get("/api/auth/me", headers=h_c).status_code == 200
    uid = [u for u in client.get("/api/users", headers=h_owner).json() if u["username"] == "cash1"][0]["id"]
    assert client.patch(f"/api/users/{uid}", json={"active": False}, headers=h_owner).status_code == 200
    assert client.get("/api/auth/me", headers=h_c).status_code == 401


def test_cannot_remove_last_owner(client):
    h = hdr(client, *OWNER)
    me = client.get("/api/auth/me", headers=h).json()["user"]
    assert client.patch(f"/api/users/{me['id']}", json={"active": False}, headers=h).status_code == 400


def test_tampered_token_rejected(client):
    h = hdr(client, *CASH)
    tok = h["Authorization"].split(" ")[1]
    body, sig = tok.split(".")
    payload = json.loads(b64d(body))
    payload["role"] = "owner"
    fake_body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    r = client.get("/api/customers", headers={"Authorization": f"Bearer {fake_body}.{sig}"})
    assert r.status_code == 401


# ------------------------------------------------------------ cashier view
def test_cashier_lookup_is_masked(client):
    seed([cust()])
    h = hdr(client, *CASH)
    r = client.get("/api/customers/lookup", params={"rfid": "47FCC963"}, headers=h).json()
    assert r["found"] and r["customer"]["phone_masked"] == "98XXXXX210"
    text = json.dumps(r)
    assert "9876543210" not in text and "balance" not in text and "matches" not in text
    ro = client.get("/api/customers/lookup", params={"rfid": "47FCC963"}, headers=hdr(client, *OWNER)).json()
    assert ro["customer"]["phone"] == "9876543210"


# ------------------------------------------------------------ codes
def issue(client, h, rfid="{47FCC963}", src="wedge"):
    return client.post("/api/migration/codes", json={"rfid": rfid, "inputSource": src}, headers=h)


def test_code_roundtrip_and_tamper(client):
    seed([cust()])
    r = issue(client, hdr(client, *CASH))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "amounts" not in body  # cashier never sees amounts
    p = verify_like_gameon(body["code"])
    assert p and p["main"] == 500 and p["bonus"] == 100 and p["tickets"] == 40
    assert p["source"] == "funsquare" and p["recordKey"] == "CENTER_1|g1"
    assert p["phoneLast3"] == "210" and p["phoneHash"] and not p["flags"]["junkPhone"]
    assert "9876543210" not in json.dumps(p)
    # edit the amount -> signature breaks
    parts = body["code"].split(".")
    forged = json.loads(b64d(parts[1])); forged["main"] = 5000
    nb = base64.urlsafe_b64encode(json.dumps(forged).encode()).decode().rstrip("=")
    assert verify_like_gameon(f"GOL1.{nb}.{parts[2]}") is None
    # wrong secret
    assert verify_like_gameon(body["code"], secret="another-secret-another-secret-123") is None
    # expiry (+16 min)
    assert verify_like_gameon(body["code"], now=time.time() + 16 * 60 + 61) is None


def test_phone_hash_matches_gameon_formula(client):
    seed([cust()])
    p = verify_like_gameon(issue(client, hdr(client, *CASH)).json()["code"])
    expect = hmac.new(SECRET.encode(), b"phone:9876543210", hashlib.sha256).hexdigest()
    assert p["phoneHash"] == expect


def test_junk_phone_and_negative_and_zero(client):
    seed([cust(phone="12345", balance_main=-1, balance_bonus=50, total_token=0)])
    p = verify_like_gameon(issue(client, hdr(client, *CASH)).json()["code"])
    assert p["flags"]["junkPhone"] and p["phoneHash"] is None and p["main"] == 0 and p["bonus"] == 50
    import asyncio
    loop = asyncio.new_event_loop(); loop.run_until_complete(server.customers.delete_many({})); loop.close()
    seed([cust(balance_main=0, balance_bonus=0, total_token=0)])
    assert issue(client, hdr(client, *CASH)).status_code == 422


def test_duplicate_rfid_latest_valued_and_ambiguous(client):
    seed([
        cust(id="a", guid="old", balance_main=100, last_activity="2025-01-01 00:00:00"),
        cust(id="b", guid="new", balance_main=300, last_activity="2026-06-01 00:00:00"),
        cust(id="c", guid="zero", balance_main=0, balance_bonus=0, total_token=0, last_activity="2026-10-10 00:00:00"),
    ])
    r = issue(client, hdr(client, *CASH)).json()
    p = verify_like_gameon(r["code"])
    assert p["recordKey"] == "CENTER_1|new" and p["main"] == 300 and p["flags"]["ambiguous"] is True


def test_new_10_char_uid_rejected(client):
    seed([cust()])
    assert issue(client, hdr(client, *CASH), rfid="{0123456789}").status_code == 400
    assert issue(client, hdr(client, *CASH), rfid="{47FCC9}").status_code == 400


def test_strict_scanner_blocks_typed_for_cashier(client, monkeypatch):
    seed([cust()])
    monkeypatch.setattr(server, "STRICT_SCANNER_INPUT", True)
    assert issue(client, hdr(client, *CASH), src="typed").status_code == 403
    assert issue(client, hdr(client, *CASH), src="wedge").status_code == 200


def test_misses_are_limited_and_audited(client):
    h = hdr(client, *CASH)
    codes = [issue(client, h, rfid="{AAAAAAA%d}" % (i % 10)).status_code for i in range(12)]
    assert 429 in codes
    ev = [a["event"] for a in client.get("/api/audit", headers=hdr(client, *OWNER)).json()["items"]]
    assert "lookup_not_found" in ev


def test_audit_never_contains_code_or_password(client):
    seed([cust()])
    r = issue(client, hdr(client, *CASH)).json()
    login(client, "cash1", "NotThePassword")
    dump = json.dumps(client.get("/api/audit", headers=hdr(client, *OWNER)).json())
    assert r["code"] not in dump and "CashierPass#1" not in dump and "NotThePassword" not in dump


# ------------------------------------------------------------ statement
def test_cashier_can_show_statement_for_tapped_card(client):
    seed([cust()])
    import asyncio
    loop = asyncio.new_event_loop()
    loop.run_until_complete(server.billing.insert_one({"id": "b1", "branch": "CENTER_1", "guid": "g1", "customer_id": 1, "amount": 50, "created_at": "2026-10-01 10:00:00", "log_game": "Air Hockey"}))
    loop.run_until_complete(server.payment.insert_one({"id": "p1", "branch": "CENTER_1", "guid": "g1", "customer_id": 1, "recharge_amount": 500, "created_at": "2026-09-01 10:00:00"}))
    loop.close()
    h = hdr(client, *CASH)
    r = client.get("/api/customers/statement", params={"rfid": "{47FCC963}", "inputSource": "wedge"}, headers=h)
    assert r.status_code == 200, r.text
    d = r.json()["customers"][0]
    assert d["balance_main"] == 500 and d["phone_masked"] == "98XXXXX210"
    assert d["billing"][0]["log_game"] == "Air Hockey" and d["payments"][0]["recharge_amount"] == 500
    assert "9876543210" not in json.dumps(r.json())
    ev = [a["event"] for a in client.get("/api/audit", headers=hdr(client, *OWNER)).json()["items"]]
    assert "statement_viewed" in ev


def test_statement_needs_login_and_blocks_typed_when_strict(client, monkeypatch):
    seed([cust()])
    assert client.get("/api/customers/statement", params={"rfid": "{47FCC963}"}).status_code == 401
    monkeypatch.setattr(server, "STRICT_SCANNER_INPUT", True)
    h = hdr(client, *CASH)
    assert client.get("/api/customers/statement", params={"rfid": "{47FCC963}", "inputSource": "typed"}, headers=h).status_code == 403


def test_email_style_username_works(client):
    import asyncio
    loop = asyncio.new_event_loop()
    loop.run_until_complete(auth.create_user("Alok.P@Example.com", "Another#Pass1", "cashier"))
    loop.close()
    assert login(client, "alok.p@example.com", "Another#Pass1").status_code == 200


# ------------------------------------------------------------ manager: find by phone
def test_cashier_cannot_search_by_phone(client):
    seed([cust()])
    h = hdr(client, *CASH)
    assert client.post("/api/migration/find-by-phone", json={"phone": "9876543210"}, headers=h).status_code == 403
    assert client.post("/api/migration/codes/by-phone", json={"phone": "9876543210", "recordIds": ["x"]}, headers=h).status_code == 403


def test_manager_phone_search_exact_only_and_no_balance(client):
    seed([cust(id="a"), cust(id="b", guid="g2", original_id=2, rfid="{11AA22BB}", rfid_norm="{11AA22BB}", balance_main=0, balance_bonus=0, total_token=0)])
    h = hdr(client, *MGR)
    r = client.post("/api/migration/find-by-phone", json={"phone": "98765 43210"}, headers=h)
    assert r.status_code == 200, r.text
    recs = r.json()["records"]
    assert len(recs) == 2 and {x["id"] for x in recs} == {"a", "b"}
    txt = json.dumps(r.json())
    assert "9876543210" not in txt and "balance" not in txt.replace("has_balance", "")
    assert recs[0]["phone_masked"] == "98XXXXX210"
    # partial / short numbers are refused
    for bad in ["98765", "9876", "", "abcdefghij"]:
        assert client.post("/api/migration/find-by-phone", json={"phone": bad}, headers=h).status_code == 400


def test_junk_phone_not_searchable(client):
    seed([cust(phone="12345", phone_norm=None)])
    h = hdr(client, *MGR)
    assert client.post("/api/migration/find-by-phone", json={"phone": "12345"}, headers=h).status_code == 400


def test_manager_codes_have_nocard_flag_and_check_phone(client):
    seed([cust(id="a"), cust(id="c", guid="g3", original_id=3, phone="9000000001", phone_norm="9000000001", rfid="{22BB33CC}", rfid_norm="{22BB33CC}")])
    h = hdr(client, *MGR)
    r = client.post("/api/migration/codes/by-phone", json={"phone": "9876543210", "recordIds": ["a", "c", "a"]}, headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert len(d["codes"]) == 1  # duplicate id collapsed, record 'c' belongs to another phone
    assert d["skipped"][0]["id"] == "c"
    p = verify_like_gameon(d["codes"][0]["code"])
    assert p["flags"]["noCard"] is True and p["inputSource"] == "phone_search" and p["main"] == 500
    assert "amounts" not in d["codes"][0]  # manager never sees amounts


def test_manager_daily_cap_and_audit(client, monkeypatch):
    seed([cust(id=str(i), guid=f"g{i}", original_id=i, rfid="{AA00BB%02d}" % i, rfid_norm="{AA00BB%02d}" % i) for i in range(1, 5)])
    monkeypatch.setattr(server, "MANAGER_DAILY_NOCARD_CAP", 3)
    h = hdr(client, *MGR)
    assert client.post("/api/migration/codes/by-phone", json={"phone": "9876543210", "recordIds": ["1", "2", "3"]}, headers=h).status_code == 200
    assert client.post("/api/migration/codes/by-phone", json={"phone": "9876543210", "recordIds": ["4"]}, headers=h).status_code == 429
    ev = [a for a in client.get("/api/audit", headers=hdr(client, *OWNER)).json()["items"]]
    assert any(e["event"] == "nocard_daily_cap_hit" for e in ev)
    assert "9876543210" not in json.dumps(ev)  # the number itself is never logged


def test_manager_cannot_use_owner_routes(client):
    h = hdr(client, *MGR)
    for method, url in [("GET", "/api/customers"), ("DELETE", "/api/purge"), ("GET", "/api/users"), ("GET", "/api/dashboard")]:
        assert client.request(method, url, headers=h).status_code == 403


def test_backfill_adds_phone_norm_without_touching_data(client):
    import asyncio
    loop = asyncio.new_event_loop()
    async def go():
        await server.customers.insert_one({"id": "z", "branch": "B", "name": "Old", "phone": "+91 98765-43210", "rfid_norm": "{47FCC963}"})
        # mongomock (the in-memory test DB) does not support bulk_write with the
        # newest pymongo; real MongoDB does. Emulate it one by one here.
        coll = server.customers
        async def fake_bulk(ops, ordered=False):
            for op in ops:
                await coll.update_one(op._filter, op._doc)
        coll.bulk_write = fake_bulk
        await server._backfill_phone_norm()
        return await server.customers.find_one({"id": "z"})
    doc = loop.run_until_complete(go()); loop.close()
    assert doc["phone_norm"] == "9876543210" and doc["phone"] == "+91 98765-43210" and doc["name"] == "Old"