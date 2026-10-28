import pytest

from openfesl.formats.definitions import Definition, read_definitions, read_tree

ITEMS = b"""\xef\xbb\xbf<!-- invented -->
<database>
  <items>
    <item id="12">
      <faction>1</faction> <!-- one faction -->
      <slot>0</slot>
      <template layer="5">mesh_a</template>
      <template layer="6">mesh_b</template>
      <values><range>2</range><damage>3</damage></values>
      <criterias><criteria><val>1</val></criteria><criteria><val>2</val></criteria></criterias>
    </item>
    <item id="13"><slot/></item>
  </items>
</database>
"""

LEVELS = b"""<abilities>
  <item> <!-- invented ability -->
    <faction>3</faction>
    <upgrade id="40"><values><cost>1</cost><refresh>20</refresh></values></upgrade>
    <upgrade id="41"><values><refresh>15</refresh></values></upgrade>
  </item>
</abilities>
"""


def test_items_keep_their_single_values_with_nested_ones_named_by_both_elements() -> None:
    # The repeated templates and the criteria nested deeper are left out.
    assert read_definitions(ITEMS, "item") == [
        Definition(12, {"faction": "1", "slot": "0", "values.range": "2", "values.damage": "3"}),
        Definition(13, {"slot": ""}),
    ]


def test_each_level_of_a_definition_is_an_item_with_the_values_of_the_levels_before_it() -> None:
    assert read_definitions(LEVELS, "item", "upgrade") == [
        Definition(40, {"faction": "3", "values.cost": "1", "values.refresh": "20"}, (40, 41)),
        Definition(41, {"faction": "3", "values.cost": "1", "values.refresh": "15"}, (40, 41)),
    ]


def test_an_item_without_a_number_or_a_file_that_is_no_xml_is_refused() -> None:
    with pytest.raises(ValueError, match="no number"):
        read_definitions(b"<items><item id='x'/></items>", "item")
    with pytest.raises(ValueError, match="no XML"):
        read_definitions(b"<items>", "item")


def test_a_file_kept_whole_keeps_its_attributes_texts_and_children() -> None:
    tree = read_tree(b"<colors><skin><c id='1'>30 20 15</c></skin><hair/></colors>")

    assert tree == {
        "tag": "colors",
        "children": [
            {
                "tag": "skin",
                "children": [{"tag": "c", "attributes": {"id": "1"}, "text": "30 20 15"}],
            },
            {"tag": "hair"},
        ],
    }
