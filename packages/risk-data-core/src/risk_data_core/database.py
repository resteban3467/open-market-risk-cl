"""Hybrid DuckDB + SQLAlchemy 2.0 database access for the risk workspace.

``RiskDatabase`` enforces the read/write lock boundary from the workspace
operating laws:

- SQLAlchemy 2.0 is used for DDL and small contract headers (``dim_*``).
- DuckDB native + Polars Zero-Copy Arrow is used for bulk analytical data
  (``fct_*``), via ``driver.sql(...).pl()``.
- ``read_only=True`` blocks ``init_schema``, ``bulk_insert_polars``, ORM
  commits, and non-SELECT queries, even for ``:memory:`` databases where the
  physical DuckDB connection cannot be opened read-only.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import polars as pl
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from risk_data_core.models import Base
from risk_data_core.observability import get_logger, timed
from risk_data_core.validation import validate_ingestion

_WRITE_KEYWORDS = frozenset(
    {
        "ALTER",
        "ATTACH",
        "BEGIN",
        "COMMIT",
        "COPY",
        "CREATE",
        "DELETE",
        "DETACH",
        "DROP",
        "EXPORT",
        "IMPORT",
        "INSERT",
        "MERGE",
        "PRAGMA",
        "REPLACE",
        "RESET",
        "ROLLBACK",
        "SET",
        "TRUNCATE",
        "UPDATE",
        "VACUUM",
    }
)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_KEYWORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_VIEW_NAME = "__polars_insert_view__"


def _first_keyword(sql: str) -> str:
    match = _KEYWORD_RE.match(sql.lstrip())
    return match.group(0).upper() if match else ""


def _is_write_statement(sql: str) -> bool:
    return _first_keyword(sql) in _WRITE_KEYWORDS


def _driver(sqlalchemy_conn: Any) -> Any:
    """Return the underlying DuckDB connection from a SQLAlchemy connection."""
    return sqlalchemy_conn.connection.driver_connection


def _guard_session_factory(read_only: bool) -> type[Session]:
    """Build a Session subclass that blocks writes when ``read_only`` is set."""

    class _GuardSession(Session):
        def flush(self, objects: Any = None) -> None:
            if read_only:
                raise PermissionError("Cannot flush ORM writes in read_only mode.")
            super().flush(objects)

        def commit(self) -> None:
            if read_only:
                raise PermissionError("Cannot commit ORM writes in read_only mode.")
            super().commit()

    return _GuardSession


class RiskDatabase:
    """Hybrid SQLAlchemy/DuckDB access with an explicit read/write lock."""

    def __init__(self, db_path: Path, read_only: bool = True) -> None:
        self.db_path = db_path
        self.read_only = read_only
        self._logger = get_logger(__name__)
        is_memory = str(db_path) == ":memory:"
        # DuckDB refuses ``read_only=True`` on ``:memory:`` connections, so the
        # flag is simulated in Python instead (see ``_is_write_statement``).
        connect_args: dict[str, Any] = {} if is_memory else {"read_only": self.read_only}
        self.engine: Engine = create_engine(f"duckdb:///{self.db_path}", connect_args=connect_args)
        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=_guard_session_factory(self.read_only),
            expire_on_commit=False,
        )

    def init_schema(self) -> None:
        """Create/verify all declarative tables (DDL). Read-write only."""
        if self.read_only:
            raise PermissionError("Cannot initialize schema in read_only mode.")
        Base.metadata.create_all(self.engine)

    def query_polars(self, sql: str, params: Any = None) -> pl.DataFrame:
        """Run a SELECT and return a Polars DataFrame via Zero-Copy Arrow."""
        if self.read_only and _is_write_statement(sql):
            raise PermissionError("Cannot execute write statements in read_only mode.")
        with timed(self._logger, "query_polars", operation="query"), self.engine.connect() as conn:
            driver = _driver(conn)
            relation = driver.sql(sql) if params is None else driver.sql(sql, params=params)
            return relation.pl()

    def bulk_insert_polars(
        self,
        df: pl.DataFrame,
        table_name: str,
        replace_partition_col: str | None = None,
        replace_partition_val: Any = None,
        *,
        validate: bool = True,
        allow_negative_coupon: bool = False,
        conflict_columns: Sequence[str] | None = None,
    ) -> None:
        """Bulk-insert a Polars DataFrame into an existing table (idempotent).

        The frame is validated against the ``data-resilience-standards`` contract
        before any SQL runs. Writes are idempotent through one of two mechanisms:

        - ``conflict_columns`` performs an atomic upsert
          (``INSERT ... ON CONFLICT (...) DO UPDATE SET ...``). When omitted, the
          conflict target defaults to the table ``PRIMARY KEY``.
        - ``replace_partition_col`` / ``replace_partition_val`` delete the target
          partition and re-insert it in the same transaction.

        Reprocessing the same ``valuation_date`` never duplicates rows.
        """
        if self.read_only:
            raise PermissionError(f"Cannot insert into {table_name} in read_only mode.")
        if not _IDENTIFIER_RE.match(table_name):
            raise ValueError(f"invalid SQL identifier for table name: {table_name!r}")
        if (replace_partition_col is None) != (replace_partition_val is None):
            raise ValueError(
                "replace_partition_col and replace_partition_val must be provided together."
            )
        if replace_partition_col is not None and not _IDENTIFIER_RE.match(replace_partition_col):
            raise ValueError(
                f"invalid SQL identifier for partition column: {replace_partition_col!r}"
            )
        if replace_partition_col is not None and conflict_columns is not None:
            raise ValueError("conflict_columns and replace_partition_col are mutually exclusive.")
        if df.is_empty():
            self._logger.info(
                "bulk_insert_polars",
                table_name=table_name,
                row_count=0,
                execution_time_ms=0.0,
                skipped_reason="empty_dataframe",
            )
            return
        self._validate_columns(df, table_name)
        if validate:
            validate_ingestion(
                df,
                table_name,
                allow_negative_coupon=allow_negative_coupon,
            )

        if conflict_columns is None and replace_partition_col is None:
            conflict_columns = self._primary_key_columns(table_name)
        if conflict_columns is not None:
            self._validate_conflict_columns(df, conflict_columns)
        update_columns = self._upsert_update_columns(df, conflict_columns)

        context: dict[str, Any] = {"table_name": table_name, "row_count": df.height}
        if replace_partition_col is not None:
            context["replace_partition_col"] = replace_partition_col
            context["replace_partition_val"] = replace_partition_val
        if conflict_columns:
            context["conflict_columns"] = tuple(conflict_columns)

        with timed(self._logger, "bulk_insert_polars", **context), self.engine.begin() as conn:
            driver = _driver(conn)
            driver.register(_VIEW_NAME, df)
            try:
                if replace_partition_col is not None:
                    driver.execute(
                        f"DELETE FROM {table_name} WHERE {replace_partition_col} = ?",
                        [replace_partition_val],
                    )
                columns = ", ".join(df.columns)
                conflict_clause = self._conflict_clause(conflict_columns, update_columns)
                driver.execute(
                    f"INSERT INTO {table_name} ({columns}) "
                    f"SELECT {columns} FROM {_VIEW_NAME}{conflict_clause}"
                )
            finally:
                driver.unregister(_VIEW_NAME)

    def _primary_key_columns(self, table_name: str) -> tuple[str, ...]:
        """Return the natural key of ``table_name`` as its declared primary key."""
        table = Base.metadata.tables.get(table_name)
        if table is None:
            raise ValueError(f"unknown table name: {table_name!r}")
        return tuple(column.name for column in table.primary_key.columns)

    @staticmethod
    def _validate_conflict_columns(df: pl.DataFrame, conflict_columns: Sequence[str]) -> None:
        for column in conflict_columns:
            if not _IDENTIFIER_RE.match(column):
                raise ValueError(f"invalid SQL identifier for conflict column: {column!r}")
            if column not in df.columns:
                raise ValueError(f"conflict column {column!r} not present in DataFrame")

    @staticmethod
    def _upsert_update_columns(
        df: pl.DataFrame, conflict_columns: Sequence[str] | None
    ) -> list[str]:
        if not conflict_columns:
            return []
        conflict = set(conflict_columns)
        return [column for column in df.columns if column not in conflict]

    @staticmethod
    def _conflict_clause(
        conflict_columns: Sequence[str] | None,
        update_columns: Sequence[str],
    ) -> str:
        if not conflict_columns:
            return ""
        target = ", ".join(conflict_columns)
        if not update_columns:
            return f" ON CONFLICT ({target}) DO NOTHING"
        assignments = ", ".join(f"{column} = EXCLUDED.{column}" for column in update_columns)
        return f" ON CONFLICT ({target}) DO UPDATE SET {assignments}"

    def _validate_columns(self, df: pl.DataFrame, table_name: str) -> None:
        table = Base.metadata.tables.get(table_name)
        if table is None:
            raise ValueError(f"unknown table name: {table_name!r}")
        missing = sorted({column.name for column in table.columns} - set(df.columns))
        if missing:
            raise ValueError(
                f"DataFrame is missing columns required by table '{table_name}': {missing}"
            )

    def close(self) -> None:
        """Release the engine and its pooled DuckDB connections."""
        self.engine.dispose()
