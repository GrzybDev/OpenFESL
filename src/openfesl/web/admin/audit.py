from collections.abc import Callable, Collection
from dataclasses import dataclass, replace
from datetime import datetime
from math import ceil
from typing import Any, Protocol, Self
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.datastructures import QueryParams

from openfesl.core.admin import AdminAction, AuditEntry, AuditFilter, Auditor
from openfesl.core.games import Games
from openfesl.core.personas import Persona, PersonaRecord
from openfesl.core.servers import GameServer, Provider
from openfesl.core.texts import localize, translate
from openfesl.web.admin.auth import AdminSessions
from openfesl.web.admin.settings import policy_views
from openfesl.web.pages import moment

PAGE_SIZE = 50
# The glyph of each kind of action: what it takes away, gives, adds or changes.
GLYPHS = {
    AdminAction.ACCOUNT_VERIFIED: "tick",
    AdminAction.PLAYER_KICK: "x",
    AdminAction.SESSION_END: "x",
    AdminAction.LOGINS_FORGET: "x",
    AdminAction.PROVIDER_ADD: "plus",
    AdminAction.PROVIDER_REVOKE: "x",
    AdminAction.ENTITLEMENT_GRANT: "plus",
    AdminAction.KEY_ADD: "plus",
}
_TAKEN = {"disabled", "banned", "deactivated", "revoked", "DISABLED", "off"}


class AuditLog(Protocol):
    """Lists the admin's actions."""

    async def entries(
        self, chosen: AuditFilter, offset: int, limit: int
    ) -> tuple[int, list[AuditEntry]]:
        """Return the actions a filter lets through, newest first, with how many there are.

        Args:
            chosen: Which actions.
            offset: How many to skip.
            limit: The most to return.
        """

    async def auditors(self, chosen: AuditFilter) -> list[Auditor]:
        """Return who acted among the actions a filter lets through by anyone, most first.

        Args:
            chosen: Which actions; its admin is not applied.
        """


class Names(Protocol):
    """Names what the admin's actions were done to."""

    async def names(self, account_ids: Collection[int]) -> dict[int, str]:
        """Return the login names of some accounts, by number.

        Args:
            account_ids: The accounts.
        """


class PersonaNames(Protocol):
    """Finds personas by number."""

    async def by_ids(self, persona_ids: Collection[int]) -> dict[int, Persona]:
        """Return some personas by number, those their players removed too.

        Args:
            persona_ids: The personas' numbers.
        """

    async def records(self, account_ids: list[int]) -> dict[int, list[PersonaRecord]]:
        """Return the personas of some accounts, by account.

        Args:
            account_ids: The accounts.
        """


class ServerNames(Protocol):
    """Lists the providers and dedicated servers."""

    async def providers(self) -> list[Provider]:
        """Return every provider, oldest first."""

    async def all(self) -> list[GameServer]:
        """Return every server that ever hosted, oldest first."""


@dataclass(frozen=True, slots=True)
class AuditRow:
    """One of the admin's actions as the audit log's row shows it.

    Attributes:
        entry: The action.
        glyph: The glyph of its kind.
        title: What was done, in words.
        target: What it was done to, in words.
        href: The panel's page of what it was done to, if it has one.
        code: Whether the target is a code, shown in the monospaced font.
        details: What changed, in words.
    """

    entry: AuditEntry
    glyph: str
    title: str
    target: str
    href: str | None
    code: bool
    details: str


@dataclass(frozen=True, slots=True)
class AuditList:
    """Which actions the audit log lists, as its address says.

    Attributes:
        admin: The subject of the admin whose actions are listed; empty for the command line.
        account: The account whose actions are listed, with its personas'.
        page: The page of the list, from 1.
    """

    admin: str | None = None
    account: int | None = None
    page: int = 1

    @classmethod
    def read(cls, query: QueryParams) -> Self:
        """Read the list's address; what it does not name is the default.

        Args:
            query: The address's query.
        """
        account, page = query.get("account", ""), query.get("page", "")
        return cls(
            admin=query.get("admin"),
            account=int(account) if account.isdigit() else None,
            page=int(page) if page.isdigit() and int(page) > 0 else 1,
        )

    def changed(self, **changes: Any) -> AuditList:
        """Return the list with some choices changed.

        Args:
            changes: The new values by attribute.
        """
        return replace(self, **changes)

    def query(self) -> str:
        """Return the query of the list's address, with a leading ? unless it is empty."""
        fields = [
            ("admin", self.admin),
            ("account", str(self.account) if self.account is not None else None),
            ("page", str(self.page) if self.page > 1 else None),
        ]
        query = urlencode([(key, value) for key, value in fields if value is not None])
        return f"?{query}" if query else ""


