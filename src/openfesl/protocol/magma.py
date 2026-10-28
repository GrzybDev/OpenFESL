from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from xml.etree.ElementTree import Element, SubElement, tostring


@dataclass(frozen=True, slots=True)
class EntitlementEntry:
    """An entitlement as the game reads it from a list.

    Attributes:
        id: The number the game names it by when it uses or changes it.
        tag: The item it entitles to and how it is owned, such as 3002-unlm.
        use_count: The uses or seconds left of an item limited by them.
        granted: When it was granted.
        expires: When an item limited by date ends; none for other items.
        status: ACTIVE, or why it is not.
    """

    id: int
    tag: str
    use_count: int
    granted: datetime
    expires: datetime | None
    status: str


def success(*children: Element) -> bytes:
    """Return the document of a Magma request that succeeded, holding what it answers.

    Args:
        children: The answer's elements, in order.
    """
    root = Element("success")
    root.extend(children)
    return _document(root)


def error(code: str, cause: str, field: str | None = None) -> bytes:
    """Return the document of a Magma request that failed, which the game logs as code and cause.

    Args:
        code: What failed.
        cause: Why it failed.
        field: The part of the request that the cause names, if any.
    """
    root = Element("error", code=code)
    failure = SubElement(root, "failure", cause=cause)
    if field is not None:
        failure.set("field", field)
    return _document(root)


def token(value: str) -> bytes:
    """Return the answer to nucleus/authToken: the token the game logs in to FESL with.

    Args:
        value: The token.
    """
    element = Element("token")
    element.text = value
    return success(element)


def name(value: str) -> bytes:
    """Return the answer to nucleus/name, whose root the game requires to be exactly name.

    Args:
        value: The name.
    """
    root = Element("name")
    root.text = value
    return _document(root)


def entitlements(entries: Iterable[EntitlementEntry]) -> bytes:
    """Return a list of entitlements, which the game reads whatever its root is named.

    Args:
        entries: The entitlements, in order.
    """
    root = Element("entitlements")
    for entry in entries:
        entitlement = SubElement(root, "entitlement")
        for name, text in (
            ("entitlementId", str(entry.id)),
            ("entitlementTag", entry.tag),
            ("useCount", str(entry.use_count)),
            ("grantDate", _date(entry.granted)),
            ("terminationDate", _date(entry.expires) if entry.expires else ""),
            ("status", entry.status),
        ):
            SubElement(entitlement, name).text = text
    return _document(root)


@dataclass(frozen=True, slots=True)
class RelationshipEntry:
    """A relationship as the game reads it from a roster or the answer to a change.

    Attributes:
        id: The number the game names it by when it acknowledges it.
        new: Whether the other side changed it since it was last acknowledged.
        state: Who agreed to it, as bits: 1 the side whose roster it is, 2 the other side.
        tag: The list it is on, such as friends.
        real_id: The other side: an account's number, or a server's name.
        type: Whether the other side is an account, nucleus, or a server.
        status: The other side's status line; empty for none.
        name: The other side's display name.
        modified: When it last changed.
        entity_modified: When the other side was last seen in its game; the game reads one
            more than 12 minutes old as gone.
    """

    id: int
    new: bool
    state: int
    tag: str
    real_id: str
    type: str
    status: str
    name: str
    modified: datetime
    entity_modified: datetime


def roster(entries: Iterable[RelationshipEntry]) -> bytes:
    """Return the relationships of an account or a server, which the game caches by their ids.

    Args:
        entries: The relationships, in order.
    """
    root = Element("roster")
    root.extend(_relationship(entry) for entry in entries)
    return _document(root)


def relationship(entry: RelationshipEntry) -> bytes:
    """Return the answer to a change of a relationship: the relationship as it is now.

    Args:
        entry: The relationship.
    """
    return success(_relationship(entry))


