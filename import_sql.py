"""Import a legacy MySQL dump straight into MongoDB — no size limit.

The browser Import page has to push the whole file through HTTP, so hosted
platforms cap it (request-body limits, proxy timeouts). This writes directly to
the database instead, so an 86 MB dump is no different from a 1 MB one, and it
can target the local server or Atlas.

Examples
--------
Into local MongoDB:

    .\\.venv\\Scripts\\python import_sql.py --file ..\\_ref\\billing.sql --branch Main

Straight into Atlas:

    .\\.venv\\Scripts\\python import_sql.py --file ..\\_ref\\billing.sql --branch Main ^
        --target "mongodb+srv://USER:PASS@cluster0.xxxxx.mongodb.net/"

Check what a file holds without writing anything:

    .\\.venv\\Scripts\\python import_sql.py --file ..\\_ref\\billing.sql --branch Main --dry-run
"""
import argparse
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import BulkWriteError, PyMongoError

from sql_parser import (
    iter_inserts, detect_table,
    CUSTOMER_STRING_COLS, BILLING_STRING_COLS, PAYMENT_STRING_COLS,
)

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

BATCH = 2000

DATASETS = {
    "es_customer": {"coll": "customers", "strings": CUSTOMER_STRING_COLS, "name": "customer"},
    "es_billing": {"coll": "billing", "strings": BILLING_STRING_COLS, "name": "billing"},
    "es_payment": {"coll": "payment", "strings": PAYMENT_STRING_COLS, "name": "payment"},
}
ALIASES = {"customer": "es_customer", "billing": "es_billing", "payment": "es_payment"}


def normalize_rfid(value):
    """Must match server.py — otherwise card lookups miss these rows."""
    import re
    if value is None:
        return None
    s = str(value).strip().upper()
    if not s:
        return None
    m = re.search(r"\{?([0-9A-F]+)\}?", s)
    inner = m.group(1) if m else re.sub(r"[^0-9A-Z]", "", s)
    return "{" + inner + "}"


def build_doc(row, branch, filename, now_iso):
    doc = dict(row)
    doc["original_id"] = doc.pop("id", None)
    doc["branch"] = branch
    doc["source_file"] = filename
    doc["imported_at"] = now_iso
    doc["id"] = str(uuid.uuid4())
    if "rfid" in row:
        doc["rfid_norm"] = normalize_rfid(row.get("rfid"))
    return doc


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", required=True, help="path to the .sql dump")
    ap.add_argument("--branch", required=True, help="branch label to tag these rows with")
    ap.add_argument("--dataset", choices=["customer", "billing", "payment"],
                    help="skip auto-detection and force the dataset")
    ap.add_argument("--target", default=None,
                    help="MongoDB URI (default: MONGO_URL from backend/.env)")
    ap.add_argument("--db", default=None,
                    help="database name (default: DB_NAME from backend/.env, else legacy_pos)")
    ap.add_argument("--dry-run", action="store_true",
                    help="parse and report, but write nothing")
    args = ap.parse_args()

    path = Path(args.file).expanduser().resolve()
    if not path.is_file():
        print(f"No such file: {path}", file=sys.stderr)
        return 1

    branch = args.branch.strip()
    if not branch:
        print("--branch cannot be empty", file=sys.stderr)
        return 1

    uri = args.target or os.environ.get("MONGO_URL")
    if not uri and not args.dry_run:
        print("No target. Pass --target, or set MONGO_URL in backend/.env.", file=sys.stderr)
        return 1
    db_name = args.db or os.environ.get("DB_NAME", "legacy_pos")

    size_mb = path.stat().st_size / 1048576
    print(f"file   : {path.name}  ({size_mb:.1f} MB)")
    print("reading...", end=" ", flush=True)
    text = path.read_text(encoding="utf-8", errors="replace")
    print("done")

    table = ALIASES.get(args.dataset) if args.dataset else detect_table(text)
    if not table or table not in DATASETS:
        print("\nCould not find a supported table (es_customer, es_billing, es_payment) "
              "in this file.\nForce it with --dataset if the dump is unusual.", file=sys.stderr)
        return 1

    cfg = DATASETS[table]
    print(f"table  : {table} -> {cfg['coll']}")
    print(f"branch : {branch}")

    if args.dry_run:
        print("\n(dry run — counting rows, writing nothing)")
        n = sum(1 for _ in iter_inserts(text, table, cfg["strings"]))
        print(f"would import {n:,} rows")
        return 0

    print(f"target : {uri.split('@')[-1]}  db={db_name}")
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=20000)
        client.admin.command("ping")
    except PyMongoError as e:
        print(f"\nCould not connect: {e}", file=sys.stderr)
        return 1

    db = client[db_name]
    coll = db[cfg["coll"]]
    now_iso = datetime.now(timezone.utc).isoformat()

    print()
    started = time.time()
    imported = 0
    batch = []

    def flush(docs):
        try:
            coll.insert_many(docs, ordered=False)
            return len(docs)
        except BulkWriteError as e:
            dupes = sum(1 for err in e.details.get("writeErrors", []) if err.get("code") == 11000)
            if len(e.details.get("writeErrors", [])) - dupes:
                raise
            return len(docs) - dupes

    for row in iter_inserts(text, table, cfg["strings"]):
        batch.append(build_doc(row, branch, path.name, now_iso))
        if len(batch) >= BATCH:
            imported += flush(batch)
            batch = []
            print(f"\r  imported {imported:,} rows", end="", flush=True)
    if batch:
        imported += flush(batch)

    secs = time.time() - started
    print(f"\r  imported {imported:,} rows in {secs:.1f}s" + " " * 12)

    if imported == 0:
        print(f"\nNo INSERT rows found for {table} in this file.", file=sys.stderr)
        return 1

    db.import_batches.insert_one({
        "id": str(uuid.uuid4()), "branch": branch, "dataset": cfg["name"],
        "filename": path.name, "row_count": imported, "created_at": now_iso,
    })

    print()
    print(f"{cfg['coll']} now holds {coll.count_documents({}):,} rows "
          f"({coll.count_documents({'branch': branch}):,} in branch '{branch}')")
    return 0


if __name__ == "__main__":
    sys.exit(main())
