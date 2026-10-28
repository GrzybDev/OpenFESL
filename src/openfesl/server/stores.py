from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.storage.accounts import SqlAccountStore
from openfesl.storage.admin import SqlAdminLog
from openfesl.storage.associations import SqlAssociationStore
from openfesl.storage.entitlements import SqlEntitlementStore
from openfesl.storage.game_icons import SqlGameIconStore
from openfesl.storage.history import SqlHistoryStore
from openfesl.storage.keys import SqlKeyStore
from openfesl.storage.messages import SqlMessageStore
from openfesl.storage.personas import SqlPersonaStore
from openfesl.storage.records import SqlRecordStore
from openfesl.storage.servers import SqlServerStore
from openfesl.storage.settings import SqlSettingStore
from openfesl.storage.stats import SqlStatStore


class Stores:
    """The database's stores, each built once for the services and pages that share it."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Build every store over the database's sessions.

        Args:
            sessions: Opens database sessions.
        """
        self.accounts = SqlAccountStore(sessions)
        self.admin_log = SqlAdminLog(sessions)
        self.associations = SqlAssociationStore(sessions)
        self.entitlements = SqlEntitlementStore(sessions)
        self.game_icons = SqlGameIconStore(sessions)
        self.history = SqlHistoryStore(sessions)
        self.keys = SqlKeyStore(sessions)
        self.messages = SqlMessageStore(sessions)
        self.personas = SqlPersonaStore(sessions)
        self.records = SqlRecordStore(sessions)
        self.servers = SqlServerStore(sessions)
        self.settings = SqlSettingStore(sessions)
        self.stats = SqlStatStore(sessions)