@dataclass(frozen=True, slots=True)
class PersonaEntry:
    """A player that a persona search found, as the game reads it.

    Attributes:
        user_id: The player's account number, which the game asks to befriend.
        display_name: The name that matched.
        namespace: Where the name is from: the website's namespace for the account's display
            name, the game's own for a hero.
    """

    user_id: int
    display_name: str
    namespace: str


def personas(entries: Iterable[PersonaEntry]) -> bytes:
    """Return the answer to a persona search: each player found, by the name that matched.

    Args:
        entries: The players, in order.
    """
    root = Element("personas")
    for entry in entries:
        persona = SubElement(root, "persona")
        for name, text in (
            ("userId", str(entry.user_id)),
            ("displayName", entry.display_name),
            ("namespaceName", entry.namespace),
        ):
            SubElement(persona, name).text = text
    return _document(root)


def status_update(body: bytes) -> str | None:
    """Return the status line of a relationship status update, or none when it holds none.

    Args:
        body: The update as the game posts it, raw although it is sent as a form.
    """
    # 1.46 writes the line between the tags as it is, which need not be well-formed XML, and
    # 1.89 in a CDATA section; neither escapes it.
    start = body.find(b"<status>")
    if start < 0:
        return None
    start += len(b"<status>")
    closing = b"</status>"
    if body.startswith(b"<![CDATA[", start):
        start += len(b"<![CDATA[")
        closing = b"]]></status>"
    end = body.find(closing, start)
    return body[start:end].decode("utf-8", "replace") if end >= 0 else None


def billing_accounts(balances: Mapping[str, int]) -> bytes:
    """Return a persona's wallets, the balance it has to spend in each currency.

    Args:
        balances: The balances, by currency code.
    """
    root = Element("billingAccounts")
    for currency, balance in balances.items():
        _wallet_account(root, currency, balance)
    return _document(root)


def refunded(currency: str, balance: int, items: Iterable[int]) -> bytes:
    """Return the answer to a refund of a hero's abilities, which the game needs a wallet in.

    Args:
        currency: The currency the abilities were paid in.
        balance: The hero's balance in it after the refund.
        items: The items taken back, by number.
    """
    root = Element("success", code="REFUNDED")
    _wallet_account(root, currency, balance)
    SubElement(root, "refunded").extend(_text("item", str(item)) for item in items)
    return _document(root)


def nothing_refunded() -> bytes:
    """Return the answer to a refund of a hero who bought no abilities, which the game reports."""
    return _document(Element("success", code="NO_CHANGE"))


def _wallet_account(parent: Element, currency: str, balance: int) -> None:
    wallet = SubElement(parent, "walletAccount")
    SubElement(wallet, "currency").text = currency
    SubElement(wallet, "balance").text = str(balance)


def _text(name: str, text: str) -> Element:
    element = Element(name)
    element.text = text
    return element


@dataclass(frozen=True, slots=True)
class ProductEntry:
    """A product among the store's offers, as the game reads it: one way to own an item at a price.

    The game lists the products of one item as one offer, whose price points they are.

    Attributes:
        id: The id the game buys it by.
        name: Its name, which the game's own names of its items take the place of.
        tag: The item and how it is owned once bought, such as 3002-tlmd.
        group: The group it is listed in.
        sort: Its place in the list.
        currency: The currency of its price.
        price: Its price.
        uses: The uses it brings, for an item limited by uses.
        days: How long it lasts, for a rental.
        original_price: Its price before a sale, which marks it on sale.
    """

    id: str
    name: str
    tag: str
    group: str
    sort: int
    currency: str
    price: int
    uses: int | None = None
    days: int | None = None
    original_price: int | None = None


def products(entries: Iterable[ProductEntry] = ()) -> bytes:
    """Return the store's products, as the game loads them as offers.

    Args:
        entries: The products, in order.
    """
    root = Element("products")
    root.extend(_product(entry) for entry in entries)
    return _document(root)


