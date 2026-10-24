# # import re
# # import os
# # import uuid
# # import logging
# # import tempfile
# # from contextlib import asynccontextmanager
# # from datetime import datetime, timezone

# # from fastapi import FastAPI, APIRouter, UploadFile, File, Form, HTTPException, Query
# # from starlette.middleware.cors import CORSMiddleware
# # from pydantic import BaseModel
# # from typing import Optional

# # from database import (
# #     db, customers, billing, payment, import_batches, ensure_indexes,
# # )
# # from sql_parser import (
# #     iter_inserts, detect_table,
# #     CUSTOMER_STRING_COLS, BILLING_STRING_COLS, PAYMENT_STRING_COLS,
# # )

# # logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
# # logger = logging.getLogger(__name__)


# # @asynccontextmanager
# # async def lifespan(_app: FastAPI):
# #     try:
# #         await ensure_indexes()
# #     except Exception as e:  # pragma: no cover - a bad Mongo URL shouldn't block boot
# #         logger.warning("Index creation issue: %s", e)
# #     yield


# # app = FastAPI(title="Legacy POS Customer Lookup", lifespan=lifespan)
# # api = APIRouter(prefix="/api")

# # DATASETS = {
# #     "es_customer": {"coll": customers, "strings": CUSTOMER_STRING_COLS, "name": "customer"},
# #     "es_billing": {"coll": billing, "strings": BILLING_STRING_COLS, "name": "billing"},
# #     "es_payment": {"coll": payment, "strings": PAYMENT_STRING_COLS, "name": "payment"},
# # }

# # # Rows sent to MongoDB per insert_many call.
# # IMPORT_BATCH = 2000

# # # Browser uploads can be unlimited locally by setting MAX_UPLOAD_MB=0. Hosted
# # # deployments should keep a finite value because their proxy/body limits still
# # # apply even when the application accepts the request.
# # MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "40"))
# # MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024 if MAX_UPLOAD_MB > 0 else None


# # def normalize_rfid(value):
# #     if value is None:
# #         return None
# #     s = str(value).strip().upper()
# #     if not s:
# #         return None
# #     m = re.search(r"\{?([0-9A-F]+)\}?", s)
# #     inner = m.group(1) if m else re.sub(r"[^0-9A-Z]", "", s)
# #     return "{" + inner + "}"


# # def clean(doc):
# #     if doc:
# #         doc.pop("_id", None)
# #     return doc


# # def build_doc(row, branch, filename, now_iso):
# #     """Turn one parsed dump row into the document we store."""
# #     doc = dict(row)
# #     # Each branch export restarts ids at 1, so the legacy id can't be our key.
# #     doc["original_id"] = doc.pop("id", None)
# #     doc["branch"] = branch
# #     doc["source_file"] = filename
# #     doc["imported_at"] = now_iso
# #     doc["id"] = str(uuid.uuid4())
# #     if "rfid" in row:
# #         doc["rfid_norm"] = normalize_rfid(row.get("rfid"))
# #     return doc


# # def build_docs(rows, branch, filename, now_iso):
# #     return [build_doc(r, branch, filename, now_iso) for r in rows]


# # # ---------------- Pydantic ----------------
# # class CustomerUpsert(BaseModel):
# #     branch: Optional[str] = None
# #     guid: Optional[str] = None
# #     rfid: Optional[str] = None
# #     name: Optional[str] = None
# #     dob: Optional[str] = None
# #     email: Optional[str] = None
# #     phone: Optional[str] = None
# #     payment_mode: Optional[str] = None
# #     balance_main: Optional[int] = None
# #     balance_bonus: Optional[int] = None
# #     status: Optional[str] = None
# #     last_activity: Optional[str] = None
# #     created_at: Optional[str] = None
# #     is_vip: Optional[int] = None
# #     is_free_time: Optional[str] = None
# #     total_token: Optional[int] = None
# #     user: Optional[str] = None
# #     location: Optional[str] = None
# #     card_sno: Optional[int] = None
# #     center_code: Optional[str] = None
# #     updated_by: Optional[str] = None


# # # ---------------- Routes ----------------
# # @api.get("/")
# # async def root():
# #     return {"message": "Legacy POS Customer Lookup API"}


# # @api.post("/import")
# # async def import_dump(
# #     branch: str = Form(...),
# #     file: UploadFile = File(...),
# #     dataset: Optional[str] = Form(None),
# # ):
# #     branch = (branch or "").strip()
# #     if not branch:
# #         raise HTTPException(status_code=400, detail="Branch label is required")

# #     # Spool the upload to disk rather than holding the raw bytes and the decoded
# #     # text in memory at the same time.
# #     tmp_path = None
# #     try:
# #         with tempfile.NamedTemporaryFile(delete=False, suffix=".sql") as tmp:
# #             tmp_path = tmp.name
# #             size = 0
# #             while True:
# #                 chunk = await file.read(1 << 20)
# #                 if not chunk:
# #                     break
# #                 size += len(chunk)
# #                 if MAX_UPLOAD_BYTES is not None and size > MAX_UPLOAD_BYTES:
# #                     raise HTTPException(
# #                         status_code=413,
# #                         detail=(
# #                             f"File is larger than the {MAX_UPLOAD_MB} MB upload limit. "
# #                             "Import big dumps with backend/import_sql.py, which writes "
# #                             "straight to MongoDB and has no size limit."
# #                         ),
# #                     )
# #                 tmp.write(chunk)

# #         with open(tmp_path, encoding="utf-8", errors="replace") as fh:
# #             text = fh.read()
# #     finally:
# #         if tmp_path and os.path.exists(tmp_path):
# #             os.unlink(tmp_path)

# #     table = None
# #     if dataset:
# #         table = {"customer": "es_customer", "billing": "es_billing", "payment": "es_payment"}.get(dataset.lower())
# #     if not table:
# #         table = detect_table(text)
# #     if not table or table not in DATASETS:
# #         raise HTTPException(status_code=400, detail="Could not find a supported table (es_customer, es_billing or es_payment) in this file.")

# #     cfg = DATASETS[table]
# #     now_iso = datetime.now(timezone.utc).isoformat()

# #     # Insert in batches as rows are parsed, so peak memory stays flat no matter
# #     # how many rows the dump holds.
# #     imported = 0
# #     batch = []
# #     for row in iter_inserts(text, table, cfg["strings"]):
# #         batch.append(build_doc(row, branch, file.filename, now_iso))
# #         if len(batch) >= IMPORT_BATCH:
# #             await cfg["coll"].insert_many(batch, ordered=False)
# #             imported += len(batch)
# #             batch = []
# #     if batch:
# #         await cfg["coll"].insert_many(batch, ordered=False)
# #         imported += len(batch)

# #     if imported == 0:
# #         raise HTTPException(status_code=400, detail=f"No INSERT rows found for {table} in this file.")

# #     await import_batches.insert_one({
# #         "id": str(uuid.uuid4()), "branch": branch, "dataset": cfg["name"],
# #         "filename": file.filename, "row_count": imported, "created_at": now_iso,
# #     })
# #     return {"imported": imported, "branch": branch, "dataset": cfg["name"], "filename": file.filename}


# # @api.get("/branches")
# # async def list_branches():
# #     pipeline = [{"$group": {"_id": "$branch", "count": {"$sum": 1}}}, {"$sort": {"_id": 1}}]
# #     out = []
# #     async for r in customers.aggregate(pipeline):
# #         out.append({"branch": r["_id"], "count": r["count"]})
# #     return out


# # async def _per_branch(coll):
# #     res = {}
# #     async for r in coll.aggregate([{"$group": {"_id": "$branch", "count": {"$sum": 1}}}]):
# #         res[r["_id"]] = r["count"]
# #     return res


# # @api.get("/dashboard")
# # async def dashboard():
# #     cust_total = await customers.count_documents({})
# #     bill_total = await billing.count_documents({})
# #     pay_total = await payment.count_documents({})
# #     vip = await customers.count_documents({"is_vip": 1})

# #     cb, bb, pb = await _per_branch(customers), await _per_branch(billing), await _per_branch(payment)
# #     all_b = sorted(set(cb) | set(bb) | set(pb))
# #     branches = [
# #         {"branch": b, "customers": int(cb.get(b, 0)), "billing": int(bb.get(b, 0)), "payments": int(pb.get(b, 0))}
# #         for b in all_b
# #     ]
# #     recent = []
# #     async for r in import_batches.find({}, {"_id": 0}).sort("created_at", -1).limit(8):
# #         recent.append(r)
# #     return {
# #         "customers": cust_total, "billing": bill_total, "payments": pay_total, "vip": vip,
# #         "branches": branches, "recent_imports": recent,
# #     }


# # @api.get("/customers/lookup")
# # async def lookup(rfid: str = Query(...), branch: Optional[str] = None):
# #     norm = normalize_rfid(rfid)
# #     flt = {"rfid_norm": norm}
# #     if branch:
# #         flt["branch"] = branch
# #     matches = []
# #     async for m in customers.find(flt, {"_id": 0}).sort([("branch", 1)]):
# #         matches.append(m)
# #     return {
# #         "found": len(matches) > 0, "scanned": rfid, "normalized": norm,
# #         "count": len(matches), "customer": matches[0] if matches else None, "matches": matches,
# #     }


# # def _search_filter(term, extra=()):
# #     rx = {"$regex": re.escape(term.strip()), "$options": "i"}
# #     ors = [{"name": rx}, {"phone": rx}, {"rfid": rx}]
# #     for f in extra:
# #         ors.append({f: rx})
# #     norm = normalize_rfid(term)
# #     if norm:
# #         ors.append({"rfid_norm": {"$regex": re.escape(norm.strip("{}")), "$options": "i"}})
# #     return ors


