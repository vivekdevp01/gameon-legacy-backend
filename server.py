import re
import os
import uuid
import logging
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, APIRouter, UploadFile, File, Form, HTTPException, Query
from starlette.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from database import (
    db, customers, billing, payment, import_batches, ensure_indexes,
)
from sql_parser import (
    iter_inserts, detect_table,
    CUSTOMER_STRING_COLS, BILLING_STRING_COLS, PAYMENT_STRING_COLS,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        await ensure_indexes()
    except Exception as e:  # pragma: no cover - a bad Mongo URL shouldn't block boot
        logger.warning("Index creation issue: %s", e)
    yield


app = FastAPI(title="Legacy POS Customer Lookup", lifespan=lifespan)
api = APIRouter(prefix="/api")

DATASETS = {
    "es_customer": {"coll": customers, "strings": CUSTOMER_STRING_COLS, "name": "customer"},
    "es_billing": {"coll": billing, "strings": BILLING_STRING_COLS, "name": "billing"},
    "es_payment": {"coll": payment, "strings": PAYMENT_STRING_COLS, "name": "payment"},
}

# Rows sent to MongoDB per insert_many call.
IMPORT_BATCH = 2000

# Browser uploads can be unlimited locally by setting MAX_UPLOAD_MB=0. Hosted
# deployments should keep a finite value because their proxy/body limits still
# apply even when the application accepts the request.
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "40"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024 if MAX_UPLOAD_MB > 0 else None


def normalize_rfid(value):
    if value is None:
        return None
    s = str(value).strip().upper()
    if not s:
        return None
    m = re.search(r"\{?([0-9A-F]+)\}?", s)
    inner = m.group(1) if m else re.sub(r"[^0-9A-Z]", "", s)
    return "{" + inner + "}"


def clean(doc):
    if doc:
        doc.pop("_id", None)
    return doc


def build_doc(row, branch, filename, now_iso):
    """Turn one parsed dump row into the document we store."""
    doc = dict(row)
    # Each branch export restarts ids at 1, so the legacy id can't be our key.
    doc["original_id"] = doc.pop("id", None)
    doc["branch"] = branch
    doc["source_file"] = filename
    doc["imported_at"] = now_iso
    doc["id"] = str(uuid.uuid4())
    if "rfid" in row:
        doc["rfid_norm"] = normalize_rfid(row.get("rfid"))
    return doc


def build_docs(rows, branch, filename, now_iso):
    return [build_doc(r, branch, filename, now_iso) for r in rows]


# ---------------- Pydantic ----------------
class CustomerUpsert(BaseModel):
    branch: Optional[str] = None
    guid: Optional[str] = None
    rfid: Optional[str] = None
    name: Optional[str] = None
    dob: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    payment_mode: Optional[str] = None
    balance_main: Optional[int] = None
    balance_bonus: Optional[int] = None
    status: Optional[str] = None
    last_activity: Optional[str] = None
    created_at: Optional[str] = None
    is_vip: Optional[int] = None
    is_free_time: Optional[str] = None
    total_token: Optional[int] = None
    user: Optional[str] = None
    location: Optional[str] = None
    card_sno: Optional[int] = None
    center_code: Optional[str] = None
    updated_by: Optional[str] = None


# ---------------- Routes ----------------
@api.get("/")
async def root():
    return {"message": "Legacy POS Customer Lookup API"}


@api.post("/import")
async def import_dump(
    branch: str = Form(...),
    file: UploadFile = File(...),
    dataset: Optional[str] = Form(None),
):
    branch = (branch or "").strip()
    if not branch:
        raise HTTPException(status_code=400, detail="Branch label is required")

    # Spool the upload to disk rather than holding the raw bytes and the decoded
    # text in memory at the same time.
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sql") as tmp:
            tmp_path = tmp.name
            size = 0
            while True:
                chunk = await file.read(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if MAX_UPLOAD_BYTES is not None and size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"File is larger than the {MAX_UPLOAD_MB} MB upload limit. "
                            "Import big dumps with backend/import_sql.py, which writes "
                            "straight to MongoDB and has no size limit."
                        ),
                    )
                tmp.write(chunk)

        with open(tmp_path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    table = None
    if dataset:
        table = {"customer": "es_customer", "billing": "es_billing", "payment": "es_payment"}.get(dataset.lower())
    if not table:
        table = detect_table(text)
    if not table or table not in DATASETS:
        raise HTTPException(status_code=400, detail="Could not find a supported table (es_customer, es_billing or es_payment) in this file.")

    cfg = DATASETS[table]
    now_iso = datetime.now(timezone.utc).isoformat()

    # Insert in batches as rows are parsed, so peak memory stays flat no matter
    # how many rows the dump holds.
    imported = 0
    batch = []
    for row in iter_inserts(text, table, cfg["strings"]):
        batch.append(build_doc(row, branch, file.filename, now_iso))
        if len(batch) >= IMPORT_BATCH:
            await cfg["coll"].insert_many(batch, ordered=False)
            imported += len(batch)
            batch = []
    if batch:
        await cfg["coll"].insert_many(batch, ordered=False)
        imported += len(batch)

    if imported == 0:
        raise HTTPException(status_code=400, detail=f"No INSERT rows found for {table} in this file.")

    await import_batches.insert_one({
        "id": str(uuid.uuid4()), "branch": branch, "dataset": cfg["name"],
        "filename": file.filename, "row_count": imported, "created_at": now_iso,
    })
    return {"imported": imported, "branch": branch, "dataset": cfg["name"], "filename": file.filename}


@api.get("/branches")
async def list_branches():
    pipeline = [{"$group": {"_id": "$branch", "count": {"$sum": 1}}}, {"$sort": {"_id": 1}}]
    out = []
    async for r in customers.aggregate(pipeline):
        out.append({"branch": r["_id"], "count": r["count"]})
    return out


async def _per_branch(coll):
    res = {}
    async for r in coll.aggregate([{"$group": {"_id": "$branch", "count": {"$sum": 1}}}]):
        res[r["_id"]] = r["count"]
    return res


@api.get("/dashboard")
async def dashboard():
    cust_total = await customers.count_documents({})
    bill_total = await billing.count_documents({})
    pay_total = await payment.count_documents({})
    vip = await customers.count_documents({"is_vip": 1})

    cb, bb, pb = await _per_branch(customers), await _per_branch(billing), await _per_branch(payment)
    all_b = sorted(set(cb) | set(bb) | set(pb))
    branches = [
        {"branch": b, "customers": int(cb.get(b, 0)), "billing": int(bb.get(b, 0)), "payments": int(pb.get(b, 0))}
        for b in all_b
    ]
    recent = []
    async for r in import_batches.find({}, {"_id": 0}).sort("created_at", -1).limit(8):
        recent.append(r)
    return {
        "customers": cust_total, "billing": bill_total, "payments": pay_total, "vip": vip,
        "branches": branches, "recent_imports": recent,
    }


@api.get("/customers/lookup")
async def lookup(rfid: str = Query(...), branch: Optional[str] = None):
    norm = normalize_rfid(rfid)
    flt = {"rfid_norm": norm}
    if branch:
        flt["branch"] = branch
    matches = []
    async for m in customers.find(flt, {"_id": 0}).sort([("branch", 1)]):
        matches.append(m)
    return {
        "found": len(matches) > 0, "scanned": rfid, "normalized": norm,
        "count": len(matches), "customer": matches[0] if matches else None, "matches": matches,
    }


def _search_filter(term, extra=()):
    rx = {"$regex": re.escape(term.strip()), "$options": "i"}
    ors = [{"name": rx}, {"phone": rx}, {"rfid": rx}]
    for f in extra:
        ors.append({f: rx})
    norm = normalize_rfid(term)
    if norm:
        ors.append({"rfid_norm": {"$regex": re.escape(norm.strip("{}")), "$options": "i"}})
    return ors


def _date_range(field, frm, to):
    """Build a Mongo range filter on a 'YYYY-MM-DD HH:MM:SS' string field."""
    cond = {}
    if frm:
        cond["$gte"] = frm.strip()
    if to:
        t = to.strip()
        if len(t) == 10:  # date only -> include the whole day
            t = t + " 23:59:59"
        cond["$lte"] = t
    return {field: cond} if cond else None


@api.get("/customers")
async def list_customers(
    branch: Optional[str] = None, q: Optional[str] = None, vip_only: bool = False,
    created_from: Optional[str] = None, created_to: Optional[str] = None,
    active_from: Optional[str] = None, active_to: Optional[str] = None,
    page: int = 1, page_size: int = 25, sort_by: str = "name", sort_dir: str = "asc",
):
    flt = {}
    if branch:
        flt["branch"] = branch
    if vip_only:
        flt["is_vip"] = 1
    if q:
        flt["$or"] = _search_filter(q)
    ands = []
    cr = _date_range("created_at", created_from, created_to)
    ac = _date_range("last_activity", active_from, active_to)
    if cr:
        ands.append(cr)
    if ac:
        ands.append(ac)
    if ands:
        flt["$and"] = ands
    total = await customers.count_documents(flt)
    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    direction = -1 if sort_dir == "desc" else 1
    items = []
    async for c in customers.find(flt, {"_id": 0}).sort([(sort_by, direction)]).skip((page - 1) * page_size).limit(page_size):
        items.append(c)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def _txn_list(coll, branch, q, page, page_size, extra, created_from=None, created_to=None, sort_dir="desc"):
    flt = {}
    if branch:
        flt["branch"] = branch
    if q:
        flt["$or"] = _search_filter(q, extra)
    cr = _date_range("created_at", created_from, created_to)
    if cr:
        flt.update(cr)
    total = await coll.count_documents(flt)
    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    direction = 1 if sort_dir == "asc" else -1
    items = []
    async for x in coll.find(flt, {"_id": 0}).sort("created_at", direction).skip((page - 1) * page_size).limit(page_size):
        items.append(x)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@api.get("/billing")
async def list_billing(branch: Optional[str] = None, q: Optional[str] = None, page: int = 1, page_size: int = 25,
                       created_from: Optional[str] = None, created_to: Optional[str] = None, sort_dir: str = "desc"):
    return await _txn_list(billing, branch, q, page, page_size, ["log_game"], created_from, created_to, sort_dir)


@api.get("/payments")
async def list_payments(branch: Optional[str] = None, q: Optional[str] = None, page: int = 1, page_size: int = 25,
                        created_from: Optional[str] = None, created_to: Optional[str] = None, sort_dir: str = "desc"):
    return await _txn_list(payment, branch, q, page, page_size, ["payment_mode"], created_from, created_to, sort_dir)


@api.delete("/purge")
async def purge(branch: Optional[str] = None):
    """Delete all data for a branch, or everything when no branch is given."""
    flt = {"branch": branch} if branch else {}
    res = {
        "customers": (await customers.delete_many(flt)).deleted_count,
        "billing": (await billing.delete_many(flt)).deleted_count,
        "payments": (await payment.delete_many(flt)).deleted_count,
        "import_batches": (await import_batches.delete_many(flt)).deleted_count,
    }
    res["branch"] = branch or "ALL"
    return res


def _link_filter(c):
    flt = {"branch": c.get("branch")}
    if c.get("guid"):
        flt["$or"] = [{"guid": c["guid"]}, {"customer_id": c.get("original_id")}]
    else:
        flt["customer_id"] = c.get("original_id")
    return flt


async def _sum(coll, flt, field):
    async for r in coll.aggregate([{"$match": flt}, {"$group": {"_id": None, "s": {"$sum": f"${field}"}}}]):
        return int(r.get("s") or 0)
    return 0


@api.get("/customers/{cid}/history")
async def customer_history(cid: str, b_page: int = 1, p_page: int = 1, page_size: int = 50):
    c = clean(await customers.find_one({"id": cid}))
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")
    page_size = min(max(1, page_size), 200)
    bf, pf = _link_filter(c), _link_filter(c)

    b_total = await billing.count_documents(bf)
    p_total = await payment.count_documents(pf)
    b_items, p_items = [], []
    async for x in billing.find(bf, {"_id": 0}).sort("created_at", -1).skip((max(1, b_page) - 1) * page_size).limit(page_size):
        b_items.append(x)
    async for x in payment.find(pf, {"_id": 0}).sort("created_at", -1).skip((max(1, p_page) - 1) * page_size).limit(page_size):
        p_items.append(x)

    return {
        "customer": c,
        "billing": {"items": b_items, "total": b_total, "page": b_page, "page_size": page_size},
        "payments": {"items": p_items, "total": p_total, "page": p_page, "page_size": page_size},
        "summary": {
            "games_played": b_total,
            "total_spent": await _sum(billing, bf, "amount"),
            "total_tokens_earned": await _sum(billing, bf, "token_earn"),
            "recharges": p_total,
            "total_recharged": await _sum(payment, pf, "recharge_amount"),
            "total_collected": await _sum(payment, pf, "collect_amount_from_customer"),
        },
    }


@api.get("/customers/{cid}")
async def get_customer(cid: str):
    c = clean(await customers.find_one({"id": cid}))
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")
    return c


@api.post("/customers")
async def create_customer(body: CustomerUpsert):
    data = body.model_dump(exclude_unset=True)
    branch = (data.pop("branch", None) or "").strip()
    if not branch:
        raise HTTPException(status_code=400, detail="Branch is required for a new customer")
    doc = dict(data)
    doc["branch"] = branch
    doc["id"] = str(uuid.uuid4())
    doc["imported_at"] = datetime.now(timezone.utc).isoformat()
    doc["rfid_norm"] = normalize_rfid(doc.get("rfid"))
    await customers.insert_one(doc)
    return clean(doc)


@api.put("/customers/{cid}")
async def update_customer(cid: str, body: CustomerUpsert):
    data = body.model_dump(exclude_unset=True)
    if "rfid" in data:
        data["rfid_norm"] = normalize_rfid(data.get("rfid"))
    res = await customers.update_one({"id": cid}, {"$set": data})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Customer not found")
    return clean(await customers.find_one({"id": cid}))


@api.delete("/customers/{cid}")
async def delete_customer(cid: str):
    res = await customers.delete_one({"id": cid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Customer not found")
    return {"deleted": cid}


@api.delete("/billing/{bid}")
async def delete_billing(bid: str):
    res = await billing.delete_one({"id": bid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Billing row not found")
    return {"deleted": bid}


@api.delete("/payments/{pid}")
async def delete_payment(pid: str):
    res = await payment.delete_one({"id": pid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Payment row not found")
    return {"deleted": pid}


@api.get("/import-batches")
async def get_import_batches():
    out = []
    async for r in import_batches.find({}, {"_id": 0}).sort("created_at", -1):
        out.append(r)
    return out


app.include_router(api)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
