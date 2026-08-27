from oasis.domain.geo import haversine_km


def test_haversine_zero_distance() -> None:
    assert haversine_km(55.8642, -4.2518, 55.8642, -4.2518) == 0


def test_haversine_glasgow_to_edinburgh_is_plausible() -> None:
    distance = haversine_km(55.8642, -4.2518, 55.9533, -3.1883)
    assert 60 < distance < 80
