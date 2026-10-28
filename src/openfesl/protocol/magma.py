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


def billing_accounts(balances: Mapping[str, int]) -> bytes:
    """Return a persona's wallets, the balance it has to spend in each currency.

    Args:
        balances: The balances, by currency code.
    """
    root = Element("billingAccounts")
    for currency, balance in balances.items():
        wallet = SubElement(root, "walletAccount")
        SubElement(wallet, "currency").text = currency
        SubElement(wallet, "balance").text = str(balance)
    return _document(root)


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


def _document(root: Element) -> bytes:
    return tostring(root, encoding="unicode").encode()