# # def _date_range(field, frm, to):
# #     """Build a Mongo range filter on a 'YYYY-MM-DD HH:MM:SS' string field."""
# #     cond = {}
# #     if frm:
# #         cond["$gte"] = frm.strip()
# #     if to:
# #         t = to.strip()
# #         if len(t) == 10:  # date only -> include the whole day
# #             t = t + " 23:59:59"
# #         cond["$lte"] = t
# #     return {field: cond} if cond else None


# # @api.get("/customers")
# # async def list_customers(
# #     branch: Optional[str] = None, q: Optional[str] = None, vip_only: bool = False,
# #     created_from: Optional[str] = None, created_to: Optional[str] = None,
# #     active_from: Optional[str] = None, active_to: Optional[str] = None,
# #     page: int = 1, page_size: int = 25, sort_by: str = "name", sort_dir: str = "asc",
# # ):
# #     flt = {}
# #     if branch:
# #         flt["branch"] = branch
# #     if vip_only:
# #         flt["is_vip"] = 1
# #     if q:
# #         flt["$or"] = _search_filter(q)
# #     ands = []
# #     cr = _date_range("created_at", created_from, created_to)
# #     ac = _date_range("last_activity", active_from, active_to)
# #     if cr:
# #         ands.append(cr)
# #     if ac:
# #         ands.append(ac)
# #     if ands:
# #         flt["$and"] = ands
# #     total = await customers.count_documents(flt)
# #     page = max(1, page)
# #     page_size = min(max(1, page_size), 200)
# #     direction = -1 if sort_dir == "desc" else 1
# #     items = []
# #     async for c in customers.find(flt, {"_id": 0}).sort([(sort_by, direction)]).skip((page - 1) * page_size).limit(page_size):
# #         items.append(c)
# #     return {"items": items, "total": total, "page": page, "page_size": page_size}


# # async def _txn_list(coll, branch, q, page, page_size, extra, created_from=None, created_to=None, sort_dir="desc"):
# #     flt = {}
# #     if branch:
# #         flt["branch"] = branch
# #     if q:
# #         flt["$or"] = _search_filter(q, extra)
# #     cr = _date_range("created_at", created_from, created_to)
# #     if cr:
# #         flt.update(cr)
# #     total = await coll.count_documents(flt)
# #     page = max(1, page)
# #     page_size = min(max(1, page_size), 200)
# #     direction = 1 if sort_dir == "asc" else -1
# #     items = []
# #     async for x in coll.find(flt, {"_id": 0}).sort("created_at", direction).skip((page - 1) * page_size).limit(page_size):
# #         items.append(x)
# #     return {"items": items, "total": total, "page": page, "page_size": page_size}


# # @api.get("/billing")
# # async def list_billing(branch: Optional[str] = None, q: Optional[str] = None, page: int = 1, page_size: int = 25,
# #                        created_from: Optional[str] = None, created_to: Optional[str] = None, sort_dir: str = "desc"):
# #     return await _txn_list(billing, branch, q, page, page_size, ["log_game"], created_from, created_to, sort_dir)


# # @api.get("/payments")
# # async def list_payments(branch: Optional[str] = None, q: Optional[str] = None, page: int = 1, page_size: int = 25,
# #                         created_from: Optional[str] = None, created_to: Optional[str] = None, sort_dir: str = "desc"):
# #     return await _txn_list(payment, branch, q, page, page_size, ["payment_mode"], created_from, created_to, sort_dir)


# # @api.delete("/purge")
# # async def purge(branch: Optional[str] = None):
# #     """Delete all data for a branch, or everything when no branch is given."""
# #     flt = {"branch": branch} if branch else {}
# #     res = {
# #         "customers": (await customers.delete_many(flt)).deleted_count,
# #         "billing": (await billing.delete_many(flt)).deleted_count,
# #         "payments": (await payment.delete_many(flt)).deleted_count,
# #         "import_batches": (await import_batches.delete_many(flt)).deleted_count,
# #     }
# #     res["branch"] = branch or "ALL"
# #     return res


# # def _link_filter(c):
# #     flt = {"branch": c.get("branch")}
# #     if c.get("guid"):
# #         flt["$or"] = [{"guid": c["guid"]}, {"customer_id": c.get("original_id")}]
# #     else:
# #         flt["customer_id"] = c.get("original_id")
# #     return flt


# # async def _sum(coll, flt, field):
# #     async for r in coll.aggregate([{"$match": flt}, {"$group": {"_id": None, "s": {"$sum": f"${field}"}}}]):
# #         return int(r.get("s") or 0)
# #     return 0


# # @api.get("/customers/{cid}/history")
# # async def customer_history(cid: str, b_page: int = 1, p_page: int = 1, page_size: int = 50):
# #     c = clean(await customers.find_one({"id": cid}))
# #     if not c:
# #         raise HTTPException(status_code=404, detail="Customer not found")
# #     page_size = min(max(1, page_size), 200)
# #     bf, pf = _link_filter(c), _link_filter(c)

# #     b_total = await billing.count_documents(bf)
# #     p_total = await payment.count_documents(pf)
# #     b_items, p_items = [], []
# #     async for x in billing.find(bf, {"_id": 0}).sort("created_at", -1).skip((max(1, b_page) - 1) * page_size).limit(page_size):
# #         b_items.append(x)
# #     async for x in payment.find(pf, {"_id": 0}).sort("created_at", -1).skip((max(1, p_page) - 1) * page_size).limit(page_size):
# #         p_items.append(x)

# #     return {
# #         "customer": c,
# #         "billing": {"items": b_items, "total": b_total, "page": b_page, "page_size": page_size},
# #         "payments": {"items": p_items, "total": p_total, "page": p_page, "page_size": page_size},
# #         "summary": {
# #             "games_played": b_total,
# #             "total_spent": await _sum(billing, bf, "amount"),
# #             "total_tokens_earned": await _sum(billing, bf, "token_earn"),
# #             "recharges": p_total,
# #             "total_recharged": await _sum(payment, pf, "recharge_amount"),
# #             "total_collected": await _sum(payment, pf, "collect_amount_from_customer"),
# #         },
# #     }


# # @api.get("/customers/{cid}")
# # async def get_customer(cid: str):
# #     c = clean(await customers.find_one({"id": cid}))
# #     if not c:
# #         raise HTTPException(status_code=404, detail="Customer not found")
# #     return c


# # @api.post("/customers")
# # async def create_customer(body: CustomerUpsert):
# #     data = body.model_dump(exclude_unset=True)
# #     branch = (data.pop("branch", None) or "").strip()
# #     if not branch:
# #         raise HTTPException(status_code=400, detail="Branch is required for a new customer")
# #     doc = dict(data)
# #     doc["branch"] = branch
# #     doc["id"] = str(uuid.uuid4())
# #     doc["imported_at"] = datetime.now(timezone.utc).isoformat()
# #     doc["rfid_norm"] = normalize_rfid(doc.get("rfid"))
# #     await customers.insert_one(doc)
# #     return clean(doc)


# # @api.put("/customers/{cid}")
# # async def update_customer(cid: str, body: CustomerUpsert):
# #     data = body.model_dump(exclude_unset=True)
# #     if "rfid" in data:
# #         data["rfid_norm"] = normalize_rfid(data.get("rfid"))
# #     res = await customers.update_one({"id": cid}, {"$set": data})
# #     if res.matched_count == 0:
# #         raise HTTPException(status_code=404, detail="Customer not found")
# #     return clean(await customers.find_one({"id": cid}))


# # @api.delete("/customers/{cid}")
# # async def delete_customer(cid: str):
# #     res = await customers.delete_one({"id": cid})
# #     if res.deleted_count == 0:
# #         raise HTTPException(status_code=404, detail="Customer not found")
# #     return {"deleted": cid}


# # @api.delete("/billing/{bid}")
# # async def delete_billing(bid: str):
# #     res = await billing.delete_one({"id": bid})
# #     if res.deleted_count == 0:
# #         raise HTTPException(status_code=404, detail="Billing row not found")
# #     return {"deleted": bid}


# # @api.delete("/payments/{pid}")
# # async def delete_payment(pid: str):
# #     res = await payment.delete_one({"id": pid})
# #     if res.deleted_count == 0:
# #         raise HTTPException(status_code=404, detail="Payment row not found")
# #     return {"deleted": pid}


# # @api.get("/import-batches")
# # async def get_import_batches():
# #     out = []
# #     async for r in import_batches.find({}, {"_id": 0}).sort("created_at", -1):
# #         out.append(r)
# #     return out


# # app.include_router(api)
# # app.add_middleware(
# #     CORSMiddleware,
# #     allow_credentials=True,
# #     allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
# #     allow_methods=["*"],
# #     allow_headers=["*"],
# # )

# import re
# import os
# import uuid
# import logging
# import tempfile
# from contextlib import asynccontextmanager
# from datetime import datetime, timezone

# from fastapi import FastAPI, APIRouter, UploadFile, File, Form, HTTPException, Query, Depends, Request
# from starlette.middleware.cors import CORSMiddleware
# from pydantic import BaseModel
# from typing import Optional

# from database import (
#     db, customers, billing, payment, import_batches, ensure_indexes,
#     app_users, audit_log, migration_codes,
# )
# import auth
# import migration_code
# from auth import require_owner, require_staff, audit, enforce_rate, client_ip
# from sql_parser import (
#     iter_inserts, detect_table,
#     CUSTOMER_STRING_COLS, BILLING_STRING_COLS, PAYMENT_STRING_COLS,
# )

# logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
# logger = logging.getLogger(__name__)


# @asynccontextmanager
# async def lifespan(_app: FastAPI):
#     # NEW (security) - refuse to start without a session secret, so the app can
#     # never silently run "open".
#     auth.assert_auth_configured()
#     try:
#         await ensure_indexes()
#     except Exception as e:  # pragma: no cover - a bad Mongo URL shouldn't block boot
#         logger.warning("Index creation issue: %s", e)
#     try:
#         await auth.bootstrap_owner()
#     except Exception as e:  # pragma: no cover
#         logger.warning("Owner bootstrap issue: %s", e)
#     if not migration_code.signing_configured():
#         logger.warning("MIGRATION_SIGNING_SECRET not set - migration codes cannot be issued")
#     yield