@dataclass(frozen=True, slots=True)
class BundleEntry:
    """A bundle as the game reads it among its hot deals: products sold together at one price.

    Attributes:
        id: The id the game buys it by.
        name: Its name, as the game shows it.
        description: What the game shows about it.
        tag: The tag of its leading item, if it has one.
        game_order: Its place among the hot deals.
        published: Since when it is sold.
        created: When it was made.
        random: Whether it gives one of its contents at random.
        currency: The currency of its price.
        price: Its price.
        original_price: Its price before a sale, which marks it on sale.
        members: The products it holds, each with its own price.
        small_image: The address of its small picture, which the game downloads; empty for none.
        big_image: The address of its big picture, likewise.
    """

    id: str
    name: str
    description: str
    tag: str
    game_order: int
    published: datetime
    created: datetime
    random: bool
    currency: str
    price: int
    original_price: int | None = None
    members: tuple[ProductEntry, ...] = ()
    small_image: str = ""
    big_image: str = ""


def hot_deals(entries: Iterable[BundleEntry]) -> bytes:
    """Return the store's hot deals, as the game loads them.

    Args:
        entries: The bundles, in order.
    """
    root = Element("products")
    for entry in entries:
        bundle = SubElement(root, "product", productId=entry.id)
        SubElement(bundle, "status").text = "published"
        attributes = SubElement(bundle, "attributes")
        named = [
            ("entitlementTag", entry.tag),
            ("gameOrder", str(entry.game_order)),
            ("hotdealsDescription", entry.description),
            ("gameSmallImage", entry.small_image),
            ("gameBigImage", entry.big_image),
            ("Product Name", entry.name),
            ("publishedDate", _moment(entry.published)),
            ("createdDate", _moment(entry.created)),
            ("randomContent", "YES" if entry.random else "NO"),
        ]
        if entry.original_price is not None:
            named.append(("originalPrice", str(entry.original_price)))
        for name, value in named:
            SubElement(attributes, "attribute", name=name, value=value)
        # The game reads only the first price point of a bundle.
        points = SubElement(bundle, "PricePoints")
        SubElement(points, "pricePoint", currency=entry.currency, price=str(entry.price))
        SubElement(bundle, "association").extend(_product(member) for member in entry.members)
    return _document(root)


def _product(entry: ProductEntry) -> Element:
    # Only a published product is read, and only its first price point.
    product = Element("product", productId=entry.id, productName=entry.name)
    SubElement(product, "status").text = "published"
    attributes = SubElement(product, "attributes")
    named = [("entitlementTag", entry.tag), ("groupName", entry.group), ("sortKey", entry.sort)]
    if entry.uses is not None:
        named.append(("useCount", entry.uses))
    if entry.days is not None:
        named += [("duration", entry.days), ("durationType", "DAY")]
    if entry.original_price is not None:
        named.append(("originalPrice", entry.original_price))
    for name, value in named:
        SubElement(attributes, "attribute", name=name, value=str(value))
    points = SubElement(product, "pricePoints")
    SubElement(points, "pricePoint", currency=entry.currency, price=str(entry.price))
    return product


def _date(moment: datetime) -> str:
    # Games read Magma's dates to the minute, in UTC.
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%MZ")


def _relationship(entry: RelationshipEntry) -> Element:
    # The game rejects an entry that lacks any of the children before modified.
    element = Element("relationship", id=str(entry.id))
    for name, text in (
        ("new", "true" if entry.new else "false"),
        ("state", str(entry.state)),
        ("tag", entry.tag),
        ("realid", entry.real_id),
        ("type", entry.type),
        ("status", entry.status),
        ("name", entry.name),
        ("modified", _moment(entry.modified)),
        ("entityModified", _moment(entry.entity_modified)),
    ):
        SubElement(element, name).text = text
    return element


def _moment(moment: datetime) -> str:
    # The dates of store products, bundles and relationships, to the second in UTC.
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _document(root: Element) -> bytes:
    return tostring(root, encoding="unicode").encode()
