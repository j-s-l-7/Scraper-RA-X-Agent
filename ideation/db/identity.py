"""Connection identity checks.

Trust auth means a DSN without a user silently connects as the OS superuser. Every app connection proves who it is
before doing anything.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg


class IdentityError(RuntimeError):
    """A connection is not the role it must be (e.g. an app path connected as a superuser)."""


@dataclass(frozen=True)
class Identity:
    user: str
    superuser: bool
    read_only_default: bool


def session_identity(conn: psycopg.Connection) -> Identity:
    row = conn.execute(
        "select current_user, current_setting('is_superuser'), current_setting('default_transaction_read_only')"
    ).fetchone()
    assert row is not None
    return Identity(user=row[0], superuser=row[1] == "on", read_only_default=row[2] == "on")


def require_app_role(conn: psycopg.Connection, role: str, *, read_only: bool) -> Identity:
    """Refuse the connection unless it is ``role``, not a superuser, and (for reads) read-only by default."""
    ident = session_identity(conn)
    if ident.superuser or ident.user != role:
        raise IdentityError(f"connected as {ident.user!r} (superuser={ident.superuser}); expected {role!r}")
    if read_only and not ident.read_only_default:
        raise IdentityError("read path is not read-only (default_transaction_read_only is off)")
    return ident


def require_superuser(conn: psycopg.Connection) -> Identity:
    ident = session_identity(conn)
    if not ident.superuser:
        raise IdentityError(f"bootstrap needs a superuser; connected as {ident.user!r}")
    return ident
