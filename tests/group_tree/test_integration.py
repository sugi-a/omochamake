from pathlib import Path
import pytest
from jtcmake import StaticGroupBase, Rule, UntypedGroup, SELF


class Group(StaticGroupBase):
    a: Rule[str]


def test_guard_outputless_rule():
    g = Group()

    def _f():
        ...

    with pytest.raises(ValueError):
        g.a.init({}, _f)()


def test_error_on_dupe_registration():
    # rule -> group
    g = UntypedGroup()
    g.add("a", Path.write_text)(SELF, "a")
    with pytest.raises(KeyError):
        g.add_group("a")

    # group -> rule
    g = UntypedGroup()
    g.add_group("a")
    with pytest.raises(KeyError):
        g.add("a", Path.write_text)(SELF, "a")
