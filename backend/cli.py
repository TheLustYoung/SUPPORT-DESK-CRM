"""Operator commands: `python -m backend.cli seed | create-admin`. Passwords are read with getpass."""
import argparse
import os
from getpass import getpass
from sqlalchemy import func, select
from backend.config import settings
from backend.database import Session
from backend.models import Staff
from backend.services import setup
from backend.services.auth import hasher, password_problem


def seed():
    with Session() as db:
        setup.seed(db)
        password = settings().first_admin_password.get_secret_value()
        if password and not db.scalar(select(func.count()).select_from(Staff)):
            db.add(Staff(username=settings().first_admin_username, display_name="Administrator", password_hash=hasher.hash(password),
                         role_id="super_admin", created_by="setup"))
            db.commit()
            print(f"Created the first Super Administrator {settings().first_admin_username!r}")
    print("Seed complete")


def create_admin():
    username = os.environ.get("ADMIN_USERNAME") or input("Super Administrator username: ").strip()
    password = getpass("Password (at least 12 characters, letters and digits): ")
    if not username or len(username) > 56 or password_problem(password) or password != getpass("Repeat password: "):
        raise SystemExit("Invalid username or password (at least 12 characters, not only letters or only digits), or they differ")
    with Session() as db:
        setup.seed(db)
        if db.scalar(select(Staff).where(func.lower(Staff.username) == username.lower())):
            raise SystemExit("This username already exists")
        db.add(Staff(username=username, display_name=username, password_hash=hasher.hash(password), role_id="super_admin", created_by="cli"))
        db.commit()
    print("Super Administrator created")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["seed", "create-admin"])
    args = parser.parse_args()
    seed() if args.command == "seed" else create_admin()


if __name__ == "__main__":
    main()
