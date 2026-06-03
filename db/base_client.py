from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Mapping, Sequence


class BaseDBClient(ABC):
    """Common interface for SQLite, DuckDB, and PostgreSQL clients."""

    @abstractmethod
    def init_schema(self) -> None: ...

    @contextmanager
    @abstractmethod
    def transaction(self) -> Iterator[Any]: ...

    @abstractmethod
    def fetch_all(self, query: str, params: Any = ()) -> list[dict[str, Any]]: ...

    @abstractmethod
    def fetch_one(self, query: str, params: Any = ()) -> dict[str, Any] | None: ...

    @abstractmethod
    def execute(self, query: str, params: Any = ()) -> None: ...

    @abstractmethod
    def upsert(
        self,
        table: str,
        values: Mapping[str, Any],
        conflict_columns: Sequence[str],
        update_columns: Sequence[str] | None = None,
        connection: Any = None,
    ) -> None: ...

    def upsert_many(
        self,
        table: str,
        rows: Iterable[Mapping[str, Any]],
        conflict_columns: Sequence[str],
        update_columns: Sequence[str] | None = None,
    ) -> None:
        with self.transaction() as conn:
            for row in rows:
                self.upsert(table, row, conflict_columns, update_columns, connection=conn)
