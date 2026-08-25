from aplist_gen.snmp_utils import collapse_to_radio_key


def test_collapse_to_radio_key_keeps_first_value_per_radio():
    raw = {"4.1": "-86", "4.2": "-86", "4.3": "-86", "5.1": "-90"}

    result = collapse_to_radio_key(raw)

    assert result == {"4": "-86", "5": "-90"}


def test_collapse_to_radio_key_separates_different_radios():
    raw = {"4.1": "-86", "5.1": "-90", "6.1": "-88"}

    result = collapse_to_radio_key(raw)

    assert result == {"4": "-86", "5": "-90", "6": "-88"}


def test_collapse_to_radio_key_handles_empty_input():
    assert collapse_to_radio_key({}) == {}
