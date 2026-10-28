from collections import Counter
from dataclasses import dataclass
from typing import Any
from xml.etree.ElementTree import Element, ParseError, fromstring


@dataclass(frozen=True, slots=True)
class Definition:
    """One item as a file of a game's item definitions defines it.

    Attributes:
        id: The item's number, unique in the file.
        fields: The values of its definition by element name; a value nested one level deeper
            is named after both elements joined by a dot, such as values.range.
        levels: The numbers of the levels of the definition the item is one level of, in order;
            empty for an item defined alone.
    """

    id: int
    fields: dict[str, str]
    levels: tuple[int, ...] = ()


def read_definitions(data: bytes, element: str, levels: str | None = None) -> list[Definition]:
    """Read the items of an XML file of item definitions, in the file's order.

    Each element of a name is an item with its number in its id attribute, or, when the
    definitions have levels, defines one item per child of the levels' name, numbered by the
    child's id and with the definition's fields and those of its level and every level before
    it, since a later level lists only the values that change. Values of elements that repeat,
    such as lists of meshes, and anything nested deeper are left out.

    Args:
        data: The XML file.
        element: The name of the elements that define items.
        levels: The name of the children that are the levels of a definition, if it has them.

    Raises:
        ValueError: When the file is no XML or an item has no number.
    """
    found = []
    for definition in _parse(data).iter(element):
        fields = _fields(definition, levels)
        if levels is None:
            found.append(Definition(_number(definition), fields))
            continue
        steps = definition.findall(levels)
        numbers = tuple(_number(step) for step in steps)
        for number, step in zip(numbers, steps, strict=True):
            fields = fields | _fields(step, None)
            found.append(Definition(number, fields, numbers))
    return found


def read_tree(data: bytes) -> dict[str, Any]:
    """Read an XML file whole, as nested elements that JSON can hold.

    Args:
        data: The XML file.

    Returns:
        The root element: its tag, and its attributes, text and children where it has them,
        each child in the same form.

    Raises:
        ValueError: When the file is no XML.
    """
    return _tree(_parse(data))


def _parse(data: bytes) -> Element:
    try:
        return fromstring(data)
    except ParseError as error:
        raise ValueError(f"the file is no XML: {error}") from None


def _tree(element: Element) -> dict[str, Any]:
    tree: dict[str, Any] = {"tag": element.tag}
    if element.attrib:
        tree["attributes"] = dict(element.attrib)
    if text := (element.text or "").strip():
        tree["text"] = text
    if len(element):
        tree["children"] = [_tree(child) for child in element]
    return tree


def _fields(element: Element, levels: str | None) -> dict[str, str]:
    counts = Counter(child.tag for child in element)
    fields = {}
    for child in element:
        if child.tag == levels or counts[child.tag] > 1:
            continue
        if not len(child):
            fields[child.tag] = (child.text or "").strip()
            continue
        inner = Counter(grandchild.tag for grandchild in child)
        fields.update(
            (f"{child.tag}.{grandchild.tag}", (grandchild.text or "").strip())
            for grandchild in child
            if not len(grandchild) and inner[grandchild.tag] == 1
        )
    return fields


def _number(element: Element) -> int:
    number = element.get("id", "")
    try:
        return int(number)
    except ValueError:
        raise ValueError(f"a <{element.tag}> has the id {number!r}, which is no number") from None
