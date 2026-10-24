from collections.abc import Mapping, Sequence

from sqlalchemy import ColumnElement, String, all_, any_, delete, func, literal, select, update
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.records import Record
from openfesl.storage.models import RecordEntryRow


class SqlRecordStore:
    """Records in PostgreSQL, one row per entry."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def find(self, partition: str, persona_id: int, name: str) -> Record | None:
        """Return a persona's record, or None when it holds no entries.

        Args:
            partition: The game's partition.
            persona_id: The persona that owns the record.
            name: The record's name.
        """
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(RecordEntryRow)
                    .where(*_record(partition, persona_id, name))
                    .order_by(RecordEntryRow.key)
                )
            ).all()
        if not rows:
            return None
        return Record({row.key: row.value for row in rows}, max(row.modified_at for row in rows))

    async def write(
        self,
        partition: str,
        persona_id: int,
        name: str,
        values: Mapping[str, str],
        removed: Sequence[str],
        size: int,
    ) -> bool:
        """Remove keys, then set entries, all of it or nothing.

        Args:
            partition: The game's partition.
            persona_id: The persona that owns the record.
            name: The record's name.
            values: The entries to set by key.
            removed: The keys to remove.
            size: The most entries the record may hold afterwards.

        Returns:
            False, with nothing changed, when the record would hold more than `size` entries.
        """
        record = _record(partition, persona_id, name)
        async with self._sessions.begin() as session:
            untouched = await session.scalar(
                select(func.count())
                .select_from(RecordEntryRow)
                .where(*record, RecordEntryRow.key != all_(_keys([*removed, *values])))
            )
            if (untouched or 0) + len(values) > size:
                return False
            if removed:
                await session.execute(
                    delete(RecordEntryRow).where(
                        *record, RecordEntryRow.key == any_(_keys(removed))
                    )
                )
            if values:
                statement = insert(RecordEntryRow)
                await session.execute(
                    statement.on_conflict_do_update(
                        constraint="record_entry_pkey", set_={"value": statement.excluded.value}
                    ),
                    [
                        {
                            "persona_id": persona_id,
                            "partition": partition,
                            "name": name,
                            "key": key,
                            "value": value,
                        }
                        for key, value in values.items()
                    ],
                )
            # Any change dates the whole record, removals included.
            await session.execute(
                update(RecordEntryRow).where(*record).values(modified_at=func.now())
            )
        return True


def _record(partition: str, persona_id: int, name: str) -> list[ColumnElement[bool]]:
    return [
        RecordEntryRow.persona_id == persona_id,
        RecordEntryRow.partition == partition,
        RecordEntryRow.name == name,
    ]


def _keys(keys: Sequence[str]) -> ColumnElement[list[str]]:
    # One array parameter however many keys a request names.
    return literal(list(keys), ARRAY(String))