def audit_routes(
    sessions: AdminSessions,
    games: Games,
    log: AuditLog,
    accounts: Names,
    personas: PersonaNames,
    servers: ServerNames,
) -> APIRouter:
    """Return the audit log: the changes made in the panel and on the command line.

    It lists them newest first, by admin or by account.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        games: The games, whose policies the settings name.
        log: Lists the admin's actions.
        accounts: Names the accounts.
        personas: Names the personas.
        servers: Names the providers and servers.
    """
    router = APIRouter()

    @router.get("/admin/audit", response_class=HTMLResponse)
    async def audit(request: Request) -> HTMLResponse:
        admin = sessions.admin(request)
        listing = AuditList.read(request.query_params)
        owned: tuple[int, ...] = ()
        if listing.account is not None:
            found = await personas.records([listing.account])
            owned = tuple(record.persona.id for record in found[listing.account])
        chosen = AuditFilter(listing.admin, listing.account, owned)
        total, entries = await log.entries(chosen, (listing.page - 1) * PAGE_SIZE, PAGE_SIZE)
        rows = await _rows(request.state.language, games, entries, accounts, personas, servers)
        named = await accounts.names([listing.account]) if listing.account is not None else {}
        return await sessions.render(
            request,
            "admin.Audit",
            admin=admin,
            listing=listing,
            rows=rows,
            total=total,
            pages=ceil(total / PAGE_SIZE),
            size=PAGE_SIZE,
            auditors=await log.auditors(chosen),
            account=named.get(listing.account) if listing.account is not None else None,
        )

    return router


