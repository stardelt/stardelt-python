"""JupySQL in a stardelt notebook: ``%sql`` and ``%%sql`` run as the signed-in person.

Load it with ``%load_ext stardelt.sqlmagic`` (the platform's notebook image does that when
the kernel starts). It needs the ``jupyter`` extra: ``pip install "stardelt[jupyter]"``.

The default connection is called ``warehouse``. It stays open for the life of the kernel, so
it asks the library for the current token on every request instead of keeping the one it
had at start (a token lives minutes, a kernel hours). The vendor telemetry of JupySQL is off.
"""

import os
import warnings

import stardelt

ALIAS = "warehouse"


def _creator():
    return stardelt._connect(live=True)


def connect():
    """Register the default connection ``warehouse`` (called by the extension)."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    from sql.connection import SQLAlchemyConnection

    engine = create_engine("trino://", creator=_creator, poolclass=NullPool)
    return SQLAlchemyConnection(engine, alias=ALIAS)


def load_ipython_extension(ip):
    os.environ.setdefault("PLOOMBER_STATS_ENABLED", "false")
    ip.run_line_magic("load_ext", "sql")
    ip.run_line_magic("config", "SqlMagic.displaylimit = 100")
    ip.run_line_magic("config", "SqlMagic.autolimit = 10000")
    try:
        connect()
    except Exception as exc:
        # Visible, not silent: a kernel outside the platform has no Trino to reach.
        warnings.warn(
            f"stardelt.sqlmagic: no default connection ({exc}). "
            "Fix the cause, then run stardelt.sqlmagic.connect().",
            stacklevel=2,
        )
