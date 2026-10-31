from pydantic import BaseModel, ConfigDict, PositiveInt

from openfesl.core.store_rules import BoxRule, Tier

# The key of the store's box whose drops are the Claw's prizes.
CLAW = "claw"


class PaidGrabs(BaseModel):
    """The grabs an account buys after its free ones.

    Attributes:
        enabled: Whether they are sold.
        price: What one costs.
        currency: The account's currency it is paid in.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    price: PositiveInt
    currency: str


class ClawFeed(BaseModel):
    """The list of the latest rare prizes that the Claw shows.

    Attributes:
        enabled: Whether it is shown.
        length: How many prizes it lists.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    length: PositiveInt = 20


class ClawRules(BaseModel):
    """The Daily Claw of a game's site: a claw machine whose prizes go to an account's heroes.

    Attributes:
        name: The id of its name in the game's texts.
        enabled: Whether it runs.
        free: How many free grabs an account has a day, from the start of the rewards' day.
        paid: The grabs bought after the free ones.
        feed: The list of the latest rare prizes.
        currency: The currency that its prizes are valued in, as the store's products price them.
        tiers: The prizes it starts with, in tiers; the admin changes them as a box's drops.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    enabled: bool = True
    free: PositiveInt = 1
    paid: PaidGrabs
    feed: ClawFeed = ClawFeed()
    currency: str
    tiers: tuple[Tier, ...]

    @property
    def box(self) -> BoxRule:
        """The store's box that holds its prizes, as the store is stocked with it."""
        return BoxRule(
            key=CLAW,
            kind="claw",
            currency=self.currency,
            value=self.paid.price,
            worth_rule=False,
            tiers=self.tiers,
        )
