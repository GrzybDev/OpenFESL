from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.storage.accounts import SqlAccountStore
from openfesl.storage.admin import SqlAdminLog
from openfesl.storage.associations import SqlAssociationStore
from openfesl.storage.boxes import SqlBoxStore
from openfesl.storage.catalogues import SqlCatalogueStore
from openfesl.storage.deal_offers import SqlOfferStore
from openfesl.storage.deal_uploads import SqlDealUploads
from openfesl.storage.deals import SqlDealStore
from openfesl.storage.entitlements import SqlEntitlementStore
from openfesl.storage.game_art import SqlArtStore
from openfesl.storage.game_icons import SqlGameIconStore
from openfesl.storage.game_marks import SqlMarkStore
from openfesl.storage.hero_admin import SqlHeroAdmin
from openfesl.storage.hero_boards import SqlHeroBoards
from openfesl.storage.heroes import SqlHeroStore
from openfesl.storage.history import SqlHistoryStore
from openfesl.storage.identities import SqlIdentityStore
from openfesl.storage.keys import SqlKeyStore
from openfesl.storage.ledgers import SqlLedgerStore
from openfesl.storage.messages import SqlMessageStore
from openfesl.storage.mugshots import SqlMugshotStore
from openfesl.storage.personas import SqlPersonaStore
from openfesl.storage.purchases import SqlPurchaseStore
from openfesl.storage.records import SqlRecordStore
from openfesl.storage.relationships import SqlRelationshipStore
from openfesl.storage.rewards import SqlRewardStore
from openfesl.storage.servers import SqlServerStore
from openfesl.storage.settings import SqlSettingStore
from openfesl.storage.stats import SqlStatStore
from openfesl.storage.store import SqlProductStore
from openfesl.storage.store_prices import SqlPriceStore
from openfesl.storage.wallets import SqlWalletStore


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
        self.catalogues = SqlCatalogueStore(sessions)
        self.entitlements = SqlEntitlementStore(sessions)
        self.game_art = SqlArtStore(sessions)
        self.game_icons = SqlGameIconStore(sessions)
        self.game_marks = SqlMarkStore(sessions)
        self.heroes = SqlHeroStore(sessions)
        self.hero_admin = SqlHeroAdmin(sessions)
        self.hero_boards = SqlHeroBoards(sessions)
        self.history = SqlHistoryStore(sessions)
        self.identities = SqlIdentityStore(sessions)
        self.keys = SqlKeyStore(sessions)
        self.ledgers = SqlLedgerStore(sessions)
        self.messages = SqlMessageStore(sessions)
        self.mugshots = SqlMugshotStore(sessions)
        self.personas = SqlPersonaStore(sessions)
        self.records = SqlRecordStore(sessions)
        self.relationships = SqlRelationshipStore(sessions)
        self.rewards = SqlRewardStore(sessions)
        self.servers = SqlServerStore(sessions)
        self.settings = SqlSettingStore(sessions)
        self.stats = SqlStatStore(sessions)
        self.wallets = SqlWalletStore(sessions)
        self.products = SqlProductStore(sessions)
        self.prices = SqlPriceStore(sessions)
        self.boxes = SqlBoxStore(sessions)
        self.deal_uploads = SqlDealUploads(sessions)
        self.purchases = SqlPurchaseStore(sessions)
        self.deals = SqlDealStore(sessions)
        self.offers = SqlOfferStore(sessions)
