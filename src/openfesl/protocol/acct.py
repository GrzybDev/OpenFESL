from pydantic import Field

from openfesl.protocol.message import Message, Text


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
    description: str
    iso_code: str = Field(alias="ISOCode")


class GetCountryListAnswer(Message):
    """The countries offered on registration; the game's own age limits apply to each."""

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