# # NEW (security) - the interactive API docs list every route to anyone who can
# # reach the site, so they are off unless explicitly enabled for local work.
# _DOCS_ON = os.environ.get("ENABLE_DOCS", "0") == "1"
# app = FastAPI(
#     title="Legacy POS Customer Lookup", lifespan=lifespan,
#     docs_url="/docs" if _DOCS_ON else None,
#     redoc_url=None,
#     openapi_url="/openapi.json" if _DOCS_ON else None,
# )

# # NEW (security) - three routers. Default is "locked": a route is open only
# # because it is on `public`.
# public = APIRouter(prefix="/api")                                              # no login
# staff = APIRouter(prefix="/api", dependencies=[Depends(require_staff)])        # owner OR cashier
# owner = APIRouter(prefix="/api", dependencies=[Depends(require_owner)])        # owner only

# DATASETS = {
#     "es_customer": {"coll": customers, "strings": CUSTOMER_STRING_COLS, "name": "customer"},
#     "es_billing": {"coll": billing, "strings": BILLING_STRING_COLS, "name": "billing"},
#     "es_payment": {"coll": payment, "strings": PAYMENT_STRING_COLS, "name": "payment"},
# }

# # Rows sent to MongoDB per insert_many call.
# IMPORT_BATCH = 2000

# # Browser uploads can be unlimited locally by setting MAX_UPLOAD_MB=0. Hosted
# # deployments should keep a finite value because their proxy/body limits still
# # apply even when the application accepts the request.
# MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "40"))
# MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024 if MAX_UPLOAD_MB > 0 else None


# def normalize_rfid(value):
#     if value is None:
#         return None
#     s = str(value).strip().upper()
#     if not s:
#         return None
#     m = re.search(r"\{?([0-9A-F]+)\}?", s)
#     inner = m.group(1) if m else re.sub(r"[^0-9A-Z]", "", s)
#     return "{" + inner + "}"


# def clean(doc):
#     if doc:
#         doc.pop("_id", None)
#     return doc


# def build_doc(row, branch, filename, now_iso):
#     """Turn one parsed dump row into the document we store."""
#     doc = dict(row)
#     # Each branch export restarts ids at 1, so the legacy id can't be our key.
#     doc["original_id"] = doc.pop("id", None)
#     doc["branch"] = branch
#     doc["source_file"] = filename
#     doc["imported_at"] = now_iso
#     doc["id"] = str(uuid.uuid4())
#     if "rfid" in row:
#         doc["rfid_norm"] = normalize_rfid(row.get("rfid"))
#     return doc


# def build_docs(rows, branch, filename, now_iso):
#     return [build_doc(r, branch, filename, now_iso) for r in rows]


# # ---------------- Pydantic ----------------
# class CustomerUpsert(BaseModel):
#     branch: Optional[str] = None
#     guid: Optional[str] = None
#     rfid: Optional[str] = None
#     name: Optional[str] = None
#     dob: Optional[str] = None
#     email: Optional[str] = None
#     phone: Optional[str] = None
#     payment_mode: Optional[str] = None
#     balance_main: Optional[int] = None
#     balance_bonus: Optional[int] = None
#     status: Optional[str] = None
#     last_activity: Optional[str] = None
#     created_at: Optional[str] = None
#     is_vip: Optional[int] = None
#     is_free_time: Optional[str] = None
#     total_token: Optional[int] = None
#     user: Optional[str] = None
#     location: Optional[str] = None
#     card_sno: Optional[int] = None
#     center_code: Optional[str] = None
#     updated_by: Optional[str] = None


# # ---------------- Routes ----------------
# @public.get("/")
# async def root():
#     return {"message": "Legacy POS Customer Lookup API"}


# @public.get("/health")
# async def health():
#     # Deliberately says nothing about data or versions.
#     return {"status": "ok"}


# @owner.post("/import")
# async def import_dump(
#     request: Request,
#     user: dict = Depends(require_owner),
#     branch: str = Form(...),
#     file: UploadFile = File(...),
#     dataset: Optional[str] = Form(None),
# ):
#     branch = (branch or "").strip()
#     if not branch:
#         raise HTTPException(status_code=400, detail="Branch label is required")

#     # Spool the upload to disk rather than holding the raw bytes and the decoded
#     # text in memory at the same time.
#     tmp_path = None
#     try:
#         with tempfile.NamedTemporaryFile(delete=False, suffix=".sql") as tmp:
#             tmp_path = tmp.name
#             size = 0
#             while True:
#                 chunk = await file.read(1 << 20)
#                 if not chunk:
#                     break
#                 size += len(chunk)
#                 if MAX_UPLOAD_BYTES is not None and size > MAX_UPLOAD_BYTES:
#                     raise HTTPException(
#                         status_code=413,
#                         detail=(
#                             f"File is larger than the {MAX_UPLOAD_MB} MB upload limit. "
#                             "Import big dumps with backend/import_sql.py, which writes "
#                             "straight to MongoDB and has no size limit."
#                         ),
#                     )
#                 tmp.write(chunk)

#         with open(tmp_path, encoding="utf-8", errors="replace") as fh:
#             text = fh.read()
#     finally:
#         if tmp_path and os.path.exists(tmp_path):
#             os.unlink(tmp_path)

#     table = None
#     if dataset:
#         table = {"customer": "es_customer", "billing": "es_billing", "payment": "es_payment"}.get(dataset.lower())
#     if not table:
#         table = detect_table(text)
#     if not table or table not in DATASETS:
#         raise HTTPException(status_code=400, detail="Could not find a supported table (es_customer, es_billing or es_payment) in this file.")

#     cfg = DATASETS[table]
#     now_iso = datetime.now(timezone.utc).isoformat()

#     # Insert in batches as rows are parsed, so peak memory stays flat no matter
#     # how many rows the dump holds.
#     imported = 0
#     batch = []
#     for row in iter_inserts(text, table, cfg["strings"]):
#         batch.append(build_doc(row, branch, file.filename, now_iso))
#         if len(batch) >= IMPORT_BATCH:
#             await cfg["coll"].insert_many(batch, ordered=False)
#             imported += len(batch)
#             batch = []
#     if batch:
#         await cfg["coll"].insert_many(batch, ordered=False)
#         imported += len(batch)

#     if imported == 0:
#         raise HTTPException(status_code=400, detail=f"No INSERT rows found for {table} in this file.")

#     await import_batches.insert_one({
#         "id": str(uuid.uuid4()), "branch": branch, "dataset": cfg["name"],
#         "filename": file.filename, "row_count": imported, "created_at": now_iso,
#     })
#     await audit("import", request, user, branch=branch, dataset=cfg["name"], rows=imported)
#     return {"imported": imported, "branch": branch, "dataset": cfg["name"], "filename": file.filename}


# @owner.get("/branches")
# async def list_branches():
#     pipeline = [{"$group": {"_id": "$branch", "count": {"$sum": 1}}}, {"$sort": {"_id": 1}}]
#     out = []
#     async for r in customers.aggregate(pipeline):
#         out.append({"branch": r["_id"], "count": r["count"]})
#     return out


# async def _per_branch(coll):
#     res = {}
#     async for r in coll.aggregate([{"$group": {"_id": "$branch", "count": {"$sum": 1}}}]):
#         res[r["_id"]] = r["count"]
#     return res


# @owner.get("/dashboard")
# async def dashboard():
#     cust_total = await customers.count_documents({})
#     bill_total = await billing.count_documents({})
#     pay_total = await payment.count_documents({})
#     vip = await customers.count_documents({"is_vip": 1})

#     cb, bb, pb = await _per_branch(customers), await _per_branch(billing), await _per_branch(payment)
#     all_b = sorted(set(cb) | set(bb) | set(pb))
#     branches = [
#         {"branch": b, "customers": int(cb.get(b, 0)), "billing": int(bb.get(b, 0)), "payments": int(pb.get(b, 0))}
#         for b in all_b
#     ]
#     recent = []
#     async for r in import_batches.find({}, {"_id": 0}).sort("created_at", -1).limit(8):
#         recent.append(r)
#     return {
#         "customers": cust_total, "billing": bill_total, "payments": pay_total, "vip": vip,
#         "branches": branches, "recent_imports": recent,
#     }


# @staff.get("/customers/lookup")
# async def lookup(request: Request, rfid: str = Query(...), branch: Optional[str] = None,
#                  user: dict = Depends(require_staff)):
#     # NEW (security) - rate limited, and a cashier only ever gets a masked,
#     # minimal answer (name / masked phone / last activity). The owner gets the
#     # full record exactly like before.
#     enforce_rate(f"lookup:{user['id']}", 60, 60)
#     norm = normalize_rfid(rfid)
#     flt = {"rfid_norm": norm}
#     if branch:
#         flt["branch"] = branch
#     matches = []
#     async for m in customers.find(flt, {"_id": 0}).sort([("branch", 1)]):
#         matches.append(m)
#     if not matches:
#         await _note_failed_lookup(request, user, norm)
#     if user.get("role") == "owner":
#         return {
#             "found": len(matches) > 0, "scanned": rfid, "normalized": norm,
#             "count": len(matches), "customer": matches[0] if matches else None, "matches": matches,
#         }
#     first = matches[0] if matches else None
#     return {
#         "found": first is not None, "count": len(matches),
#         "customer": {
#             "name": first.get("name"),
#             "phone_masked": migration_code.mask_phone(first.get("phone")),
#             "last_activity": first.get("last_activity"),
#         } if first else None,
#     }


