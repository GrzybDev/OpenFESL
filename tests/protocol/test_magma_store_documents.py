from openfesl.protocol.magma import ProductEntry, products


def test_each_product_is_one_price_point_with_how_the_item_is_owned() -> None:
    # The game merges the products of one item into an offer, reads a rental's days and a pack's
    # uses from the attributes, and marks an offer whose price is below its original on sale.
    document = products(
        [
            ProductEntry("OFB-DBFH:1", "Practice Rifle", "3015-unlm", "weapon", 0, "_PF", 6720),
            ProductEntry("OFB-DBFH:2", "Rifle", "3015-tlmd", "weapon", 1, "_DV", 250, days=1),
            ProductEntry(
                "OFB-DBFH:3", "Tonic & Co", "2153-lmct", "ability", 2, "_PF", 175, uses=25
            ),
            ProductEntry(
                "OFB-DBFH:4", "Hat", "3-unlm", "appearance", 3, "_PF", 1999, original_price=2600
            ),
        ]
    )

    assert document == (
        b'<products><product productId="OFB-DBFH:1" productName="Practice Rifle">'
        b"<status>published</status><attributes>"
        b'<attribute name="entitlementTag" value="3015-unlm" />'
        b'<attribute name="groupName" value="weapon" />'
        b'<attribute name="sortKey" value="0" /></attributes>'
        b'<pricePoints><pricePoint currency="_PF" price="6720" /></pricePoints></product>'
        b'<product productId="OFB-DBFH:2" productName="Rifle">'
        b"<status>published</status><attributes>"
        b'<attribute name="entitlementTag" value="3015-tlmd" />'
        b'<attribute name="groupName" value="weapon" />'
        b'<attribute name="sortKey" value="1" />'
        b'<attribute name="duration" value="1" />'
        b'<attribute name="durationType" value="DAY" /></attributes>'
        b'<pricePoints><pricePoint currency="_DV" price="250" /></pricePoints></product>'
        b'<product productId="OFB-DBFH:3" productName="Tonic &amp; Co">'
        b"<status>published</status><attributes>"
        b'<attribute name="entitlementTag" value="2153-lmct" />'
        b'<attribute name="groupName" value="ability" />'
        b'<attribute name="sortKey" value="2" />'
        b'<attribute name="useCount" value="25" /></attributes>'
        b'<pricePoints><pricePoint currency="_PF" price="175" /></pricePoints></product>'
        b'<product productId="OFB-DBFH:4" productName="Hat">'
        b"<status>published</status><attributes>"
        b'<attribute name="entitlementTag" value="3-unlm" />'
        b'<attribute name="groupName" value="appearance" />'
        b'<attribute name="sortKey" value="3" />'
        b'<attribute name="originalPrice" value="2600" /></attributes>'
        b'<pricePoints><pricePoint currency="_PF" price="1999" /></pricePoints></product>'
        b"</products>"
    )
