from fastapi import FastAPI

from openfesl.core.admins import GroupRoles
from openfesl.core.games import Games
from openfesl.server.services import GameServices
from openfesl.server.settings import Settings
from openfesl.server.stores import Stores
from openfesl.web.admin.account_decisions import account_decision_routes
from openfesl.web.admin.accounts import AccountViews, account_routes
from openfesl.web.admin.audit import audit_routes
from openfesl.web.admin.auth import AdminSessions, NeedsRole, SignInNeeded, SignInProvider
from openfesl.web.admin.box_tables import box_table_routes
from openfesl.web.admin.boxes import BoxAdminViews, box_routes
from openfesl.web.admin.catalogues import catalogue_routes
from openfesl.web.admin.deal_pictures import deal_picture_routes
from openfesl.web.admin.deals import DealAdminViews, deal_routes
from openfesl.web.admin.entitlements import entitlement_routes
from openfesl.web.admin.hero_actions import hero_action_routes
from openfesl.web.admin.hero_counts import hero_count_routes
from openfesl.web.admin.hero_items import hero_item_routes
from openfesl.web.admin.heroes import HeroAdminViews, hero_routes
from openfesl.web.admin.home import home_routes
from openfesl.web.admin.key_edit import key_edit_routes
from openfesl.web.admin.keys import key_routes
from openfesl.web.admin.providers import provider_routes
from openfesl.web.admin.reward_changes import reward_change_routes
from openfesl.web.admin.reward_claims import reward_claim_routes
from openfesl.web.admin.reward_events import RewardEventViews, reward_event_routes
from openfesl.web.admin.reward_preview import reward_preview_routes
from openfesl.web.admin.rewards import RewardAdminViews, reward_routes
from openfesl.web.admin.servers import (
    ServerViews,
    decision_routes,
    official_routes,
    server_list_routes,
)
from openfesl.web.admin.settings import settings_routes
from openfesl.web.admin.store import StoreAdminViews, store_routes
from openfesl.web.admin.store_item import store_item_routes
from openfesl.web.admin.store_rules import store_rule_routes
from openfesl.web.admin.wallets import WalletAdminViews, wallet_routes
from openfesl.web.deal_art import DealArt
from openfesl.web.pages import Pages
from openfesl.web.sessions import FormExpired


