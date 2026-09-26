"""``supabase`` DB provider: Postgres through Supabase's Supavisor pooler (#186).

Supavisor's transaction mode (port 6543) hands each transaction a possibly
different server connection, so psycopg's server-side prepared statements
break ("prepared statement ... does not exist"): ``prepare_threshold=None``
disables them. Migrations must not go through the pooler -- ``morphloop
migrate`` uses ``MORPHLOOP_DATABASE_DIRECT_URL`` (port 5432) when it is set.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from harness.core.settings import Settings


def supabase_engine() -> Engine:
    """An engine on ``DATABASE_URL`` (the Supavisor URL) with prepared statements off."""
    return create_engine(
        Settings().database_url,
        connect_args={"connect_timeout": 2, "prepare_threshold": None},
    )
