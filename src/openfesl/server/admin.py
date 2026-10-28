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
from openfesl.web.admin.entitlements import entitlement_routes
from openfesl.web.admin.home import home_routes
from openfesl.web.admin.key_edit import key_edit_routes
from openfesl.web.admin.keys import key_routes
from openfesl.web.admin.providers import provider_routes
from openfesl.web.admin.servers import ServerViews, decision_routes, server_list_routes
from openfesl.web.admin.settings import settings_routes
from openfesl.web.pages import Pages
from openfesl.web.sessions import FormExpired


def include_admin_routes(
    app: FastAPI,
    settings: Settings,
    games: Games,
    pages: Pages,
    services: GameServices,
    stores: Stores,
) -> None:
    """Add the admin panel's pages and the answers to requests its sign-in turns away.

    Args:
        app: The application to add them to.
        settings: The deployment's settings, which name the sign-in provider.
        games: The games the server serves.
        pages: Renders the pages.
        services: The games' side of the server, which the panel shows and changes.
        stores: The database's stores.
    """
    census, players, apply = services.census, services.players, services.trust.apply_decisions
    server_views = ServerViews(games, census, services.approval, stores.servers)
    sign_in = AdminSessions(
        pages, _sign_in_provider(settings), settings.public_url, server_views.menu_counts
    )
    app.include_router(sign_in.routes())
    app.include_router(home_routes(sign_in, games, census, players, stores.accounts, server_views))
    app.include_router(server_list_routes(sign_in, server_views))
    app.include_router(decision_routes(sign_in, server_views, apply))
    app.include_router(provider_routes(sign_in, games, server_views, apply))
    account_views = AccountViews(games, census, players, stores.accounts, stores.personas)
    app.include_router(
        account_routes(sign_in, account_views, stores.entitlements, services.terms.version)
    )
    app.include_router(
        entitlement_routes(sign_in, account_views, stores.entitlements, stores.keys, games)
    )
    app.include_router(key_routes(sign_in, games, stores.keys))
    app.include_router(key_edit_routes(sign_in, games, stores.keys))
    app.include_router(
        audit_routes(
            sign_in, games, stores.admin_log, stores.accounts, stores.personas, stores.servers
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
