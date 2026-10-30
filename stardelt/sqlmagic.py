"""JupySQL integration for stardelt notebooks."""
import os

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

import stardelt


def _creator():
    return stardelt._connect()


def _configure_jupysql():
    try:
        from sql import connection as sql_connection
        try:
            from sql.magic import SqlMagic
        except Exception:
            SqlMagic = None
    except Exception:
        return

    os.environ.setdefault("PLOOMBER_STATS_ENABLED", "false")

    try:
        if SqlMagic is not None:
            try:
                if hasattr(SqlMagic, "autopandas"):
                    SqlMagic.autopandas = False
            except Exception:
                pass
            try:
                if hasattr(SqlMagic, "displaylimit"):
                    SqlMagic.displaylimit = 100
            except Exception:
                pass
            try:
                if hasattr(SqlMagic, "autolimit"):
                    SqlMagic.autolimit = 10000
            except Exception:
                pass
            try:
                if hasattr(SqlMagic, "feedback"):
                    SqlMagic.feedback = 0
            except Exception:
                pass
    except Exception:
        pass

    try:
        engine = create_engine("trino://", creator=_creator, poolclass=NullPool)
        try:
            orig = getattr(sql_connection.SQLAlchemyConnection, '_get_database_information', None)
            if orig is not None:
                def _fake_dbinfo(self):
                    return {"dialect": "trino", "driver": "trino", "server_version_info": None}
                sql_connection.SQLAlchemyConnection._get_database_information = _fake_dbinfo
                try:
                    conn = sql_connection.SQLAlchemyConnection(engine, alias="warehouse")
                finally:
                    sql_connection.SQLAlchemyConnection._get_database_information = orig
            else:
                conn = sql_connection.SQLAlchemyConnection(engine, alias="warehouse")
            try:
                sql_connection.ConnectionManager.connections["warehouse"] = conn
            except Exception:
                pass
            try:
                sql_connection.ConnectionManager.current = conn
            except Exception:
                pass
            return
        except Exception:
            pass
        try:
            sql_connection.ConnectionManager.set(engine, displaycon=False, alias="warehouse")
            return
        except Exception:
            pass
    except Exception:
        pass


_configure_jupysql()
