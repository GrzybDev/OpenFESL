from pydantic import BaseModel, ConfigDict


class RelationshipRules(BaseModel):
    """How a game keeps the relationships between its players' accounts that Magma lists.

    Attributes:
        friends: The list tag of friendships between accounts.
        most: The most relationships on each of its lists that an account asks for or
            accepts; requests it was sent take no room until it accepts them.
    """

    model_config = ConfigDict(frozen=True)

    friends: str
    most: int
