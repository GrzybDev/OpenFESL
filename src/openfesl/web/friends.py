from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from openfesl.core.games import Game, Games
from openfesl.core.heroes import Hero, Heroes
from openfesl.core.hosting import GAME, Hosting
from openfesl.core.relationships import Party, Relationship, RelationshipRefused, Relationships
from openfesl.core.session import Players
from openfesl.core.texts import localize
from openfesl.web.account.session import signed_in
from openfesl.web.pages import Pages
from openfesl.web.sessions import form_matches, form_token, inside, toast

FRIENDS = "/friends"
# What the friends page's form does with a relationship, and to which list.
ASK, ACCEPT, DECLINE, CANCEL, REMOVE, LEAVE = (
    "ask",
    "accept",
    "decline",
    "cancel",
    "remove",
    "leave",
)


@dataclass(frozen=True, slots=True)
class Friend:
    """Another account on one of the account's lists, as the friends page shows it.

    Attributes:
        relationship: The relationship as the account sees it.
        hero: The other account's default hero, once it has one.
        playing: The server it plays a match on, or empty in the game's menus; none while its
            game is not logged in.
    """

    relationship: Relationship
    hero: Hero | None
    playing: str | None


class FriendsPages:
    """The friends page of a game's site: requests, friends and the servers' VIP lists."""

    def __init__(
        self,
        pages: Pages,
        games: Games,
        relationships: Relationships,
        heroes: Heroes,
        players: Players,
        hosting: Hosting,
    ) -> None:
        """Prepare the friends page of each game that lists it.

        Args:
            pages: Renders the pages.
            games: The games, of which those listing the page get it.
            relationships: Keeps the accounts' friends and the servers' VIP lists.
            heroes: Name the default hero of each friend.
            players: The games logged in, which say who plays now.
            hosting: The games hosted, which name the server a friend plays on.
        """
        self._pages = pages
        self._games = games
        self._relationships = relationships
        self._heroes = heroes
        self._players = players
        self._hosting = hosting

    def routes(self) -> APIRouter:
        """Return the friends page of each game that lists it, its form and its removal page."""
        router = APIRouter()
        for game in self._games.with_page("friends"):
            path = f"/{game.id}{FRIENDS}"
            router.add_api_route(path, self._page(game), response_class=HTMLResponse)
            router.add_api_route(path, self._change(game), methods=["POST"])
            router.add_api_route(
                path + "/{account_id}/remove", self._removal(game), response_class=HTMLResponse
            )
        return router

    def _page(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def show_friends(request: Request) -> Response:
            player = signed_in(request)
            friends = await self._friends(game, player.account_id) if player else []
            accounts = [f for f in friends if f.relationship.member.account_id is not None]
            return self._pages.render(
                request,
                "friends.Friends",
                game=game,
                signed=player is not None,
                incoming=[f for f in accounts if f.relationship.theirs and not f.relationship.own],
                outgoing=[f for f in accounts if f.relationship.own and not f.relationship.theirs],
                friends=[f for f in accounts if f.relationship.mutual],
                # The servers that keep the account on their VIP lists.
                vip=[f for f in friends if f.relationship.member.server and f.relationship.theirs],
                next=localize(f"/{game.id}{FRIENDS}", request.state.language),
                csrf=form_token(request) if player else "",
            )

        return show_friends

    def _removal(self, game: Game) -> Callable[[Request, int], Awaitable[Response]]:
        async def show_removal(request: Request, account_id: int) -> Response:
            # The removal's question as a page of its own, where scripts ask it in a dialog.
            player = signed_in(request)
            page = localize(f"/{game.id}{FRIENDS}", request.state.language)
            friends = await self._friends(game, player.account_id) if player else []
            friend = next(
                (f for f in friends if f.relationship.member.account_id == account_id), None
            )
            if friend is None or not friend.relationship.mutual:
                return RedirectResponse(page, 303)
            return self._pages.render(
                request, "friends.Remove", game=game, friend=friend, csrf=form_token(request)
            )

        return show_removal

    def _change(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def change(request: Request) -> Response:
            form = await request.form()
            page = inside(str(form.get("next", "")), ("/",))
            page = page or localize(f"/{game.id}{FRIENDS}", request.state.language)
            player = signed_in(request)
            if player is None:
                return RedirectResponse(page, 303)
            if not form_matches(request, form):
                toast(request, "web.friends.refused.form", error=True)
                return RedirectResponse(page, 303)
            action, name = str(form.get("action", "")), str(form.get("name", ""))
            account = str(form.get("account", ""))
            server = str(form.get("server", "")) or None
            member = Party(int(account) if account.isdecimal() else None, server)
            rules = game.relationships
            assert rules is not None
            tag = (rules.vip if action == LEAVE else rules.friends) or ""
            me = Party(player.account_id)
            roster = await self._relationships.roster(game, me)
            known = next((r for r in roster if (r.member, r.tag) == (member, tag)), None)
            own, theirs = (known.own, known.theirs) if known else (False, False)
            if action == ASK and own:
                key = "already" if theirs else "waiting"
                toast(request, f"web.friends.refused.{key}", error=True, name=name)
                return RedirectResponse(page, 303)
            # What each action finds as the page showed it; another tab or the game may have
            # changed it since, and then nothing changes.
            expected = {
                ASK: True,
                ACCEPT: theirs and not own,
                DECLINE: theirs and not own,
                CANCEL: own and not theirs,
                REMOVE: own and theirs,
                LEAVE: theirs,
            }
            if action not in expected:
                return RedirectResponse(page, 303)
            if not expected[action]:
                toast(request, "web.friends.refused.form", error=True)
                return RedirectResponse(page, 303)
            try:
                if action in (ASK, ACCEPT):
                    await self._relationships.increase(game, me, member, tag)
                else:
                    await self._relationships.decrease(game, me, member, tag)
            except RelationshipRefused as refused:
                key = f"web.friends.refused.{refused.refusal.name.lower()}"
                toast(request, key, error=True, name=name, most=str(rules.most))
                return RedirectResponse(page, 303)
            # Asking an account that asked already accepts its request.
            done = ACCEPT if action == ASK and theirs else action
            toast(request, f"web.friends.done.{done}", name=name, game=game.name)
            return RedirectResponse(page, 303)

        return change

    async def _friends(self, game: Game, account_id: int) -> list[Friend]:
        relationships = await self._relationships.roster(game, Party(account_id))
        others = [r.member.account_id for r in relationships if r.member.account_id is not None]
        heroes = await self._heroes.defaults(game, others)
        found = []
        for relationship in relationships:
            other = relationship.member.account_id
            hero = heroes.get(other) if other is not None else None
            found.append(Friend(relationship, hero, self._playing(game, relationship)))
        return found

    def _playing(self, game: Game, relationship: Relationship) -> str | None:
        # Where a friend plays: a match's server, or empty in the game's menus. Only friends
        # who both agreed see it.
        other = relationship.member.account_id
        session = self._players.online(other) if other and relationship.mutual else None
        if session is None or session.game is not game:
            return None
        persona = session.persona
        hosted = self._hosting.find_by_player(game, GAME, None, persona.id) if persona else None
        return hosted[0].name if hosted else ""
