from pathlib import Path

from bid_knowledge.utils.io_utils import read_json, write_json


class _ArrayLike:
    def __init__(self, values):
        self._values = values

    def tolist(self):
        return list(self._values)


class _ScalarLike:
    def __init__(self, value):
        self._value = value

    def item(self):
        return self._value


def test_write_json_serializes_array_like_values(tmp_path: Path) -> None:
    path = tmp_path / "sample.json"

    write_json(
        path,
        {
            "numbers": _ArrayLike([1, 2, 3]),
            "nested": {"score": _ScalarLike(0.98)},
        },
    )

    assert read_json(path) == {"numbers": [1, 2, 3], "nested": {"score": 0.98}}


def test_write_json_stringifies_unknown_objects(tmp_path: Path) -> None:
    class Font:
        def __str__(self):
            return "demo-font"

    path = tmp_path / "font.json"

    write_json(path, {"font": Font()})

    assert read_json(path) == {"font": "demo-font"}
