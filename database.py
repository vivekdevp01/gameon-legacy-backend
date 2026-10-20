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


async def ensure_indexes():
    await customers.create_index("rfid_norm")
    await customers.create_index("branch")
    await customers.create_index("name")
    await customers.create_index("phone")
    await customers.create_index("id", unique=True)
    for coll in (billing, payment):
        await coll.create_index([("branch", 1), ("guid", 1)])
        await coll.create_index([("branch", 1), ("customer_id", 1)])
        await coll.create_index("rfid_norm")
        await coll.create_index("created_at")
        await coll.create_index("id", unique=True)
