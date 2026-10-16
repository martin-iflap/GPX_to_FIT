"""Every sport has an entry in every per-sport table.

A new SportType value (cycling is planned) would otherwise fail only once a
conversion reaches the table it's missing from, with a KeyError. Or, where a
table had a fallback, be paced or written as another sport without a word.
The tables stay separate on purpose: the tuning harness reads them one by one.
"""

import pytest

from gpx2fit.core.fit_writer import FIT_SPORT, RECORD_INTERVAL_S
from gpx2fit.core.models import SportType
from gpx2fit.core.pacing.acceleration import MAX_ACCELERATION_MPS2
from gpx2fit.core.pacing.curve_selection import (
    CURVE_FILLS,
    DEFAULT_TOBLER_WEIGHT,
    MAX_SPEED_RATIO_BOUNDS,
    TOBLER_THRESHOLDS,
)
from gpx2fit.core.pacing.surface import _FAMILY_WEIGHTS, _WEIGHTS_BY_SPORT

SPORT_TABLES = {
    "TOBLER_THRESHOLDS": TOBLER_THRESHOLDS,
    "DEFAULT_TOBLER_WEIGHT": DEFAULT_TOBLER_WEIGHT,
    "MAX_SPEED_RATIO_BOUNDS": MAX_SPEED_RATIO_BOUNDS,
    "CURVE_FILLS": CURVE_FILLS,
    "MAX_ACCELERATION_MPS2": MAX_ACCELERATION_MPS2,
    "RECORD_INTERVAL_S": RECORD_INTERVAL_S,
    "FIT_SPORT": FIT_SPORT,
}


@pytest.mark.parametrize("table", SPORT_TABLES.values(), ids=SPORT_TABLES.keys())
def test_every_sport_has_an_entry_and_nothing_else_does(table):
    assert set(table) == set(SportType)


def test_every_sport_has_surface_weights_and_nothing_else_does():
    # surface_weights.json is keyed by SportType.value.
    assert set(_WEIGHTS_BY_SPORT) == {sport.value for sport in SportType}


@pytest.mark.parametrize("sport", list(SportType), ids=lambda sport: sport.value)
def test_every_sport_has_each_surface_table_calculate_surface_multipliers_reads(sport):
    assert set(_WEIGHTS_BY_SPORT[sport.value]) == {*_FAMILY_WEIGHTS, "sac_scale"}
