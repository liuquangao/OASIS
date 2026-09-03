import pytest

from hydromind.domain.areas import resolve_area


def test_resolve_glasgow_alias() -> None:
    area = resolve_area("格拉斯哥")
    assert area.id == "glasgow"
    assert area.country_code == "GB-SCT"


def test_unknown_area_is_explicit() -> None:
    with pytest.raises(ValueError, match="Unsupported named area"):
        resolve_area("London")