# async def _note_failed_lookup(request: Request, user: dict, norm: Optional[str]):
#     """Repeated misses look like someone guessing card ids - log and slow down."""
#     key = f"notfound:{user['id']}"
#     ok = auth.limiter.hit(key, 10, 600)
#     await audit("lookup_not_found", request, user, rfid_hint=(norm or "")[:20])
#     if not ok:
#         await audit("lookup_blocked_many_misses", request, user)
#         raise HTTPException(status_code=429, detail="Too many cards not found. Wait a few minutes or ask the owner.")


# def _search_filter(term, extra=()):
#     rx = {"$regex": re.escape(term.strip()), "$options": "i"}
#     ors = [{"name": rx}, {"phone": rx}, {"rfid": rx}]
#     for f in extra:
#         ors.append({f: rx})
#     norm = normalize_rfid(term)
#     if norm:
#         ors.append({"rfid_norm": {"$regex": re.escape(norm.strip("{}")), "$options": "i"}})
#     return ors


# def _date_range(field, frm, to):
#     """Build a Mongo range filter on a 'YYYY-MM-DD HH:MM:SS' string field."""
#     cond = {}
#     if frm:
#         cond["$gte"] = frm.strip()
#     if to:
#         t = to.strip()
#         if len(t) == 10:  # date only -> include the whole day
#             t = t + " 23:59:59"
#         cond["$lte"] = t
#     return {field: cond} if cond else None


# @owner.get("/customers")
# async def list_customers(
#     branch: Optional[str] = None, q: Optional[str] = None, vip_only: bool = False,
#     created_from: Optional[str] = None, created_to: Optional[str] = None,
#     active_from: Optional[str] = None, active_to: Optional[str] = None,
#     page: int = 1, page_size: int = 25, sort_by: str = "name", sort_dir: str = "asc",
# ):
#     flt = {}
#     if branch:
#         flt["branch"] = branch
#     if vip_only:
#         flt["is_vip"] = 1
#     if q:
#         flt["$or"] = _search_filter(q)
#     ands = []
#     cr = _date_range("created_at", created_from, created_to)
#     ac = _date_range("last_activity", active_from, active_to)
#     if cr:
#         ands.append(cr)
#     if ac:
#         ands.append(ac)
#     if ands:
#         flt["$and"] = ands
#     total = await customers.count_documents(flt)
#     page = max(1, page)
#     page_size = min(max(1, page_size), 200)
#     direction = -1 if sort_dir == "desc" else 1
#     items = []
#     async for c in customers.find(flt, {"_id": 0}).sort([(sort_by, direction)]).skip((page - 1) * page_size).limit(page_size):
#         items.append(c)
#     return {"items": items, "total": total, "page": page, "page_size": page_size}


# async def _txn_list(coll, branch, q, page, page_size, extra, created_from=None, created_to=None, sort_dir="desc"):
#     flt = {}
#     if branch:
#         flt["branch"] = branch
#     if q:
#         flt["$or"] = _search_filter(q, extra)
#     cr = _date_range("created_at", created_from, created_to)
#     if cr:
#         flt.update(cr)
#     total = await coll.count_documents(flt)
#     page = max(1, page)
#     page_size = min(max(1, page_size), 200)
#     direction = 1 if sort_dir == "asc" else -1
#     items = []
#     async for x in coll.find(flt, {"_id": 0}).sort("created_at", direction).skip((page - 1) * page_size).limit(page_size):
#         items.append(x)
#     return {"items": items, "total": total, "page": page, "page_size": page_size}


# @owner.get("/billing")
# async def list_billing(branch: Optional[str] = None, q: Optional[str] = None, page: int = 1, page_size: int = 25,
#                        created_from: Optional[str] = None, created_to: Optional[str] = None, sort_dir: str = "desc"):
#     return await _txn_list(billing, branch, q, page, page_size, ["log_game"], created_from, created_to, sort_dir)


# @owner.get("/payments")
# async def list_payments(branch: Optional[str] = None, q: Optional[str] = None, page: int = 1, page_size: int = 25,
#                         created_from: Optional[str] = None, created_to: Optional[str] = None, sort_dir: str = "desc"):
#     return await _txn_list(payment, branch, q, page, page_size, ["payment_mode"], created_from, created_to, sort_dir)


# @owner.delete("/purge")
# async def purge(request: Request, branch: Optional[str] = None, user: dict = Depends(require_owner)):
#     """Delete all data for a branch, or everything when no branch is given."""
#     flt = {"branch": branch} if branch else {}
#     res = {
#         "customers": (await customers.delete_many(flt)).deleted_count,
#         "billing": (await billing.delete_many(flt)).deleted_count,
#         "payments": (await payment.delete_many(flt)).deleted_count,
#         "import_batches": (await import_batches.delete_many(flt)).deleted_count,
#     }
#     res["branch"] = branch or "ALL"
#     await audit("purge", request, user, branch=res["branch"], deleted=res)
#     return res


# def _link_filter(c):
#     flt = {"branch": c.get("branch")}
#     if c.get("guid"):
#         flt["$or"] = [{"guid": c["guid"]}, {"customer_id": c.get("original_id")}]
#     else:
#         flt["customer_id"] = c.get("original_id")
#     return flt


# async def _sum(coll, flt, field):
#     async for r in coll.aggregate([{"$match": flt}, {"$group": {"_id": None, "s": {"$sum": f"${field}"}}}]):
#         return int(r.get("s") or 0)
#     return 0


# @owner.get("/customers/{cid}/history")
# async def customer_history(cid: str, b_page: int = 1, p_page: int = 1, page_size: int = 50):
#     c = clean(await customers.find_one({"id": cid}))
#     if not c:
#         raise HTTPException(status_code=404, detail="Customer not found")
#     page_size = min(max(1, page_size), 200)
#     bf, pf = _link_filter(c), _link_filter(c)

#     b_total = await billing.count_documents(bf)
#     p_total = await payment.count_documents(pf)
#     b_items, p_items = [], []
#     async for x in billing.find(bf, {"_id": 0}).sort("created_at", -1).skip((max(1, b_page) - 1) * page_size).limit(page_size):
#         b_items.append(x)
#     async for x in payment.find(pf, {"_id": 0}).sort("created_at", -1).skip((max(1, p_page) - 1) * page_size).limit(page_size):
#         p_items.append(x)

#     return {
#         "customer": c,
#         "billing": {"items": b_items, "total": b_total, "page": b_page, "page_size": page_size},
#         "payments": {"items": p_items, "total": p_total, "page": p_page, "page_size": page_size},
#         "summary": {
#             "games_played": b_total,
#             "total_spent": await _sum(billing, bf, "amount"),
#             "total_tokens_earned": await _sum(billing, bf, "token_earn"),
#             "recharges": p_total,
#             "total_recharged": await _sum(payment, pf, "recharge_amount"),
#             "total_collected": await _sum(payment, pf, "collect_amount_from_customer"),
#         },
#     }


# @owner.get("/customers/{cid}")
# async def get_customer(cid: str):
#     c = clean(await customers.find_one({"id": cid}))
#     if not c:
#         raise HTTPException(status_code=404, detail="Customer not found")
#     return c


# @owner.post("/customers")
# async def create_customer(body: CustomerUpsert, request: Request, user: dict = Depends(require_owner)):
#     data = body.model_dump(exclude_unset=True)
#     branch = (data.pop("branch", None) or "").strip()
#     if not branch:
#         raise HTTPException(status_code=400, detail="Branch is required for a new customer")
#     doc = dict(data)
#     doc["branch"] = branch
#     doc["id"] = str(uuid.uuid4())
#     doc["imported_at"] = datetime.now(timezone.utc).isoformat()
#     doc["rfid_norm"] = normalize_rfid(doc.get("rfid"))
#     await customers.insert_one(doc)
#     await audit("customer_create", request, user, customer_id=doc["id"])
#     return clean(doc)


# @owner.put("/customers/{cid}")
# async def update_customer(cid: str, body: CustomerUpsert, request: Request, user: dict = Depends(require_owner)):
#     data = body.model_dump(exclude_unset=True)
#     if "rfid" in data:
#         data["rfid_norm"] = normalize_rfid(data.get("rfid"))
#     res = await customers.update_one({"id": cid}, {"$set": data})
#     if res.matched_count == 0:
#         raise HTTPException(status_code=404, detail="Customer not found")
#     await audit("customer_update", request, user, customer_id=cid, fields=sorted(data.keys()))
#     return clean(await customers.find_one({"id": cid}))


# @owner.delete("/customers/{cid}")
# async def delete_customer(cid: str, request: Request, user: dict = Depends(require_owner)):
#     res = await customers.delete_one({"id": cid})
#     if res.deleted_count == 0:
#         raise HTTPException(status_code=404, detail="Customer not found")
#     await audit("customer_delete", request, user, customer_id=cid)
#     return {"deleted": cid}


# @owner.delete("/billing/{bid}")
# async def delete_billing(bid: str, request: Request, user: dict = Depends(require_owner)):
#     res = await billing.delete_one({"id": bid})
#     if res.deleted_count == 0:
#         raise HTTPException(status_code=404, detail="Billing row not found")
#     await audit("billing_delete", request, user, billing_id=bid)
#     return {"deleted": bid}


# @owner.delete("/payments/{pid}")
# async def delete_payment(pid: str, request: Request, user: dict = Depends(require_owner)):
#     res = await payment.delete_one({"id": pid})
#     if res.deleted_count == 0:
#         raise HTTPException(status_code=404, detail="Payment row not found")
#     await audit("payment_delete", request, user, payment_id=pid)
#     return {"deleted": pid}


# @owner.get("/import-batches")
# async def get_import_batches():
#     out = []
#     async for r in import_batches.find({}, {"_id": 0}).sort("created_at", -1):
#         out.append(r)
#     return out


# # ====================================================================
# # NEW (security) - auth, users, audit and the signed migration code
# # ====================================================================
# class LoginBody(BaseModel):
#     username: str
#     password: str


# @public.post("/auth/login")
# async def auth_login(body: LoginBody, request: Request):
#     return await auth.login(request, body.username, body.password)


