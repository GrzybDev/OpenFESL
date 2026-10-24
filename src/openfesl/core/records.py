import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from openfesl.core.fesl import PARAMETERS_ERROR, FeslConnection, Route, TransactionError
from openfesl.core.stats import PERSONA, Owner, game_of, owner_of, partition_of
from openfesl.protocol.message import Message
from openfesl.protocol.recp import (
    GetRecord,
    GetRecordAnswer,
    GetRecordAsMapAnswer,
    RecordEntry,
    WriteRecord,
    WriteRecordAsMap,
)

logger = logging.getLogger(__name__)

RECORD_NOT_FOUND = 5000


@dataclass(frozen=True, slots=True)
class Record:
    """A persona's record: its entries by key, in key order, and when it last changed."""

    entries: dict[str, str]
    modified: datetime


class RecordStore(Protocol):
    """Where the records of a game's partition are kept, unique per persona, name and key."""

    async def find(self, partition: str, persona_id: int, name: str) -> Record | None:
        """Return a persona's record, or None when it holds no entries.

        Args:
            partition: The game's partition.
            persona_id: The persona that owns the record.
            name: The record's name.
        """

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


class RecordService:
    """The recp transactions, through which a game keeps small records per persona.

    A record that holds no entries does not exist. Adding and updating both store entries:
    the game adds a record it found missing and updates one it found, so either creates the
    record or changes it, and a record two sessions race to create is not lost.
    """

    def __init__(self, store: RecordStore) -> None:
        """Serve records from a store.

        Args:
            store: Where records are kept.
        """
        self._store = store

    def routes(self) -> dict[tuple[str, str], Route[Any]]:
        """Return the handlers of the service's transactions."""
        return {
            ("recp", "GetRecord"): Route(GetRecord, self.get_record),
            ("recp", "GetRecordAsMap"): Route(GetRecord, self.get_record_as_map),
            ("recp", "AddRecord"): Route(WriteRecord, self.write_record),
            ("recp", "UpdateRecord"): Route(WriteRecord, self.write_record),
            ("recp", "AddRecordAsMap"): Route(WriteRecordAsMap, self.write_record_as_map),
            ("recp", "UpdateRecordAsMap"): Route(WriteRecordAsMap, self.write_record_as_map),
        }

    async def get_record(self, connection: FeslConnection, request: GetRecord) -> GetRecordAnswer:
        """Return a record's entries as a list; records of every persona are readable.

        Args:
            connection: The FESL connection the request came on.
            request: The owner and the record's name.
        """
        record = await self._find(connection, request)
        return GetRecordAnswer(
            values=[RecordEntry(key=key, value=value) for key, value in record.entries.items()]
        )

    async def get_record_as_map(
        self, connection: FeslConnection, request: GetRecord
    ) -> GetRecordAsMapAnswer:
        """Return a record's entries as a map.

        Args:
            connection: The FESL connection the request came on.
            request: The owner and the record's name.
        """
        record = await self._find(connection, request)
        return GetRecordAsMapAnswer(last_modified=record.modified, values=record.entries)

    async def write_record(self, connection: FeslConnection, request: WriteRecord) -> Message:
        """Set and remove entries of the session's own record, given as a list.

        Args:
            connection: The FESL connection the request came on.
            request: The record, its entries and the keys to remove.
        """
        values = {entry.key: entry.value for entry in request.values}
        await self._write(connection, request, values, request.remove)
        return Message()

    async def write_record_as_map(
        self, connection: FeslConnection, request: WriteRecordAsMap
    ) -> Message:
        """Set and remove entries of the session's own record, given as a map.

        Args:
            connection: The FESL connection the request came on.
            request: The record, its entries and the keys to remove.
        """
        await self._write(connection, request, request.values, request.remove)
        return Message()

    async def _find(self, connection: FeslConnection, request: GetRecord) -> Record:
        game = game_of(connection)
        owner = owner_of(connection, request.owner, request.owner_type)
        if owner.type != PERSONA:
            raise TransactionError(PARAMETERS_ERROR)
        record = await self._store.find(partition_of(game), owner.id, request.record_name)
        if record is None:
            raise TransactionError(RECORD_NOT_FOUND)
        return record

    async def _write(
        self,
        connection: FeslConnection,
        request: GetRecord,
        values: Mapping[str, str],
        removed: Sequence[str],
    ) -> None:
        game = game_of(connection)
        owner = owner_of(connection, request.owner, request.owner_type)
        size = game.records.get(request.record_name)
        if size is None:
            logger.warning(
                "Refusing record %r, which %s does not keep", request.record_name, game.name
            )
            raise TransactionError(PARAMETERS_ERROR)
        # Records are what a player's own game keeps, so nobody else changes them.
        playing = connection.session.persona
        if playing is None or owner != Owner(PERSONA, playing.id):
            raise TransactionError(PARAMETERS_ERROR)
        if not await self._store.write(
            partition_of(game), playing.id, request.record_name, values, removed, size
        ):
            raise TransactionError(PARAMETERS_ERROR)
