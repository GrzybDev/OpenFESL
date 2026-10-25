from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    """What an admin may do in the admin panel."""

    ADMIN = "admin"
    """Everything, such as managing providers, keys and settings."""
    MODERATOR = "moderator"
    """The day-to-day decisions about servers and accounts."""


@dataclass(frozen=True, slots=True)
class Admin:
    """Someone signed in to the admin panel through the deployment's sign-in provider.

    Attributes:
        subject: The account's id at the provider, which never changes.
        name: The account's display name there when it signed in.
        role: What the admin may do.
    """

    subject: str
    name: str
    role: Role


@dataclass(frozen=True, slots=True)
class GroupRoles:
    """The groups of the sign-in provider whose members get a role.

    Attributes:
        admins: Members of this group are admins.
        moderators: Members of this group are moderators, unless they are admins.
    """

    admins: str | None = None
    moderators: str | None = None

    def role(self, groups: list[str]) -> Role | None:
        """Return the role that a member of some groups gets, if any.

        Args:
            groups: The groups the account is a member of.
        """
        if self.admins and self.admins in groups:
            return Role.ADMIN
        if self.moderators and self.moderators in groups:
            return Role.MODERATOR
        return None
