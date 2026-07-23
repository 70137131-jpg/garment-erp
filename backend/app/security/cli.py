"""Administrative recovery commands for deployments without a working admin login."""

import argparse
from getpass import getpass

from sqlmodel import Session, select

from ..db import engine
from .models import User
from .passwords import validate_password
from .service import normalize_email, reset_user_password


def reset_password(email: str) -> int:
    password = getpass("Temporary password: ")
    confirmation = getpass("Confirm temporary password: ")
    if password != confirmation:
        print("Passwords do not match.")
        return 2
    try:
        validate_password(password)
    except ValueError as exc:
        print(f"Invalid password: {exc}")
        return 2

    with Session(engine) as session:
        user = session.exec(
            select(User).where(User.email == normalize_email(email))
        ).one_or_none()
        if user is None:
            print("User not found.")
            return 1
        reset_user_password(session, user, password)
        session.commit()
    print("Temporary password set. Existing sessions were revoked.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Garment ERP security recovery")
    subparsers = parser.add_subparsers(dest="command", required=True)
    reset = subparsers.add_parser("reset-password", help="reset a user's password")
    reset.add_argument("--email", required=True)
    args = parser.parse_args()
    if args.command == "reset-password":
        return reset_password(args.email)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
