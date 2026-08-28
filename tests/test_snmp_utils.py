import pytest

from aplist_gen.snmp_utils import collapse_to_radio_key, walk_bounded


class _FakeWalker:
    """Stands in for pysnmp's walk_cmd async generator, tracking whether
    aclose() actually gets called when walk_bounded exits early."""

    def __init__(self, rows):
        self._rows = iter(rows)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._rows)
        except StopIteration:
            raise StopAsyncIteration

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_walk_bounded_closes_generator_on_subtree_boundary(monkeypatch):
    fake = _FakeWalker([
        (None, 0, 0, [("1.2.3.4", "value")]),
        (None, 0, 0, [("1.2.4.1", "next-table-not-ours")]),
    ])
    monkeypatch.setattr("aplist_gen.snmp_utils.walk_cmd", lambda *a, **k: fake)

    result = await walk_bounded(engine=None, community="public", target=None, base_oid="1.2.3")

    assert result == {"4": "value"}
    assert fake.closed is True


@pytest.mark.asyncio
async def test_walk_bounded_closes_generator_on_snmp_error(monkeypatch):
    fake = _FakeWalker([
        ("timeout", 0, 0, []),
    ])
    monkeypatch.setattr("aplist_gen.snmp_utils.walk_cmd", lambda *a, **k: fake)

    result = await walk_bounded(engine=None, community="public", target=None, base_oid="1.2.3")

    assert result == {}
    assert fake.closed is True


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