# @staff.get("/auth/me")
# async def auth_me(user: dict = Depends(require_staff)):
#     return {"user": auth.public_user(user)}


# class ChangePasswordBody(BaseModel):
#     old_password: str
#     new_password: str


# @staff.post("/auth/change-password")
# async def auth_change_password(body: ChangePasswordBody, request: Request, user: dict = Depends(require_staff)):
#     enforce_rate(f"pwchange:{user['id']}", 5, 600)
#     if not auth.verify_password(body.old_password, user["password_hash"]):
#         await audit("password_change_failed", request, user)
#         raise HTTPException(status_code=400, detail="Current password is wrong")
#     auth.validate_new_password(body.new_password)
#     await app_users.update_one(
#         {"id": user["id"]},
#         {"$set": {"password_hash": auth.hash_password(body.new_password)}, "$inc": {"token_version": 1}},
#     )
#     await audit("password_changed", request, user)
#     return {"ok": True, "message": "Password changed. Please log in again."}


# class NewUserBody(BaseModel):
#     username: str
#     password: str
#     role: str = "cashier"
#     name: Optional[str] = None


# class UpdateUserBody(BaseModel):
#     active: Optional[bool] = None
#     password: Optional[str] = None
#     name: Optional[str] = None
#     role: Optional[str] = None


# @owner.get("/users")
# async def list_users():
#     out = []
#     async for u in app_users.find({}).sort("username", 1):
#         out.append(auth.public_user(u))
#     return out


# @owner.post("/users")
# async def create_user(body: NewUserBody, request: Request, user: dict = Depends(require_owner)):
#     doc = await auth.create_user(body.username, body.password, body.role, body.name)
#     await audit("user_create", request, user, target=doc["username"], role=doc["role"])
#     return auth.public_user(doc)


# @owner.patch("/users/{uid}")
# async def update_user(uid: str, body: UpdateUserBody, request: Request, user: dict = Depends(require_owner)):
#     target = await app_users.find_one({"id": uid})
#     if not target:
#         raise HTTPException(status_code=404, detail="User not found")
#     upd, inc = {}, {}
#     losing_owner = (
#         target["role"] == "owner" and target.get("active", True)
#         and (body.active is False or (body.role is not None and body.role != "owner"))
#     )
#     if losing_owner:
#         if target["id"] == user["id"]:
#             raise HTTPException(status_code=400, detail="You cannot deactivate or demote your own owner login")
#         if await app_users.count_documents({"role": "owner", "active": True}) <= 1:
#             raise HTTPException(status_code=400, detail="There must always be at least one active owner")
#     if body.active is not None:
#         upd["active"] = body.active
#         inc["token_version"] = 1
#         if body.active:
#             upd["failed_attempts"] = 0
#             upd["locked_until"] = None
#     if body.role is not None:
#         if body.role not in auth.ROLES:
#             raise HTTPException(status_code=400, detail="Role must be owner or cashier")
#         upd["role"] = body.role
#         inc["token_version"] = 1
#     if body.name is not None:
#         upd["name"] = body.name.strip()[:100] or None
#     if body.password is not None:
#         auth.validate_new_password(body.password)
#         upd["password_hash"] = auth.hash_password(body.password)
#         upd["failed_attempts"] = 0
#         upd["locked_until"] = None
#         inc["token_version"] = 1
#     op = {}
#     if upd:
#         op["$set"] = upd
#     if inc:
#         op["$inc"] = inc
#     if op:
#         await app_users.update_one({"id": uid}, op)
#     await audit("user_update", request, user, target=target["username"], fields=sorted(list(upd.keys())))
#     return auth.public_user(await app_users.find_one({"id": uid}))


# @owner.get("/audit")
# async def list_audit(event: Optional[str] = None, page: int = 1, page_size: int = 50):
#     flt = {"event": event} if event else {}
#     page = max(1, page)
#     page_size = min(max(1, page_size), 200)
#     total = await audit_log.count_documents(flt)
#     items = []
#     async for r in audit_log.find(flt, {"_id": 0}).sort("ts", -1).skip((page - 1) * page_size).limit(page_size):
#         items.append(r)
#     return {"items": items, "total": total, "page": page, "page_size": page_size}


# @owner.get("/migration/codes")
# async def list_issued_codes(page: int = 1, page_size: int = 50):
#     page = max(1, page)
#     page_size = min(max(1, page_size), 200)
#     total = await migration_codes.count_documents({})
#     items = []
#     async for r in migration_codes.find({}, {"_id": 0}).sort("issued_at", -1).skip((page - 1) * page_size).limit(page_size):
#         items.append(r)
#     return {"items": items, "total": total, "page": page, "page_size": page_size}


# # ---- customer statement (cashier shows the customer THEIR OWN details) ----
# # A customer standing at the counter often asks "what is my balance / what did
# # I spend". The cashier can show that - but only for a card that was just
# # TAPPED (no search, no list, no export), and every view is audited and rate
# # limited. The phone stays masked.
# _STATEMENT_DROP = {"_id", "id", "source_file", "imported_at", "rfid_norm"}


# def _strip(doc):
#     return {k: v for k, v in doc.items() if k not in _STATEMENT_DROP}


# @staff.get("/customers/statement")
# async def customer_statement(request: Request, rfid: str = Query(...), inputSource: str = "unknown",
#                              user: dict = Depends(require_staff)):
#     enforce_rate(f"statement:{user['id']}", 30, 60)
#     src = inputSource if inputSource in INPUT_SOURCES else "unknown"
#     if STRICT_SCANNER_INPUT and user.get("role") != "owner" and src == "typed":
#         await audit("statement_rejected_typed", request, user)
#         raise HTTPException(status_code=403, detail="Please TAP the card on the reader. Typing the card id is not allowed.")
#     norm = normalize_rfid(rfid)
#     if not norm:
#         raise HTTPException(status_code=400, detail="Tap the card on the reader")
#     rows = []
#     async for m in customers.find({"rfid_norm": norm}, {"_id": 0}).sort([("last_activity", -1)]).limit(5):
#         rows.append(m)
#     if not rows:
#         await _note_failed_lookup(request, user, norm)
#         return {"found": False, "customers": []}
#     out = []
#     for c in rows:
#         bf = _link_filter(c)
#         bill, pay = [], []
#         async for x in billing.find(bf, {"_id": 0}).sort("created_at", -1).limit(50):
#             bill.append(_strip(x))
#         async for x in payment.find(bf, {"_id": 0}).sort("created_at", -1).limit(50):
#             pay.append(_strip(x))
#         out.append({
#             "name": c.get("name"),
#             "phone_masked": migration_code.mask_phone(c.get("phone")),
#             "branch": c.get("branch"), "status": c.get("status"), "is_vip": c.get("is_vip"),
#             "balance_main": c.get("balance_main"), "balance_bonus": c.get("balance_bonus"),
#             "total_token": c.get("total_token"), "created_at": c.get("created_at"),
#             "last_activity": c.get("last_activity"),
#             "billing": bill, "payments": pay,
#         })
#     await audit("statement_viewed", request, user, input_source=src, rows=len(out))
#     return {"found": True, "customers": out}


# # ---- signed migration code -------------------------------------------
# # 8 hex characters between braces = the OLD reader. A 10-character GameOn
# # card id is rejected here, so a new card can never be "migrated".
# OLD_UID_REGEX = re.compile(os.environ.get("OLD_UID_REGEX", r"^\{[0-9A-F]{8}\}$"))
# SOURCE_LABEL = os.environ.get("SOURCE_LABEL", "legacy")
# STRICT_SCANNER_INPUT = os.environ.get("STRICT_SCANNER_INPUT", "0") == "1"
# INPUT_SOURCES = {"serial", "wedge", "typed", "unknown"}


# class CodeRequest(BaseModel):
#     rfid: str
#     inputSource: Optional[str] = "unknown"


# def _value_of(c: dict) -> int:
#     return max(0, int(c.get("balance_main") or 0)) + max(0, int(c.get("balance_bonus") or 0)) + max(0, int(c.get("total_token") or 0))


# def _record_key(c: dict) -> str:
#     # Must stay the same if the dump is re-imported, so we never use the
#     # random per-import id. Branch + the old software's own guid / row id.
#     ident = c.get("guid") or f"id{c.get('original_id')}"
#     return f"{c.get('branch')}|{ident}"[:100]


# @staff.post("/migration/codes")
# async def issue_migration_code(body: CodeRequest, request: Request, user: dict = Depends(require_staff)):
#     enforce_rate(f"codes:{user['id']}", 30, 60)
#     if not migration_code.signing_configured():
#         raise HTTPException(status_code=503, detail="Migration codes are not set up on this server (MIGRATION_SIGNING_SECRET missing). Ask the owner.")

#     input_source = body.inputSource if body.inputSource in INPUT_SOURCES else "unknown"
#     if STRICT_SCANNER_INPUT and user.get("role") != "owner" and input_source == "typed":
#         await audit("code_rejected_typed", request, user)
#         raise HTTPException(status_code=403, detail="Please TAP the old card on the reader. Typing the card id is not allowed.")

#     norm = normalize_rfid(body.rfid)
#     if not norm or not OLD_UID_REGEX.match(norm):
#         await audit("code_rejected_bad_uid", request, user, uid_len=len(norm or ""))
#         raise HTTPException(status_code=400, detail="This is not an OLD card id. Tap the old card on the OLD reader (a new GameOn card cannot be migrated).")

#     rows = []
#     async for m in customers.find({"rfid_norm": norm}, {"_id": 0}):
#         rows.append(m)
#     if not rows:
#         await _note_failed_lookup(request, user, norm)
#         raise HTTPException(status_code=404, detail="No old customer found for this card.")

#     valued = [r for r in rows if _value_of(r) > 0]
#     pool = valued or rows
#     pool.sort(key=lambda r: (r.get("last_activity") or ""), reverse=True)
#     chosen = pool[0]
#     if _value_of(chosen) <= 0:
#         await audit("code_rejected_zero", request, user)
#         raise HTTPException(status_code=422, detail="This old card has no balance to migrate.")

