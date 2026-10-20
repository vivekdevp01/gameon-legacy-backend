"""Copy a database between two MongoDB servers (e.g. local -> Atlas).

Uses the pymongo already installed in backend/.venv, so it needs no extra
tooling (mongodump/mongorestore are a separate ~100 MB download).

Examples
--------
Local -> Atlas:

    .\\.venv\\Scripts\\python migrate_db.py ^
        --target "mongodb+srv://USER:PASS@cluster0.xxxxx.mongodb.net/" --drop

Atlas -> local (pull production data back down):

    .\\.venv\\Scripts\\python migrate_db.py ^
        --source "mongodb+srv://USER:PASS@cluster0.xxxxx.mongodb.net/" ^
        --target "mongodb://localhost:27017" --drop
"""
import argparse
import sys
import time

from pymongo import MongoClient
from pymongo.errors import BulkWriteError, PyMongoError

BATCH = 5000

# Mirrors ensure_indexes() in database.py so the copy is queryable at the same
# speed as the source.
INDEXES = {
    "customers": [
        [("rfid_norm", 1)], [("branch", 1)], [("name", 1)], [("phone", 1)],
    ],
    "billing": [
        [("branch", 1), ("guid", 1)], [("branch", 1), ("customer_id", 1)],
        [("rfid_norm", 1)], [("created_at", 1)],
    ],
    "payment": [
        [("branch", 1), ("guid", 1)], [("branch", 1), ("customer_id", 1)],
        [("rfid_norm", 1)], [("created_at", 1)],
    ],
}
UNIQUE_ID = ("customers", "billing", "payment")


def human(n):
    return f"{n:,}"


def copy_collection(src_db, dst_db, name, drop):
    src = src_db[name]
    dst = dst_db[name]

    total = src.estimated_document_count()
    if total == 0:
        print(f"  {name:<16} empty, skipped")
        return 0

    if drop:
        dst.drop()

    started = time.time()
    copied = 0
    batch = []

    for doc in src.find({}, no_cursor_timeout=True).batch_size(BATCH):
        batch.append(doc)
        if len(batch) >= BATCH:
            copied += _flush(dst, batch)
            batch = []
            pct = copied * 100 // max(total, 1)
            print(f"\r  {name:<16} {human(copied)}/{human(total)}  {pct}%", end="", flush=True)

    if batch:
        copied += _flush(dst, batch)

    secs = time.time() - started
    print(f"\r  {name:<16} {human(copied)}/{human(total)}  done in {secs:.1f}s" + " " * 12)
    return copied


def _flush(dst, batch):
    """Insert a batch, tolerating rows that already exist on the target."""
    try:
        dst.insert_many(batch, ordered=False)
        return len(batch)
    except BulkWriteError as e:
        # Duplicate _id just means this row was copied by an earlier run.
        dupes = sum(1 for err in e.details.get("writeErrors", []) if err.get("code") == 11000)
        other = len(e.details.get("writeErrors", [])) - dupes
        if other:
            raise
        return len(batch) - dupes


def build_indexes(dst_db):
    print("Building indexes...")
    for coll, specs in INDEXES.items():
        for spec in specs:
            dst_db[coll].create_index(spec)
        if coll in UNIQUE_ID:
            dst_db[coll].create_index("id", unique=True)
        print(f"  {coll:<16} {len(specs) + 1} indexes")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="mongodb://localhost:27017",
                    help="source URI (default: local MongoDB)")
    ap.add_argument("--target", required=True, help="target URI")
    ap.add_argument("--db", default="legacy_pos", help="database name (default: legacy_pos)")
    ap.add_argument("--target-db", default=None,
                    help="target database name (defaults to --db)")
    ap.add_argument("--drop", action="store_true",
                    help="drop each target collection before copying")
    args = ap.parse_args()

    target_db_name = args.target_db or args.db

    print(f"source : {args.source.split('@')[-1]}  db={args.db}")
    print(f"target : {args.target.split('@')[-1]}  db={target_db_name}")
    print()

    try:
        src_client = MongoClient(args.source, serverSelectionTimeoutMS=20000)
        dst_client = MongoClient(args.target, serverSelectionTimeoutMS=20000)
        src_client.admin.command("ping")
        dst_client.admin.command("ping")
    except PyMongoError as e:
        print(f"Could not connect: {e}", file=sys.stderr)
        print("\nIf the target is Atlas, check that Network Access allows 0.0.0.0/0 "
              "and that the password is URL-encoded.", file=sys.stderr)
        return 1

    src_db = src_client[args.db]
    dst_db = dst_client[target_db_name]

    names = [c for c in src_db.list_collection_names() if not c.startswith("system.")]
    if not names:
        print(f"Source database '{args.db}' has no collections.", file=sys.stderr)
        return 1

    print(f"Copying {len(names)} collection(s){' (dropping targets first)' if args.drop else ''}:")
    grand = 0
    for name in sorted(names):
        grand += copy_collection(src_db, dst_db, name, args.drop)

    print()
    build_indexes(dst_db)

    print()
    print(f"Copied {human(grand)} documents.")
    print("Target now holds:")
    for name in sorted(names):
        print(f"  {name:<16} {human(dst_db[name].count_documents({}))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
