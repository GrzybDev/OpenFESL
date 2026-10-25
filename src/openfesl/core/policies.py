import logging
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ConfigDict

# For annotations only, since a game's data holds its policies.
if TYPE_CHECKING:
    from openfesl.core.games import Game

logger = logging.getLogger(__name__)


class ConcurrentLogin(StrEnum):
    """What a player's login does while the account is logged in elsewhere."""

    REPLACE = "replace"
    """The earlier session is signed out."""
    REFUSE = "refuse"
    """The new login is refused."""


class UnderParentalAge(StrEnum):
    """What registration does with a player old enough to register but not without consent."""

    ALLOW = "allow"
    """The player registers; the service asks for no parent's consent."""
    REFUSE = "refuse"
    """The registration is refused; the game shows its birth date notice and disconnects."""


class _Section(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RegistrationPolicies(_Section):
    """How players register.

    Attributes:
        age_check: Whether a birth date is checked against the country's age limits.
        under_parental_age: What the age check does with a player under the country's age
            for registering without a parent's consent.
    """

    age_check: bool
    under_parental_age: UnderParentalAge


class AccountPolicies(_Section):
    """How accounts log in.

    Attributes:
        concurrent_login: What a player's login does while the account is logged in elsewhere.
    """

    concurrent_login: ConcurrentLogin


class Policies(_Section):
    """How a game's services behave where the game handles more than one behaviour.

    A game's data file holds the defaults and the instance overrides them one by one, each
    named by its section and field, such as `accounts.concurrent_login`.

    Attributes:
        registration: How players register.
        accounts: How accounts log in.
    """

    registration: RegistrationPolicies
    accounts: AccountPolicies

    def names(self) -> list[str]:
        """Return the name of every policy."""
        return [
            f"{section}.{name}" for section, values in self.model_dump().items() for name in values
        ]

    def text(self, name: str) -> str:
        """Return a policy's value as the settings write it, such as on, off or replace.

        Args:
            name: The policy's name.

        Raises:
            KeyError: When no policy has the name.
        """
        section, _, field = name.partition(".")
        return _text(self.model_dump()[section][field])

    def overridden(self, name: str, value: str) -> Policies:
        """Return the policies with one of them set from its text.

        Args:
            name: The policy's name.
            value: Its value as the settings write it.

        Raises:
            KeyError: When no policy has the name.
            ValueError: When the policy takes no such value.
        """
        values = self.model_dump()
        section, _, field = name.partition(".")
        if field not in values.get(section, {}):
            raise KeyError(name)
        values[section][field] = value
        return Policies.model_validate(values)


class SettingStore(Protocol):
    """Where the instance's settings are kept, each change recorded among the admin's actions."""

    async def settings(self, prefix: str) -> dict[str, str]:
        """Return the settings whose keys start with a prefix, by key.

        Args:
            prefix: The start of the keys.
        """

    async def change(self, key: str, value: str | None, admin: str | None) -> None:
        """Set or remove a setting and record the change.

        Args:
            key: The setting's key.
            value: Its new value; None removes it.
            admin: The admin who changed it; None for the command line.
        """


def setting_key(game: Game, name: str) -> str:
    """Return the key of the setting that overrides one of a game's policies.

    Args:
        game: The game.
        name: The policy's name.
    """
    return f"{game.id}.{name}"


class GamePolicies:
    """Reads each game's policies: its data file's defaults with the instance's overrides."""

    def __init__(self, store: SettingStore) -> None:
        """Read the overrides from a store.

        Args:
            store: Where the instance's settings are kept.
        """
        self._store = store

    async def of(self, game: Game) -> Policies:
        """Return a game's policies as of now.

        The admin may change them while the server runs, so they are read on every use. An
        override that no policy takes, as after an upgrade, is skipped.

        Args:
            game: The game.
        """
        policies = game.policies
        prefix = setting_key(game, "")
        for key, value in (await self._store.settings(prefix)).items():
            try:
                policies = policies.overridden(key.removeprefix(prefix), value)
            except KeyError, ValueError:
                logger.warning("Ignoring the setting %s, which no policy takes", key)
        return policies


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value)
