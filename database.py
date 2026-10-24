# # import os
# # from pathlib import Path
# # from dotenv import load_dotenv
# # from motor.motor_asyncio import AsyncIOMotorClient

# # ROOT_DIR = Path(__file__).parent
# # # Local dev reads backend/.env; hosted platforms (Railway, etc.) inject real
# # # environment variables and simply have no .env file, which load_dotenv ignores.
# # load_dotenv(ROOT_DIR / '.env')

# # mongo_url = os.environ.get("MONGO_URL")
# # if not mongo_url:
# #     raise RuntimeError(
# #         "MONGO_URL is not set. Locally, copy backend/.env.example to backend/.env. "
# #         "On Railway, add it under the service's Variables tab."
# #     )

# # client = AsyncIOMotorClient(mongo_url)
# # db = client[os.environ.get("DB_NAME", "legacy_pos")]

# # customers = db.customers
# # billing = db.billing
# # payment = db.payment
# # import_batches = db.import_batches


# # async def ensure_indexes():
# #     await customers.create_index("rfid_norm")
# #     await customers.create_index("branch")
# #     await customers.create_index("name")
# #     await customers.create_index("phone")
# #     await customers.create_index("id", unique=True)
# #     for coll in (billing, payment):
# #         await coll.create_index([("branch", 1), ("guid", 1)])
# #         await coll.create_index([("branch", 1), ("customer_id", 1)])
# #         await coll.create_index("rfid_norm")
# #         await coll.create_index("created_at")
# #         await coll.create_index("id", unique=True)


# import os
# from pathlib import Path
# from dotenv import load_dotenv
# from motor.motor_asyncio import AsyncIOMotorClient

# ROOT_DIR = Path(__file__).parent
# # Local dev reads backend/.env; hosted platforms (Railway, etc.) inject real
# # environment variables and simply have no .env file, which load_dotenv ignores.
# load_dotenv(ROOT_DIR / '.env')

# mongo_url = os.environ.get("MONGO_URL")
# if not mongo_url:
#     raise RuntimeError(
#         "MONGO_URL is not set. Locally, copy backend/.env.example to backend/.env. "
#         "On Railway, add it under the service's Variables tab."
#     )

# client = AsyncIOMotorClient(mongo_url)
# db = client[os.environ.get("DB_NAME", "legacy_pos")]

# customers = db.customers
# billing = db.billing
# payment = db.payment
# import_batches = db.import_batches

# # NEW (security) - staff accounts, audit trail and issued migration codes.
# app_users = db.app_users
# audit_log = db.audit_log
# migration_codes = db.migration_codes


# async def ensure_indexes():
#     await customers.create_index("rfid_norm")
#     await customers.create_index("branch")
#     await customers.create_index("name")
#     await customers.create_index("phone")
#     await customers.create_index("id", unique=True)
#     for coll in (billing, payment):
#         await coll.create_index([("branch", 1), ("guid", 1)])
#         await coll.create_index([("branch", 1), ("customer_id", 1)])
#         await coll.create_index("rfid_norm")
#         await coll.create_index("created_at")
#         await coll.create_index("id", unique=True)
#     # NEW (security)
#     await app_users.create_index("username", unique=True)
#     await app_users.create_index("id", unique=True)
#     await audit_log.create_index("ts")
#     await audit_log.create_index("event")
#     await migration_codes.create_index("jti", unique=True)
#     await migration_codes.create_index([("issued_by", 1), ("issued_at", -1)])

import os
from pathlib import Path
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT_DIR = Path(__file__).parent
# Local dev reads backend/.env; hosted platforms (Railway, etc.) inject real
# environment variables and simply have no .env file, which load_dotenv ignores.
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ.get("MONGO_URL")
if not mongo_url:
    raise RuntimeError(
        "MONGO_URL is not set. Locally, copy backend/.env.example to backend/.env. "
        "On Railway, add it under the service's Variables tab."
    )

client = AsyncIOMotorClient(mongo_url)
db = client[os.environ.get("DB_NAME", "legacy_pos")]

customers = db.customers
billing = db.billing
payment = db.payment
import_batches = db.import_batches

# NEW (security) - staff accounts, audit trail and issued migration codes.
app_users = db.app_users
audit_log = db.audit_log
migration_codes = db.migration_codes


async def ensure_indexes():
    await customers.create_index("rfid_norm")
    await customers.create_index("branch")
    await customers.create_index("name")
    await customers.create_index("phone")
    await customers.create_index("phone_norm")  # NEW - exact phone search
    await customers.create_index("id", unique=True)
    for coll in (billing, payment):
        await coll.create_index([("branch", 1), ("guid", 1)])
        await coll.create_index([("branch", 1), ("customer_id", 1)])
        await coll.create_index("rfid_norm")
        await coll.create_index("created_at")
        await coll.create_index("id", unique=True)
    # NEW (security)
    await app_users.create_index("username", unique=True)
    await app_users.create_index("id", unique=True)
    await audit_log.create_index("ts")
    await audit_log.create_index("event")
    await migration_codes.create_index("jti", unique=True)
    await migration_codes.create_index([("issued_by", 1), ("issued_at", -1)])