import asyncio
import secrets
import shutil
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin
from openfesl.core.catalogue import CatalogueDocument
from openfesl.core.catalogues import StoredCatalogue
from openfesl.storage.admin import action_row
from openfesl.storage.models import CatalogueRow, CatalogueTextRow


class SqlCatalogueStore:
    """The games' imported catalogues in PostgreSQL, one document and its texts per game."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def current(self, partition: str) -> StoredCatalogue | None:
        """Return a game's catalogue without its texts, if it was imported.

        Args:
            partition: The game's partition.

        Raises:
            ValueError: When the kept catalogue is of a layout this server does not read.
        """
        async with self._sessions() as session:
            row = await session.get(CatalogueRow, partition)
            if row is None:
                return None
            languages = await session.scalars(
                select(CatalogueTextRow.language)
                .where(CatalogueTextRow.partition == partition)
                .order_by(CatalogueTextRow.language)
            )
            document = CatalogueDocument.model_validate(row.document)
            return StoredCatalogue(
                document, row.revision, row.icons, row.models, row.textures, tuple(languages)
            )

    async def texts(self, partition: str, language: str) -> dict[str, str]:
        """Return the texts of a game's catalogue in a language by id; empty without any.

        Args:
            partition: The game's partition.
            language: The language code.
        """
        async with self._sessions() as session:
            texts = await session.scalar(
                select(CatalogueTextRow.texts).where(
                    CatalogueTextRow.partition == partition, CatalogueTextRow.language == language
                )
            )
            return texts or {}

    async def replace(
        self,
        partition: str,
        game: str,
        catalogue: StoredCatalogue,
        texts: Mapping[str, Mapping[str, str]],
        admin: Admin | None,
    ) -> None:
        """Replace a game's catalogue and all its texts at once, and record the import.

        Args:
            partition: The game's partition.
            game: The game's data-file name, which the admin's log names.
            catalogue: The new catalogue.
            texts: Its texts by language code, each by id.
            admin: The admin who imported it; None for the command line.
        """
        document = catalogue.document
        details = {
            "items": len(document.items),
            "languages": len(texts),
            "icons": catalogue.icons,
            "models": catalogue.models,
            "textures": catalogue.textures,
        }
        async with self._sessions.begin() as session:
            # The texts go with the catalogue's row.
            await session.execute(delete(CatalogueRow).where(CatalogueRow.partition == partition))
            session.add(
                CatalogueRow(
                    partition=partition,
                    document=document.model_dump(mode="json", exclude_defaults=True),
                    revision=catalogue.revision,
                    icons=catalogue.icons,
                    models=catalogue.models,
                    textures=catalogue.textures,
                )
            )
            await session.flush()
            session.add_all(
                CatalogueTextRow(partition=partition, language=language, texts=dict(found))
                for language, found in texts.items()
            )
            session.add(action_row(AdminAction.CATALOGUE_IMPORT, game, details, admin))

    @asynccontextmanager
    async def importing(self, partition: str) -> AsyncIterator[None]:
        """Hold off other imports of a game's catalogue, on any server, until the block ends.

        Args:
            partition: The game's partition.
        """
        async with self._sessions.begin() as session:
            # The lock ends with this transaction, also when the import fails or is cancelled.
            key = func.hashtext(f"catalogue:{partition}")
            await session.execute(select(func.pg_advisory_xact_lock(key)))
            yield


class CatalogueFolder:
    """The files of the games' imported catalogues, in a folder per game and import."""

    def __init__(self, path: Path) -> None:
        """Keep the files in a folder of the deployment's files.

        Args:
            path: The folder where the deployment keeps files.
        """
        self._path = path / "catalogues"

    async def write(self, game: str, revision: str, files: Mapping[str, bytes]) -> None:
        """Keep the files of an import.

        Args:
            game: The game's data-file name.
            revision: The import.
            files: The files, by their paths.
        """
        await asyncio.to_thread(self._write, self._path / game / revision, files)

    async def drop(self, game: str, revision: str) -> None:
        """Remove the files of an import.

        Args:
            game: The game's data-file name.
            revision: The import.
        """
        await asyncio.to_thread(shutil.rmtree, self._path / game / revision, ignore_errors=True)

    async def keep_only(self, game: str, revision: str) -> None:
        """Remove the files of a game's other imports.

        Args:
            game: The game's data-file name.
            revision: The import whose files stay.
        """
        await asyncio.to_thread(self._keep_only, self._path / game, revision)

    async def located(self, game: str, revision: str, path: str) -> Path | None:
        """Return where a file of an import lies, if it is kept.

        Args:
            game: The game's data-file name.
            revision: The import.
            path: The file's path in the import's folder.
        """
        found = self._path / game / revision / path
        return found if await asyncio.to_thread(found.is_file) else None

    @staticmethod
    def _write(folder: Path, files: Mapping[str, bytes]) -> None:
        for path, data in files.items():
            target = folder / path
            target.parent.mkdir(parents=True, exist_ok=True)
            # A file appears whole, as a page may ask for one while it is written.
            part = target.with_name(f"{target.name}.{secrets.token_hex(4)}")
            part.write_bytes(data)
            part.replace(target)

    @staticmethod
    def _keep_only(folder: Path, revision: str) -> None:
        if not folder.is_dir():
            return
        for kept in folder.iterdir():
            if kept.name != revision:
                shutil.rmtree(kept, ignore_errors=True)
