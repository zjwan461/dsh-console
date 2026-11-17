"""Console maintenance CLI.

    python -m app.cli list-users
    python -m app.cli reset-password alice            # generates a password
    python -m app.cli reset-password alice --password 'S3cret!'
    python -m app.cli create-admin ops --password 'S3cret!'
    python -m app.cli whoami-config

Run it from `api/` (or with `PYTHONPATH=api`), and point `DATABASE_URL` at the
same database the API uses. This is the escape hatch when nobody knows the admin
password — the API only creates a bootstrap admin when the table is empty.
"""

from __future__ import annotations

import argparse
import secrets
import sys

from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .models import Plan, Role, User, UserQuota, UserSession, UserStatus
from .security import hash_password, utcnow, validate_username
from .services.quota import current_cycle


def _resolve_password(password: str | None) -> tuple[str, bool]:
    if password:
        if len(password) < 8:
            raise SystemExit("password must be at least 8 characters")
        return password, False
    # Readable but still random: 18 chars of urlsafe base64.
    generated = secrets.token_urlsafe(13)
    return generated, True


def find_user(db: Session, username: str) -> User:
    user = db.query(User).filter(User.username == username, User.deleted_at.is_(None)).first()
    if user is None:
        raise SystemExit(f"no such user: {username}")
    return user


def reset_password(
    db: Session,
    username: str,
    password: str | None = None,
    *,
    force_change: bool = True,
) -> tuple[User, str]:
    user = find_user(db, username)
    plain, generated = _resolve_password(password)
    user.password_hash = hash_password(plain)
    user.must_change_password = force_change
    # A password reset must also end every existing session.
    db.query(UserSession).filter(
        UserSession.user_id == user.id, UserSession.revoked_at.is_(None)
    ).update({UserSession.revoked_at: utcnow()})
    db.commit()
    return user, plain if generated else ""


def create_admin(
    db: Session, username: str, password: str | None = None, display_name: str = ""
) -> tuple[User, str]:
    error = validate_username(username)
    if error:
        raise SystemExit(error)
    if db.query(User.id).filter(User.username == username).first():
        raise SystemExit(f"user already exists: {username}")
    plain, generated = _resolve_password(password)
    plan = db.query(Plan).filter(Plan.is_default.is_(True)).first()
    start, end = current_cycle()
    user = User(
        username=username,
        subdomain=username,
        display_name=display_name or username,
        password_hash=hash_password(plain),
        role=Role.admin,
        status=UserStatus.active,
        plan_id=plan.id if plan else None,
        must_change_password=False,
    )
    db.add(user)
    db.flush()
    db.add(
        UserQuota(
            user_id=user.id,
            plan_id=plan.id if plan else None,
            cycle_start=start,
            cycle_end=end,
        )
    )
    db.commit()
    return user, plain if generated else ""


def list_users(db: Session) -> list[User]:
    return db.query(User).filter(User.deleted_at.is_(None)).order_by(User.id).all()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli", description="优墨云舱 maintenance commands"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-users", help="list active users")

    reset = sub.add_parser("reset-password", help="reset a user's password")
    reset.add_argument("username")
    reset.add_argument("--password", default=None, help="omit to generate one")
    reset.add_argument(
        "--no-force-change",
        dest="force_change",
        action="store_false",
        help="do not require a password change at next login",
    )

    create = sub.add_parser("create-admin", help="create an additional admin")
    create.add_argument("username")
    create.add_argument("--password", default=None, help="omit to generate one")
    create.add_argument("--display-name", default="")

    sub.add_parser("show-config", help="print the effective database/domain settings")

    args = parser.parse_args(argv)

    if args.command == "show-config":
        print(f"DATABASE_URL = {settings.database_url}")
        print(f"BASE_DOMAIN  = {settings.base_domain}")
        print(f"ENV          = {settings.env}")
        return 0

    with SessionLocal() as db:
        if args.command == "list-users":
            rows = list_users(db)
            if not rows:
                print("no users; the API creates a bootstrap admin on first start")
                return 0
            print(f"{'id':>4}  {'username':<24} {'role':<6} {'status':<9} must_change")
            for user in rows:
                print(
                    f"{user.id:>4}  {user.username:<24} {user.role.value:<6} "
                    f"{user.status.value:<9} {bool(user.must_change_password)}"
                )
            return 0

        if args.command == "reset-password":
            user, generated = reset_password(
                db, args.username, args.password, force_change=args.force_change
            )
            print(f"password reset for {user.username} (role={user.role.value})")
            if generated:
                print(f"generated password: {generated}")
            tail = (
                "the user must change it after login"
                if args.force_change
                else "no forced password change"
            )
            print(f"all existing sessions were revoked; {tail}")
            return 0

        if args.command == "create-admin":
            user, generated = create_admin(db, args.username, args.password, args.display_name)
            print(f"admin created: {user.username} (id={user.id})")
            if generated:
                print(f"generated password: {generated}")
            return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
