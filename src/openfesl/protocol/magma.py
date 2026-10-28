from xml.etree.ElementTree import Element, SubElement, tostring


def success(*children: Element) -> bytes:
    """Return the document of a Magma request that succeeded, holding what it answers.

    Args:
        children: The answer's elements, in order.
    """
    root = Element("success")
    root.extend(children)
    return _document(root)


def error(code: str, cause: str) -> bytes:
    """Return the document of a Magma request that failed, which the game logs as code and cause.

    Args:
        code: What failed.
        cause: Why it failed.
    """
    root = Element("error", code=code)
    SubElement(root, "failure", cause=cause)
    return _document(root)


def token(value: str) -> bytes:
    """Return the answer to nucleus/authToken: the token the game logs in to FESL with.

    Args:
        value: The token.
    """
    element = Element("token")
    element.text = value
    return success(element)


def _document(root: Element) -> bytes:
    return tostring(root, encoding="unicode").encode()
