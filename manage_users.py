"""Owner-side account tool (run on the server / your laptop, never over the web).

    python manage_users.py create-owner  <username>      # asks for the password
    python manage_users.py set-password  <username>      # reset a forgotten password
    python manage_users.py list

Reads MONGO_URL / DB_NAME from the environment or backend/.env. The password is
typed at a hidden prompt, so it never appears in shell history or logs.
"""
import asyncio
import getpass
import sys

from database import app_users
import auth


async def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[1]
    if cmd == "list":
        async for u in app_users.find({}).sort("username", 1):
            print(f"{u['username']:<20} {u['role']:<8} {'active' if u.get('active', True) else 'DISABLED'}")
        return 0
    if len(argv) < 3:
        print("username is required")
        return 1
    username = argv[2].strip().lower()
    pw = getpass.getpass("New password: ")
    if pw != getpass.getpass("Repeat password: "):
        print("Passwords do not match")
        return 1
    auth.validate_new_password(pw)
    if cmd == "create-owner":
        await auth.create_user(username, pw, "owner", "Owner")
        print(f"Owner '{username}' created")
    elif cmd == "set-password":
        res = await app_users.update_one(
            {"username": username},
            {"$set": {"password_hash": auth.hash_password(pw), "failed_attempts": 0, "locked_until": None, "active": True},
             "$inc": {"token_version": 1}},
        )
        print("Password updated" if res.matched_count else "No such user")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv)))