#     flags = {"ambiguous": len(valued) > 1, "typedInput": input_source == "typed"}
#     payload = migration_code.build_payload(
#         source=SOURCE_LABEL, record_key=_record_key(chosen), old_rfid=norm,
#         name=chosen.get("name") or "", phone=chosen.get("phone"),
#         main=int(chosen.get("balance_main") or 0), bonus=int(chosen.get("balance_bonus") or 0),
#         tickets=int(chosen.get("total_token") or 0), last_activity=chosen.get("last_activity"),
#         flags=flags, input_source=input_source, issued_by=user["username"],
#     )
#     code = migration_code.sign_code(payload)
#     await migration_codes.insert_one({
#         "jti": payload["jti"], "record_key": payload["recordKey"], "old_rfid": norm,
#         "issued_by": user["username"], "user_id": user["id"], "role": user["role"],
#         "input_source": input_source, "ambiguous": flags["ambiguous"],
#         "issued_at": datetime.now(timezone.utc).isoformat(),
#         "expires_at": datetime.fromtimestamp(payload["exp"], timezone.utc).isoformat(),
#         "ip": client_ip(request),
#     })
#     await audit("code_issued", request, user, jti=payload["jti"], input_source=input_source, ambiguous=flags["ambiguous"])

#     resp = {
#         "code": code,
#         "expires_at": datetime.fromtimestamp(payload["exp"], timezone.utc).isoformat(),
#         "customer": {
#             "name": payload["name"],
#             "phone_masked": migration_code.mask_phone(chosen.get("phone")),
#             "last_activity": payload["lastActivity"],
#         },
#         "flags": payload["flags"],
#         "rows_on_card": len(rows),
#     }
#     if user.get("role") == "owner":  # cashiers never see the amounts
#         resp["amounts"] = {"main": payload["main"], "bonus": payload["bonus"], "tickets": payload["tickets"]}
#     return resp


# # Order matters: /customers/lookup (staff) must be registered before
# # /customers/{cid} (owner).
# app.include_router(public)
# app.include_router(staff)
# app.include_router(owner)


# # ---- CORS: only the real frontend origin(s), never "*" ----------------
# def _cors_origins():
#     raw = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
#     safe = [o for o in raw if o != "*"]
#     if len(safe) != len(raw):
#         logger.error("CORS_ORIGINS contains '*' - ignored. List the real frontend origin(s).")
#     if not safe:
#         logger.warning("CORS_ORIGINS is empty - browsers on other origins will be blocked.")
#     return safe


# app.add_middleware(
#     CORSMiddleware,
#     allow_credentials=False,  # we use an Authorization header, not cookies
#     allow_origins=_cors_origins(),
#     allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
#     allow_headers=["Authorization", "Content-Type"],
# )


# @app.middleware("http")
# async def security_headers(request, call_next):
#     resp = await call_next(request)
#     resp.headers.setdefault("X-Content-Type-Options", "nosniff")
#     resp.headers.setdefault("X-Frame-Options", "DENY")
#     resp.headers.setdefault("Referrer-Policy", "no-referrer")
#     resp.headers.setdefault("Cache-Control", "no-store")
#     return resp