def include_admin_routes(
    app: FastAPI,
    settings: Settings,
    games: Games,
    pages: Pages,
    services: GameServices,
    stores: Stores,
    deal_art: DealArt,
) -> None:
    """Add the admin panel's pages and the answers to requests its sign-in turns away.

    Args:
        app: The application to add them to.
        settings: The deployment's settings, which name the sign-in provider.
        games: The games the server serves.
        pages: Renders the pages.
        services: The games' side of the server, which the panel shows and changes.
        stores: The database's stores.
        deal_art: Draws the pictures of the hot deals, or gives the uploaded ones.
    """
    census, players, apply = services.census, services.players, services.trust.apply_decisions
    server_views = ServerViews(
        games, census, services.approval, stores.servers, services.game_marks
    )
    sections = frozenset(
        name
        for name, served in (
            ("catalogues", any(game.catalogue for game in games)),
            ("store", any(game.store and game.heroes for game in games)),
            ("rewards", any(game.rewards for game in games)),
        )
        if served
    )
    store_views = StoreAdminViews(
        games, services.catalogues, stores.prices, stores.settings, services.feed.apply
    )
    box_views = BoxAdminViews(store_views, services.catalogues, services.deals, stores.boxes)
    deal_views = DealAdminViews(
        store_views, services.deals, stores.settings, deal_art, stores.deal_uploads
    )
    reward_views = RewardAdminViews(
        games, services.rewards, services.catalogues, stores.settings, stores.admin_log
    )

    async def menu_counts() -> dict[str, int]:
        counts = await server_views.menu_counts()
        if attention := await box_views.flagged() + len(await deal_views.short()):
            counts["store"] = attention
        if paused := await reward_views.paused():
            counts["rewards"] = len(paused)
        return counts

    sign_in = AdminSessions(
        pages, _sign_in_provider(settings), settings.public_url, menu_counts, sections
    )
    app.include_router(sign_in.routes())
    app.include_router(
        home_routes(
            sign_in,
            games,
            census,
            players,
            stores.accounts,
            server_views,
            box_views,
            deal_views,
            reward_views,
        )
    )
    app.include_router(server_list_routes(sign_in, server_views))
    app.include_router(decision_routes(sign_in, server_views, apply))
    app.include_router(official_routes(sign_in, server_views, apply))
    app.include_router(provider_routes(sign_in, games, server_views, apply))
    account_views = AccountViews(games, census, players, stores.accounts, stores.personas)
    hero_views = HeroAdminViews(
        games,
        services.heroes,
        services.wallets,
        stores.entitlements,
        services.catalogues,
        stores.hero_admin,
    )
    app.include_router(
        account_routes(
            sign_in, account_views, stores.entitlements, hero_views, services.terms.version
        )
    )
    app.include_router(
        hero_routes(
            sign_in,
            account_views,
            hero_views,
            services.catalogues,
            services.catalogue_files,
            services.mugshots,
        )
    )
    # Before the hero's other changes, whose path takes any change's name.
    app.include_router(hero_count_routes(sign_in, account_views, hero_views))
    app.include_router(hero_action_routes(sign_in, account_views, hero_views, services.feed.apply))
    app.include_router(hero_item_routes(sign_in, account_views, hero_views))
    wallet_views = WalletAdminViews(games, services.heroes, services.wallets, stores.ledgers)
    app.include_router(wallet_routes(sign_in, account_views, wallet_views, services.catalogues))
    app.include_router(
        entitlement_routes(sign_in, account_views, stores.entitlements, stores.keys, games)
    )
    app.include_router(key_routes(sign_in, games, stores.keys))
    app.include_router(key_edit_routes(sign_in, games, stores.keys))
    app.include_router(
        catalogue_routes(
            sign_in,
            games,
            services.catalogues,
            stores.admin_log,
            services.catalogue_files is not None,
            services.feed.apply,
            services.game_art,
            services.mugshots,
        )
    )
    app.include_router(store_routes(sign_in, store_views))
    app.include_router(store_item_routes(sign_in, store_views))
    app.include_router(store_rule_routes(sign_in, store_views))
    app.include_router(box_routes(sign_in, box_views))
    app.include_router(box_table_routes(sign_in, box_views))
    app.include_router(deal_routes(sign_in, deal_views))
    app.include_router(deal_picture_routes(sign_in, deal_views))
    app.include_router(reward_routes(sign_in, reward_views))
    app.include_router(reward_change_routes(sign_in, reward_views, stores.rewards))
    event_views = RewardEventViews(reward_views, box_views, stores.rewards)
    app.include_router(reward_event_routes(sign_in, event_views))
    app.include_router(reward_preview_routes(sign_in, reward_views))
    app.include_router(reward_claim_routes(sign_in, reward_views, stores.rewards))
    app.include_router(
        audit_routes(
            sign_in,
            games,
            stores.admin_log,
            stores.accounts,
            stores.personas,
            stores.servers,
            services.catalogues,
        )
    )
    app.include_router(
        settings_routes(
            sign_in,
            games,
            services.policies,
            stores.settings,
            stores.servers,
            server_views,
            apply,
            sign_in.provider,
            stores.game_icons,
            settings.public_url,
            services.feed.apply,
            services.game_marks,
            stores.admin_log,
        )
    )
    app.include_router(
        account_decision_routes(
            sign_in,
            account_views,
            stores.accounts,
            stores.personas,
            stores.admin_log,
            services.feed.apply,
        )
    )
    app.add_exception_handler(SignInNeeded, sign_in.sign_in_needed)
    app.add_exception_handler(FormExpired, sign_in.form_expired)
    app.add_exception_handler(NeedsRole, sign_in.needs_role)


def _sign_in_provider(settings: Settings) -> SignInProvider | None:
    secret = settings.admin_oidc_client_secret
    if not (settings.admin_oidc_issuer and settings.admin_oidc_client_id and secret):
        return None
    return SignInProvider(
        settings.admin_oidc_name,
        settings.admin_oidc_issuer,
        settings.admin_oidc_client_id,
        secret.get_secret_value(),
        GroupRoles(settings.admin_group, settings.moderator_group),
    )
