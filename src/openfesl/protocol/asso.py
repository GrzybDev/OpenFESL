from typing import Annotated

from pydantic import Field

from openfesl.protocol.message import FeslDateTime, Message, Text

# The SDK keeps list types in 65 bytes and each part of a domain partition in 33, NULs included.
ListType = Annotated[Text, Field(max_length=64)]
PartitionPart = Annotated[Text, Field(max_length=32)]


class Partition(Message):
    """The domain partition a list lives in; every part is empty for a list without one."""

    domain: PartitionPart = ""
    sub_domain: PartitionPart = ""
    key: PartitionPart = ""


class Owner(Message):
    """A persona as requests name it; type 1 is a persona."""

    id: int = 0
    type: int = 0


class Entity(Message):
    id: int
    type: int
    name: str | None = None


class MemberInfo(Entity):
    created: FeslDateTime | None = None
    modified: FeslDateTime | None = None


class GetAssociations(Message):
    """A request for one list, or for its size in GetAssociationCount."""

    domain_partition: Partition = Field(default_factory=Partition)
    type: ListType = ""
    owner: Owner = Field(default_factory=Owner)


class GetAssociationsAnswer(Message):
    """A list's members; the game finds the list by its type, partition and owner."""

    type: str
    domain_partition: Partition
    owner: Entity
    max_list_size: int
    members: list[MemberInfo]


class GetAssociationCountAnswer(Message):
    max_list_size: int
    count: int


class AssociationRequest(Message):
    owner: Owner = Field(default_factory=Owner)
    member: Owner = Field(default_factory=Owner)
    mutual: bool = False


class AddAssociations(Message):
    domain_partition: Partition = Field(default_factory=Partition)
    type: ListType = ""
    list_full_behavior: str = ""
    add_requests: list[AssociationRequest] = Field(default_factory=list)


class DeleteAssociations(Message):
    domain_partition: Partition = Field(default_factory=Partition)
    type: ListType = ""
    delete_requests: list[AssociationRequest] = Field(default_factory=list)


class AssociationOutcome(Message):
    """What became of one request; any nonzero outcome fails the whole batch in the game."""

    owner: Entity
    member: MemberInfo
    list_size: int | None = None
    outcome: int
    mutual: bool


class AssociationsAnswer(Message):
    """The outcomes of AddAssociations or DeleteAssociations, grouped by owner in the game."""

    type: str
    domain_partition: Partition
    result: list[AssociationOutcome]


class NotifyAssociationUpdate(Message):
    """A change to the receiving persona's own list that someone else made."""

    type: str
    domain_partition: Partition
    owner: Entity
    member: MemberInfo
    list_size: int
    mutual: bool
    operation: str
