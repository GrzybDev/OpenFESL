from pydantic import BaseModel, ConfigDict


class RelationshipRules(BaseModel):
    """How a game keeps the relationships of its players' accounts that Magma lists.

    Attributes:
        friends: The list tag of friendships between accounts.
        vip: The list tag of the VIP lists that the game's dedicated servers keep of accounts;
            none for a game whose servers keep none.
        most: The most relationships on each of its lists that an account or a server asks
            for or accepts; requests it was sent take no room until it accepts them.
        web_namespace: The namespace that the game reads a player search's display names in,
            apart from heroes, which are in the game's own.
        search_minimum: The fewest characters besides wildcards that a player search names.
        search_limit: The most names that a player search answers.
    """

    model_config = ConfigDict(frozen=True)

    friends: str
    vip: str | None = None
    most: int
    web_namespace: str
    search_minimum: int
    search_limit: int