async def _rows(
    language: str,
    games: Games,
    entries: list[AuditEntry],
    accounts: Names,
    personas: PersonaNames,
    servers: ServerNames,
) -> list[AuditRow]:
    def say(name: str, /, **values: Any) -> str:
        text = translate(name, language)
        return text % values if values else text

    def page(path: str) -> str:
        return localize(path, language)

    def account(number: int) -> str:
        if number in names:
            return say("web.admin.audit.account", number=number, name=names[number])
        return say("web.admin.account.number", number=number)

    numbers = {int(entry.target) for entry in entries if entry.target.isdigit()}
    persona_list = await personas.by_ids(numbers)
    names = await accounts.names(
        numbers | {persona.account_id for persona in persona_list.values()}
    )
    providers = {provider.id.hex(): provider.name for provider in await servers.providers()}
    hosted = {server.ugid: server.name for server in await servers.all()}
    rows = []
    for entry in entries:
        action, target, details = entry.action, entry.target, entry.details
        kind = action.value
        shown, href, code, said = target, None, False, ""
        if action in (
            AdminAction.ACCOUNT_STATE,
            AdminAction.ACCOUNT_VERIFIED,
            AdminAction.SESSION_END,
            AdminAction.LOGINS_FORGET,
            AdminAction.ENTITLEMENT_GRANT,
            AdminAction.ENTITLEMENT_STATE,
        ):
            number = int(target) if target.isdigit() else -1
            shown = account(number)
            href = page(f"/admin/accounts/{target}")
            if action is AdminAction.ACCOUNT_STATE:
                kind = f"account_state.{details.get('state')}"
                if details.get("until"):
                    until = moment(datetime.fromisoformat(details["until"]), language)
                    said = say("web.admin.audit.until", time=until)
            elif action is AdminAction.LOGINS_FORGET:
                said = say("web.admin.audit.logins", count=details.get("count", 0))
            elif action is AdminAction.ENTITLEMENT_GRANT:
                said = ", ".join(details.get("tags", []))
                if details.get("key"):
                    said = say("web.admin.audit.from_key", tags=said, key=details["key"])
            elif action is AdminAction.ENTITLEMENT_STATE:
                taken = details.get("status") != "ACTIVE"
                kind = f"entitlement_state.{'revoked' if taken else 'active'}"
                said = str(details.get("tag", ""))
        elif action in (AdminAction.PERSONA_STATE, AdminAction.PLAYER_KICK):
            persona = persona_list.get(int(target)) if target.isdigit() else None
            if persona is not None:
                shown = persona.name
                href = page(f"/admin/accounts/{persona.account_id}")
                said = account(persona.account_id)
            if action is AdminAction.PERSONA_STATE:
                kind = f"persona_state.{details.get('state')}"
        elif action in (AdminAction.PROVIDER_ADD, AdminAction.PROVIDER_REVOKE):
            shown = providers.get(target, str(details.get("name", target)))
            href = page(f"/admin/providers/{target}")
            said = say("web.admin.audit.provider_id", id=target[:8].upper())
        elif action is AdminAction.SERVER_STATE:
            shown = hosted.get(target, target)
            href = page(f"/admin/servers/{target}")
            kind = f"server_state.{details.get('state')}"
        elif action in (
            AdminAction.KEY_ADD,
            AdminAction.KEY_STATE,
            AdminAction.KEY_GRANTS,
            AdminAction.KEY_LIMITS,
        ):
            code = True
            href = page(f"/admin/keys?{urlencode({'open': target})}#key-{quote(target)}")
            if action is AdminAction.KEY_STATE:
                kind = f"key_state.{'enabled' if details.get('enabled') else 'disabled'}"
            elif action is AdminAction.KEY_ADD:
                said = say(
                    "web.admin.audit.key." + ("starting" if details.get("starting") else "here")
                )
            elif action is AdminAction.KEY_GRANTS:
                said = ", ".join(details.get("tags", [])) or say("web.admin.audit.nothing")
            else:
                most = details.get("max_uses")
                said = say("web.admin.audit.limits." + ("most" if most else "any"), most=most or 0)
                if details.get("once_per_account"):
                    said += " · " + say("web.admin.key.uses.once")
        elif action is AdminAction.SETTING:
            shown, href = _setting(say, page, games, target)
            said = _setting_value(say, games, target, details)
            if target == "hosting_mode":
                kind = "hosting_mode"
        rows.append(
            AuditRow(
                entry,
                _glyph(action, kind, details),
                say(f"web.admin.audit.action.{kind}"),
                shown,
                href,
                code,
                said,
            )
        )
    return rows


def _glyph(action: AdminAction, kind: str, details: dict[str, Any]) -> str:
    if action in GLYPHS:
        return GLYPHS[action]
    state = kind.rpartition(".")[2]
    if action in (AdminAction.SETTING, AdminAction.KEY_GRANTS, AdminAction.KEY_LIMITS):
        return "edit"
    return "x" if state in _TAKEN else "tick"


def _setting(
    say: Callable[..., str], page: Callable[[str], str], games: Games, key: str
) -> tuple[str, str]:
    if key == "hosting_mode":
        return say("web.admin.menu.settings"), page("/admin/settings")
    game_id, _, name = key.partition(".")
    game = games.by_id(game_id)
    label = say(f"web.admin.policy.{name}") if game and name in game.policies.names() else key
    shown = f"{game.name} · {label}" if game else key
    return shown, page(f"/admin/settings#game-{game_id}")


def _setting_value(say: Callable[..., str], games: Games, key: str, details: dict[str, Any]) -> str:
    value = details.get("value")
    if key == "hosting_mode":
        now = say(f"web.host.mode.{value}")
        if earlier := details.get("from"):
            return say("web.admin.audit.from", earlier=say(f"web.host.mode.{earlier}"), now=now)
        return now
    if value is None:
        return say("web.admin.audit.reset")
    game_id, _, name = key.partition(".")
    game = games.by_id(game_id)
    views = {view.name: view for view in policy_views(game, game.policies)} if game else {}
    match views.get(name):
        case None:
            return str(value)
        case view if view.kind == "switch":
            return say(f"web.admin.policy.value.{value}")
        case view if view.kind == "choice":
            return say(f"web.admin.policy.{name}.{value}")
        case _:
            return str(value) or say("web.admin.policy.version.any")