import re
import os
import uuid
import logging
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, APIRouter, UploadFile, File, Form, HTTPException, Query, Depends, Request
from starlette.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from database import (
    db, customers, billing, payment, import_batches, ensure_indexes,
    app_users, audit_log, migration_codes,
)
import auth
import migration_code
from auth import require_owner, require_staff, require_manager, audit, enforce_rate, client_ip
from sql_parser import (
    iter_inserts, detect_table,
    CUSTOMER_STRING_COLS, BILLING_STRING_COLS, PAYMENT_STRING_COLS,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # NEW (security) - refuse to start without a session secret, so the app can
    # never silently run "open".
    auth.assert_auth_configured()
    try:
        await ensure_indexes()
    except Exception as e:  # pragma: no cover - a bad Mongo URL shouldn't block boot
        logger.warning("Index creation issue: %s", e)
    try:
        await _backfill_phone_norm()
    except Exception as e:  # pragma: no cover
        logger.warning("phone_norm backfill issue: %s", type(e).__name__)
    try:
        await auth.bootstrap_owner()
    except Exception as e:  # pragma: no cover
        logger.warning("Owner bootstrap issue: %s", e)
    if not migration_code.signing_configured():
        logger.warning("MIGRATION_SIGNING_SECRET not set - migration codes cannot be issued")
    yield


async def _backfill_phone_norm():
    """One-time, safe: only ADDS a derived `phone_norm` field to rows that do
    not have it yet. Original data is never changed. Rows with junk phones get
    phone_norm = None (not searchable by phone)."""
    from pymongo import UpdateOne
    ops = []
    async for c in customers.find({"phone_norm": {"$exists": False}}, {"_id": 1, "phone": 1}):
        ops.append(UpdateOne({"_id": c["_id"]}, {"$set": {"phone_norm": migration_code.normalize_phone(c.get("phone"))}}))
        if len(ops) >= 1000:
            await customers.bulk_write(ops, ordered=False)
            ops = []
    if ops:
        await customers.bulk_write(ops, ordered=False)


# NEW (security) - the interactive API docs list every route to anyone who can
# reach the site, so they are off unless explicitly enabled for local work.
_DOCS_ON = os.environ.get("ENABLE_DOCS", "0") == "1"
app = FastAPI(
    title="Legacy POS Customer Lookup", lifespan=lifespan,
    docs_url="/docs" if _DOCS_ON else None,
    redoc_url=None,
    openapi_url="/openapi.json" if _DOCS_ON else None,
)

# NEW (security) - three routers. Default is "locked": a route is open only
# because it is on `public`.
public = APIRouter(prefix="/api")                                              # no login
staff = APIRouter(prefix="/api", dependencies=[Depends(require_staff)])        # owner OR cashier
owner = APIRouter(prefix="/api", dependencies=[Depends(require_owner)])        # owner only

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
    if "phone" in row:
        doc["phone_norm"] = migration_code.normalize_phone(row.get("phone"))
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
@public.get("/")
async def root():
    return {"message": "Legacy POS Customer Lookup API"}


@public.get("/health")
async def health():
    # Deliberately says nothing about data or versions.
    return {"status": "ok"}


@owner.post("/import")
async def import_dump(
    request: Request,
    user: dict = Depends(require_owner),
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
    await audit("import", request, user, branch=branch, dataset=cfg["name"], rows=imported)
    return {"imported": imported, "branch": branch, "dataset": cfg["name"], "filename": file.filename}


@owner.get("/branches")
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


@owner.get("/dashboard")
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


@staff.get("/customers/lookup")
async def lookup(request: Request, rfid: str = Query(...), branch: Optional[str] = None,
                 user: dict = Depends(require_staff)):
    # NEW (security) - rate limited, and a cashier only ever gets a masked,
    # minimal answer (name / masked phone / last activity). The owner gets the
    # full record exactly like before.
    enforce_rate(f"lookup:{user['id']}", 60, 60)
    norm = normalize_rfid(rfid)
    flt = {"rfid_norm": norm}
    if branch:
        flt["branch"] = branch
    matches = []
    async for m in customers.find(flt, {"_id": 0}).sort([("branch", 1)]):
        matches.append(m)
    if not matches:
        await _note_failed_lookup(request, user, norm)
    if user.get("role") == "owner":
        return {
            "found": len(matches) > 0, "scanned": rfid, "normalized": norm,
            "count": len(matches), "customer": matches[0] if matches else None, "matches": matches,
        }
    first = matches[0] if matches else None
    return {
        "found": first is not None, "count": len(matches),
        "customer": {
            "name": first.get("name"),
            "phone_masked": migration_code.mask_phone(first.get("phone")),
            "last_activity": first.get("last_activity"),
        } if first else None,
    }


async def _note_failed_lookup(request: Request, user: dict, norm: Optional[str]):
    """Repeated misses look like someone guessing card ids - log and slow down."""
    key = f"notfound:{user['id']}"
    ok = auth.limiter.hit(key, 10, 600)
    await audit("lookup_not_found", request, user, rfid_hint=(norm or "")[:20])
    if not ok:
        await audit("lookup_blocked_many_misses", request, user)
        raise HTTPException(status_code=429, detail="Too many cards not found. Wait a few minutes or ask the owner.")


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


@owner.get("/customers")
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


@owner.get("/billing")
async def list_billing(branch: Optional[str] = None, q: Optional[str] = None, page: int = 1, page_size: int = 25,
                       created_from: Optional[str] = None, created_to: Optional[str] = None, sort_dir: str = "desc"):
    return await _txn_list(billing, branch, q, page, page_size, ["log_game"], created_from, created_to, sort_dir)


@owner.get("/payments")
async def list_payments(branch: Optional[str] = None, q: Optional[str] = None, page: int = 1, page_size: int = 25,
                        created_from: Optional[str] = None, created_to: Optional[str] = None, sort_dir: str = "desc"):
    return await _txn_list(payment, branch, q, page, page_size, ["payment_mode"], created_from, created_to, sort_dir)


@owner.delete("/purge")
async def purge(request: Request, branch: Optional[str] = None, user: dict = Depends(require_owner)):
    """Delete all data for a branch, or everything when no branch is given."""
    flt = {"branch": branch} if branch else {}
    res = {
        "customers": (await customers.delete_many(flt)).deleted_count,
        "billing": (await billing.delete_many(flt)).deleted_count,
        "payments": (await payment.delete_many(flt)).deleted_count,
        "import_batches": (await import_batches.delete_many(flt)).deleted_count,
    }
    res["branch"] = branch or "ALL"
    await audit("purge", request, user, branch=res["branch"], deleted=res)
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


@owner.get("/customers/{cid}/history")
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


@owner.get("/customers/{cid}")
async def get_customer(cid: str):
    c = clean(await customers.find_one({"id": cid}))
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")
    return c


@owner.post("/customers")
async def create_customer(body: CustomerUpsert, request: Request, user: dict = Depends(require_owner)):
    data = body.model_dump(exclude_unset=True)
    branch = (data.pop("branch", None) or "").strip()
    if not branch:
        raise HTTPException(status_code=400, detail="Branch is required for a new customer")
    doc = dict(data)
    doc["branch"] = branch
    doc["id"] = str(uuid.uuid4())
    doc["imported_at"] = datetime.now(timezone.utc).isoformat()
    doc["rfid_norm"] = normalize_rfid(doc.get("rfid"))
    doc["phone_norm"] = migration_code.normalize_phone(doc.get("phone"))
    await customers.insert_one(doc)
    await audit("customer_create", request, user, customer_id=doc["id"])
    return clean(doc)


@owner.put("/customers/{cid}")
async def update_customer(cid: str, body: CustomerUpsert, request: Request, user: dict = Depends(require_owner)):
    data = body.model_dump(exclude_unset=True)
    if "rfid" in data:
        data["rfid_norm"] = normalize_rfid(data.get("rfid"))
    if "phone" in data:
        data["phone_norm"] = migration_code.normalize_phone(data.get("phone"))
    res = await customers.update_one({"id": cid}, {"$set": data})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Customer not found")
    await audit("customer_update", request, user, customer_id=cid, fields=sorted(data.keys()))
    return clean(await customers.find_one({"id": cid}))


@owner.delete("/customers/{cid}")
async def delete_customer(cid: str, request: Request, user: dict = Depends(require_owner)):
    res = await customers.delete_one({"id": cid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Customer not found")
    await audit("customer_delete", request, user, customer_id=cid)
    return {"deleted": cid}


@owner.delete("/billing/{bid}")
async def delete_billing(bid: str, request: Request, user: dict = Depends(require_owner)):
    res = await billing.delete_one({"id": bid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Billing row not found")
    await audit("billing_delete", request, user, billing_id=bid)
    return {"deleted": bid}


@owner.delete("/payments/{pid}")
async def delete_payment(pid: str, request: Request, user: dict = Depends(require_owner)):
    res = await payment.delete_one({"id": pid})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Payment row not found")
    await audit("payment_delete", request, user, payment_id=pid)
    return {"deleted": pid}


@owner.get("/import-batches")
async def get_import_batches():
    out = []
    async for r in import_batches.find({}, {"_id": 0}).sort("created_at", -1):
        out.append(r)
    return out


# ====================================================================
# NEW (security) - auth, users, audit and the signed migration code
# ====================================================================
class LoginBody(BaseModel):
    username: str
    password: str


@public.post("/auth/login")
async def auth_login(body: LoginBody, request: Request):
    return await auth.login(request, body.username, body.password)


@staff.get("/auth/me")
async def auth_me(user: dict = Depends(require_staff)):
    return {"user": auth.public_user(user)}


class ChangePasswordBody(BaseModel):
    old_password: str
    new_password: str


@staff.post("/auth/change-password")
async def auth_change_password(body: ChangePasswordBody, request: Request, user: dict = Depends(require_staff)):
    enforce_rate(f"pwchange:{user['id']}", 5, 600)
    if not auth.verify_password(body.old_password, user["password_hash"]):
        await audit("password_change_failed", request, user)
        raise HTTPException(status_code=400, detail="Current password is wrong")
    auth.validate_new_password(body.new_password)
    await app_users.update_one(
        {"id": user["id"]},
        {"$set": {"password_hash": auth.hash_password(body.new_password)}, "$inc": {"token_version": 1}},
    )
    await audit("password_changed", request, user)
    return {"ok": True, "message": "Password changed. Please log in again."}


class NewUserBody(BaseModel):
    username: str
    password: str
    role: str = "cashier"
    name: Optional[str] = None


class UpdateUserBody(BaseModel):
    active: Optional[bool] = None
    password: Optional[str] = None
    name: Optional[str] = None
    role: Optional[str] = None


@owner.get("/users")
async def list_users():
    out = []
    async for u in app_users.find({}).sort("username", 1):
        out.append(auth.public_user(u))
    return out


@owner.post("/users")
async def create_user(body: NewUserBody, request: Request, user: dict = Depends(require_owner)):
    doc = await auth.create_user(body.username, body.password, body.role, body.name)
    await audit("user_create", request, user, target=doc["username"], role=doc["role"])
    return auth.public_user(doc)


@owner.patch("/users/{uid}")
async def update_user(uid: str, body: UpdateUserBody, request: Request, user: dict = Depends(require_owner)):
    target = await app_users.find_one({"id": uid})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    upd, inc = {}, {}
    losing_owner = (
        target["role"] == "owner" and target.get("active", True)
        and (body.active is False or (body.role is not None and body.role != "owner"))
    )
    if losing_owner:
        if target["id"] == user["id"]:
            raise HTTPException(status_code=400, detail="You cannot deactivate or demote your own owner login")
        if await app_users.count_documents({"role": "owner", "active": True}) <= 1:
            raise HTTPException(status_code=400, detail="There must always be at least one active owner")
    if body.active is not None:
        upd["active"] = body.active
        inc["token_version"] = 1
        if body.active:
            upd["failed_attempts"] = 0
            upd["locked_until"] = None
    if body.role is not None:
        if body.role not in auth.ROLES:
            raise HTTPException(status_code=400, detail="Role must be owner or cashier")
        upd["role"] = body.role
        inc["token_version"] = 1
    if body.name is not None:
        upd["name"] = body.name.strip()[:100] or None
    if body.password is not None:
        auth.validate_new_password(body.password)
        upd["password_hash"] = auth.hash_password(body.password)
        upd["failed_attempts"] = 0
        upd["locked_until"] = None
        inc["token_version"] = 1
    op = {}
    if upd:
        op["$set"] = upd
    if inc:
        op["$inc"] = inc
    if op:
        await app_users.update_one({"id": uid}, op)
    await audit("user_update", request, user, target=target["username"], fields=sorted(list(upd.keys())))
    return auth.public_user(await app_users.find_one({"id": uid}))


@owner.get("/audit")
async def list_audit(event: Optional[str] = None, page: int = 1, page_size: int = 50):
    flt = {"event": event} if event else {}
    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    total = await audit_log.count_documents(flt)
    items = []
    async for r in audit_log.find(flt, {"_id": 0}).sort("ts", -1).skip((page - 1) * page_size).limit(page_size):
        items.append(r)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@owner.get("/migration/codes")
async def list_issued_codes(page: int = 1, page_size: int = 50):
    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    total = await migration_codes.count_documents({})
    items = []
    async for r in migration_codes.find({}, {"_id": 0}).sort("issued_at", -1).skip((page - 1) * page_size).limit(page_size):
        items.append(r)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ---- customer statement (cashier shows the customer THEIR OWN details) ----
# A customer standing at the counter often asks "what is my balance / what did
# I spend". The cashier can show that - but only for a card that was just
# TAPPED (no search, no list, no export), and every view is audited and rate
# limited. The phone stays masked.
_STATEMENT_DROP = {"_id", "id", "source_file", "imported_at", "rfid_norm"}


def _strip(doc):
    return {k: v for k, v in doc.items() if k not in _STATEMENT_DROP}


@staff.get("/customers/statement")
async def customer_statement(request: Request, rfid: str = Query(...), inputSource: str = "unknown",
                             user: dict = Depends(require_staff)):
    enforce_rate(f"statement:{user['id']}", 30, 60)
    src = inputSource if inputSource in INPUT_SOURCES else "unknown"
    if STRICT_SCANNER_INPUT and user.get("role") != "owner" and src == "typed":
        await audit("statement_rejected_typed", request, user)
        raise HTTPException(status_code=403, detail="Please TAP the card on the reader. Typing the card id is not allowed.")
    norm = normalize_rfid(rfid)
    if not norm:
        raise HTTPException(status_code=400, detail="Tap the card on the reader")
    rows = []
    async for m in customers.find({"rfid_norm": norm}, {"_id": 0}).sort([("last_activity", -1)]).limit(5):
        rows.append(m)
    if not rows:
        await _note_failed_lookup(request, user, norm)
        return {"found": False, "customers": []}
    out = []
    for c in rows:
        bf = _link_filter(c)
        bill, pay = [], []
        async for x in billing.find(bf, {"_id": 0}).sort("created_at", -1).limit(50):
            bill.append(_strip(x))
        async for x in payment.find(bf, {"_id": 0}).sort("created_at", -1).limit(50):
            pay.append(_strip(x))
        out.append({
            "name": c.get("name"),
            "phone_masked": migration_code.mask_phone(c.get("phone")),
            "branch": c.get("branch"), "status": c.get("status"), "is_vip": c.get("is_vip"),
            "balance_main": c.get("balance_main"), "balance_bonus": c.get("balance_bonus"),
            "total_token": c.get("total_token"), "created_at": c.get("created_at"),
            "last_activity": c.get("last_activity"),
            "billing": bill, "payments": pay,
        })
    await audit("statement_viewed", request, user, input_source=src, rows=len(out))
    return {"found": True, "customers": out}


# ---- signed migration code -------------------------------------------
# 8 hex characters between braces = the OLD reader. A 10-character GameOn
# card id is rejected here, so a new card can never be "migrated".
OLD_UID_REGEX = re.compile(os.environ.get("OLD_UID_REGEX", r"^\{[0-9A-F]{8}\}$"))
SOURCE_LABEL = os.environ.get("SOURCE_LABEL", "legacy")
STRICT_SCANNER_INPUT = os.environ.get("STRICT_SCANNER_INPUT", "0") == "1"
INPUT_SOURCES = {"serial", "wedge", "typed", "unknown", "phone_search"}
MANAGER_DAILY_NOCARD_CAP = int(os.environ.get("MANAGER_DAILY_NOCARD_CAP", "10"))
PHONE_SEARCH_MAX_RESULTS = 5


class CodeRequest(BaseModel):
    rfid: str
    inputSource: Optional[str] = "unknown"


def _value_of(c: dict) -> int:
    return max(0, int(c.get("balance_main") or 0)) + max(0, int(c.get("balance_bonus") or 0)) + max(0, int(c.get("total_token") or 0))


def _record_key(c: dict) -> str:
    # Must stay the same if the dump is re-imported, so we never use the
    # random per-import id. Branch + the old software's own guid / row id.
    ident = c.get("guid") or f"id{c.get('original_id')}"
    return f"{c.get('branch')}|{ident}"[:100]


@staff.post("/migration/codes")
async def issue_migration_code(body: CodeRequest, request: Request, user: dict = Depends(require_staff)):
    enforce_rate(f"codes:{user['id']}", 30, 60)
    if not migration_code.signing_configured():
        raise HTTPException(status_code=503, detail="Migration codes are not set up on this server (MIGRATION_SIGNING_SECRET missing). Ask the owner.")

    input_source = body.inputSource if body.inputSource in INPUT_SOURCES else "unknown"
    if STRICT_SCANNER_INPUT and user.get("role") != "owner" and input_source == "typed":
        await audit("code_rejected_typed", request, user)
        raise HTTPException(status_code=403, detail="Please TAP the old card on the reader. Typing the card id is not allowed.")

    norm = normalize_rfid(body.rfid)
    if not norm or not OLD_UID_REGEX.match(norm):
        await audit("code_rejected_bad_uid", request, user, uid_len=len(norm or ""))
        raise HTTPException(status_code=400, detail="This is not an OLD card id. Tap the old card on the OLD reader (a new GameOn card cannot be migrated).")

    rows = []
    async for m in customers.find({"rfid_norm": norm}, {"_id": 0}):
        rows.append(m)
    if not rows:
        await _note_failed_lookup(request, user, norm)
        raise HTTPException(status_code=404, detail="No old customer found for this card.")

    valued = [r for r in rows if _value_of(r) > 0]
    pool = valued or rows
    pool.sort(key=lambda r: (r.get("last_activity") or ""), reverse=True)
    chosen = pool[0]
    if _value_of(chosen) <= 0:
        await audit("code_rejected_zero", request, user)
        raise HTTPException(status_code=422, detail="This old card has no balance to migrate.")

    return await _issue_for_row(request, user, chosen, norm, input_source,
                                {"ambiguous": len(valued) > 1, "typedInput": input_source == "typed"},
                                rows_on_card=len(rows))


async def _issue_for_row(request: Request, user: dict, chosen: dict, norm: str, input_source: str,
                         flags: dict, rows_on_card: int = 1):
    """Build + sign + log one migration code for one legacy record."""
    payload = migration_code.build_payload(
        source=SOURCE_LABEL, record_key=_record_key(chosen), old_rfid=norm,
        name=chosen.get("name") or "", phone=chosen.get("phone"),
        main=int(chosen.get("balance_main") or 0), bonus=int(chosen.get("balance_bonus") or 0),
        tickets=int(chosen.get("total_token") or 0), last_activity=chosen.get("last_activity"),
        flags=flags, input_source=input_source, issued_by=user["username"],
    )
    code = migration_code.sign_code(payload)
    await migration_codes.insert_one({
        "jti": payload["jti"], "record_key": payload["recordKey"], "old_rfid": norm,
        "issued_by": user["username"], "user_id": user["id"], "role": user["role"],
        "input_source": input_source, "ambiguous": bool(flags.get("ambiguous")),
        "no_card": bool(flags.get("noCard")),
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": datetime.fromtimestamp(payload["exp"], timezone.utc).isoformat(),
        "ip": client_ip(request),
    })
    await audit("code_issued", request, user, jti=payload["jti"], input_source=input_source,
                ambiguous=bool(flags.get("ambiguous")), no_card=bool(flags.get("noCard")))

    resp = {
        "code": code,
        "expires_at": datetime.fromtimestamp(payload["exp"], timezone.utc).isoformat(),
        "customer": {
            "name": payload["name"],
            "phone_masked": migration_code.mask_phone(chosen.get("phone")),
            "last_activity": payload["lastActivity"],
        },
        "flags": payload["flags"],
        "rows_on_card": rows_on_card,
    }
    if user.get("role") == "owner":  # cashiers / managers never see the amounts
        resp["amounts"] = {"main": payload["main"], "bonus": payload["bonus"], "tickets": payload["tickets"]}
    return resp


# ---- Store Manager: find by phone (customer did NOT bring the card) ------
# Exact 10-digit number only (no partial / name search), max 5 records, no
# balances in the answer, rate limited, capped per day, fully audited. The
# codes it makes carry `noCard`, which GameOn answers with a mandatory OTP to
# that phone (plus stricter approvals) - the manager alone can never migrate.
class PhoneSearch(BaseModel):
    phone: str


class PhoneIssue(BaseModel):
    phone: str
    recordIds: list[str]


def _rfid_hint(rfid_norm):
    core = str(rfid_norm or "").strip("{}")
    return ("•" * max(0, len(core) - 4)) + core[-4:] if core else "—"


@staff.post("/migration/find-by-phone")
async def find_by_phone(body: PhoneSearch, request: Request, user: dict = Depends(require_manager)):
    enforce_rate(f"phonesearch:{user['id']}", 20, 600, "Too many phone searches. Wait a few minutes.")
    n = migration_code.normalize_phone(body.phone)
    if not n:
        raise HTTPException(status_code=400, detail="Enter the customer's full 10-digit mobile number")
    rows = []
    async for r in customers.find({"phone_norm": n}, {"_id": 0}).sort([("last_activity", -1)]).limit(PHONE_SEARCH_MAX_RESULTS):
        rows.append(r)
    # never log the number itself - only a short hash fingerprint
    fp = migration_code.phone_hash(n)[:10] if migration_code.signing_configured() else "n/a"
    await audit("phone_search", request, user, phone_fp=fp, results=len(rows))
    if not rows:
        key = f"phonemiss:{user['id']}"
        if not auth.limiter.hit(key, 8, 600):
            await audit("phone_search_blocked_many_misses", request, user)
            raise HTTPException(status_code=429, detail="Too many numbers not found. Wait a few minutes.")
    return {
        "found": len(rows) > 0,
        "records": [
            {
                "id": r["id"], "name": r.get("name"), "branch": r.get("branch"),
                "phone_masked": migration_code.mask_phone(r.get("phone")),
                "card_hint": _rfid_hint(r.get("rfid_norm")),
                "last_activity": r.get("last_activity"),
                "has_balance": _value_of(r) > 0,
            }
            for r in rows
        ],
    }


@staff.post("/migration/codes/by-phone")
async def issue_codes_by_phone(body: PhoneIssue, request: Request, user: dict = Depends(require_manager)):
    enforce_rate(f"codes:{user['id']}", 30, 60)
    if not migration_code.signing_configured():
        raise HTTPException(status_code=503, detail="Migration codes are not set up on this server (MIGRATION_SIGNING_SECRET missing). Ask the owner.")
    n = migration_code.normalize_phone(body.phone)
    ids = list(dict.fromkeys(body.recordIds or []))  # de-duplicate, keep order
    if not n:
        raise HTTPException(status_code=400, detail="Enter the customer's full 10-digit mobile number")
    if not ids:
        raise HTTPException(status_code=400, detail="Choose at least one record")
    if len(ids) > PHONE_SEARCH_MAX_RESULTS:
        raise HTTPException(status_code=400, detail=f"Choose at most {PHONE_SEARCH_MAX_RESULTS} records at a time")

    # Daily cap on no-card codes per manager (owner is exempt).
    if user.get("role") != "owner":
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        used = await migration_codes.count_documents({"user_id": user["id"], "no_card": True, "issued_at": {"$gte": since}})
        if used + len(ids) > MANAGER_DAILY_NOCARD_CAP:
            await audit("nocard_daily_cap_hit", request, user, used=used)
            raise HTTPException(status_code=429, detail=f"Daily limit of {MANAGER_DAILY_NOCARD_CAP} no-card migrations reached. Ask the owner.")

    # Every chosen record MUST carry the typed phone - the manager cannot pick
    # an arbitrary record id.
    out, skipped = [], []
    for rid in ids:
        r = await customers.find_one({"id": rid, "phone_norm": n}, {"_id": 0})
        if not r:
            skipped.append({"id": rid, "reason": "This record does not match that phone number"})
            continue
        if _value_of(r) <= 0:
            skipped.append({"id": rid, "reason": "No balance to migrate"})
            continue
        norm = r.get("rfid_norm") or ""
        if not OLD_UID_REGEX.match(norm):
            skipped.append({"id": rid, "reason": "This record has no valid old card id"})
            continue
        out.append(await _issue_for_row(request, user, r, norm, "phone_search",
                                        {"noCard": True, "ambiguous": False, "typedInput": False}))
    if not out:
        raise HTTPException(status_code=422, detail=skipped[0]["reason"] if skipped else "Nothing to migrate")
    return {"codes": out, "skipped": skipped}


# Order matters: /customers/lookup (staff) must be registered before
# /customers/{cid} (owner).
app.include_router(public)
app.include_router(staff)
app.include_router(owner)


# ---- CORS: only the real frontend origin(s), never "*" ----------------
def _cors_origins():
    raw = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
    safe = [o for o in raw if o != "*"]
    if len(safe) != len(raw):
        logger.error("CORS_ORIGINS contains '*' - ignored. List the real frontend origin(s).")
    if not safe:
        logger.warning("CORS_ORIGINS is empty - browsers on other origins will be blocked.")
    return safe


app.add_middleware(
    CORSMiddleware,
    allow_credentials=False,  # we use an Authorization header, not cookies
    allow_origins=_cors_origins(),
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Cache-Control", "no-store")
    return resp