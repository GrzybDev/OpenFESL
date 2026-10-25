from pydantic import Field

from openfesl.protocol.message import FeslDate, Message, Text


class NuLogin(Message):
    """An account login with a name and password, or with a token from an earlier login."""

    nuid: Text = ""
    password: Text = ""
    encrypted_info: Text = ""
    return_encrypted_info: bool = False
    mac_addr: str = ""
    tos_version: Text = ""


class NuLoginAnswer(Message):
    """The account's session key and identity; the token lets the game log in silently later."""

    lkey: str
    nuid: str
    user_id: int
    encrypted_login_info: str | None = None


class NuGetTos(Message):
    country_code: str = ""


class NuGetTosAnswer(Message):
    """The terms that the game shows, and the version it sends back once they are accepted."""

    version: str
    tos: str


class Country(Message):
    """A country of the registration form with the ages from which a player may register.

    Attributes:
        registration_age_limit: The age from which a player may register at all.
        parental_control_age_limit: The age from which a player registers without a parent.
    """

    description: str
    iso_code: str = Field(alias="ISOCode")
    registration_age_limit: int
    parental_control_age_limit: int


class GetCountryListAnswer(Message):
    """The countries offered on registration."""

    country_list: list[Country]


class NuAddAccount(Message):
    """A new account from the game's registration form."""

    nuid: Text = ""
    password: Text = ""
    country: Text = ""
    tos_version: Text = ""
    dob_day: int = Field(0, alias="DOBDay")
    dob_month: int = Field(0, alias="DOBMonth")
    dob_year: int = Field(0, alias="DOBYear")


class NuUpdateAccount(Message):
    """The account form again, with the account's own name and password to confirm the change.

    The birth date comes only when the player gave one.
    """

    nuid: Text = ""
    password: Text = ""
    country: Text = ""
    dob_day: int | None = Field(None, alias="DOBDay")
    dob_month: int | None = Field(None, alias="DOBMonth")
    dob_year: int | None = Field(None, alias="DOBYear")


class NuGetAccountByNuid(Message):
    nuid: Text = ""
    password: Text = ""


class NuGetAccountAnswer(Message):
    """The account's details; the service keeps no names, address, birth date or e-mail choices."""

    nuid: str
    user_id: int
    country: str


class NuUpdatePassword(Message):
    password: Text = ""
    new_password: Text = ""
    return_encrypted_info: bool = False


class NuUpdatePasswordAnswer(Message):
    """The token for the next silent login, since the change forgets the remembered ones."""

    encrypted_password_info: str | None = None


class Persona(Message):
    """A request naming one of the account's personas."""

    name: str = ""


class NuSuggestPersonas(Message):
    name: str = ""
    max_suggestions: int = 0


class NuSuggestPersonasAnswer(Message):
    """Free names; the game reads them until the first gap, so they have none."""

    names: list[str]


class NuGetPersonas(Message):
    namespace: str = ""


class NuGetPersonasAnswer(Message):
    """The account's personas; the game reads them until the first gap, so they have none."""

    personas: list[str]


class NuLoginPersonaAnswer(Message):
    """The persona's session key, which Theater and the game's HTTP requests carry."""

    lkey: str
    user_id: int


class UserLookup(Message):
    user_name: str | None = None
    user_id: int | None = None


class NuLookupUserInfo(Message):
    user_info: list[UserLookup] = Field(default_factory=list)


class UserInfo(Message):
    user_name: str
    user_id: int
    master_user_id: int
    namespace: str


class NuLookupUserInfoAnswer(Message):
    """The personas found, each with its account as the master user."""

    user_info: list[UserInfo]


class NuSearchOwners(Message):
    """A search for personas by name, where * stands for any run of characters."""

    screen_name: Text = ""
    chunk_size: int = 0


class FoundOwner(Message):
    name: str
    type: int
    id: int


class NuSearchOwnersAnswer(Message):
    users: list[FoundOwner]


class NuEntitleGame(Message):
    """A product key for the game, sent with the login it should entitle."""

    key: Text = ""
    nuid: Text = ""
    password: Text = ""
    encrypted_info: Text = ""


class NuEntitleUser(Message):
    """A product key that the logged-in account redeems, such as one for downloadable content."""

    key: Text = ""


class Product(Message):
    product_id: str | None = None
    name: str


class NuEntitleUserAnswer(Message):
    product_list: list[Product]


class NuGetEntitlements(Message):
    """Filters for an account's entitlements; a dedicated server names the player's account."""

    entitlement_id: str = ""
    entitlement_tag: str = ""
    product_id: str = ""
    group_name: str = ""
    status: str = ""
    master_user_id: int = 0
    device_physical_id: str = ""


class EntitlementInfo(Message):
    entitlement_id: str
    entitlement_tag: str
    product_id: str | None = None
    status: str
    grant_date: FeslDate


class NuGetEntitlementsAnswer(Message):
    entitlements: list[EntitlementInfo]


class NuGetEntitlementCountAnswer(Message):
    entitlement_count: int


class GetLockerURLAnswer(Message):
    """Where the game lists the files it keeps online, with its persona's session key."""

    url: str


class GetTelemetryTokenAnswer(Message):
    """Where the game sends telemetry, as base64 of `address,port,locale,`, and for which countries.

    The SDK sends nothing for a country on the disabled list.
    """

    telemetry_token: str
    enabled: str
    disabled: str


class NuGrantEntitlement(Message):
    """An entitlement the game grants; a public dedicated server names the player's account."""

    entitlement_tag: Text = ""
    master_id: int = 0
