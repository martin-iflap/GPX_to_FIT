"""The drift guard: tuning/model.py must pace identically to core's combine().

tuning/model.py restates combine()'s arithmetic with the constants as
parameters, because core exposes no injection point for them. That restatement
is only trustworthy while it stays in step with the real thing — if combine()
changes and this doesn't, the harness quietly starts tuning a model the app
doesn't run, and nothing else would notice.

So: at default PacingParams, predict_elapsed must reproduce combine()'s
timestamps exactly, for both sports and across flat, rolling and mountainous
terrain.
"""

from datetime import timedelta

import pytest

from gpx2fit.core.models import SportType, Track
from gpx2fit.core.pacing.combine import combine
from gpx2fit.core.pacing.curve_selection import (
    resolve_curve_shape,
    resolve_max_speed_ratio,
    resolve_tobler_weight,
)
from tests.core.conftest import START, anchor, flat_track, leg_distances, rolling_track, timestamp_of
from tuning.model import (
    PacingParams,
    TrackModel,
    curve_shape_for,
    max_speed_ratio_for,
    predict_elapsed,
    resolve,
    tobler_weight_for,
)
from gpx2fit.core.pacing.gradient import calculate_gradient

SPORTS = [SportType.RUNNING, SportType.HIKING]

# Flat, gently rolling, and steep enough to push resolve_tobler_weight's
# verticality criterion over — the three regimes the constants behave
# differently in.
TERRAINS = {
    "flat": lambda: flat_track([i * 50.0 for i in range(81)]),
    "rolling": lambda: rolling_track(4000.0, climb_per_km=40.0),
    "mountain": lambda: rolling_track(4000.0, climb_per_km=250.0),
}

# Fast enough to stay on Minetti, and slow enough to tip to Tobler.
DURATIONS = [1200.0, 5400.0]


def _combine_elapsed(track: Track, sport: SportType, active_seconds: float) -> list[float]:
    """Seconds at each point according to the real combine(), from start and end anchors only."""
    end = START + timedelta(seconds=active_seconds)
    anchors = [anchor(0.0, START), anchor(track.points[-1].distance_from_start, end)]
    combine(track, anchors, sport)
    return [(timestamp_of(point) - START).total_seconds() for point in track.points]


@pytest.mark.parametrize("terrain", sorted(TERRAINS))
@pytest.mark.parametrize("sport", SPORTS)
@pytest.mark.parametrize("active_seconds", DURATIONS)
class TestMirrorMatchesCombine:
    def test_elapsed_times_match_exactly(self, terrain, sport, active_seconds):
        predicted, _ = predict_elapsed(TERRAINS[terrain](), sport, active_seconds, PacingParams())
        actual = _combine_elapsed(TERRAINS[terrain](), sport, active_seconds)

        assert len(predicted) == len(actual)
        for index, (mine, theirs) in enumerate(zip(predicted, actual)):
            # combine() stamps datetimes, which round to the microsecond;
            # anything above that is real divergence, not representation.
            assert mine == pytest.approx(theirs, abs=1e-6), f"point {index}"

    def test_resolved_settings_match_core(self, terrain, sport, active_seconds):
        track = TERRAINS[terrain]()
        gradients = calculate_gradient(track)
        distances = leg_distances(track)
        params = PacingParams()
        model = TrackModel.from_legs(gradients, distances)

        assert tobler_weight_for(model, active_seconds, sport, params) == pytest.approx(
            resolve_tobler_weight(gradients, distances, active_seconds, sport)
        )
        core_ratio = resolve_max_speed_ratio(gradients, distances, sport)
        assert max_speed_ratio_for(model, sport, params) == pytest.approx(core_ratio)

        mine = curve_shape_for(core_ratio, params)
        theirs = resolve_curve_shape(core_ratio)
        assert mine.minetti.uphill == pytest.approx(theirs.minetti.uphill)
        assert mine.minetti.downhill == pytest.approx(theirs.minetti.downhill)
        assert mine.tobler.uphill == pytest.approx(theirs.tobler.uphill)
        assert mine.tobler.downhill == pytest.approx(theirs.tobler.downhill)


class TestPredictElapsedShape:
    def test_total_matches_active_seconds_exactly(self):
        """The whole premise of the harness: only shape is predicted, never the total."""
        predicted, _ = predict_elapsed(rolling_track(3000.0, 60.0), SportType.RUNNING, 1500.0, PacingParams())
        assert predicted[0] == 0.0
        assert predicted[-1] == pytest.approx(1500.0)

    def test_elapsed_is_strictly_increasing(self):
        predicted, _ = predict_elapsed(rolling_track(3000.0, 120.0), SportType.HIKING, 4000.0, PacingParams())
        assert all(later > earlier for earlier, later in zip(predicted, predicted[1:]))

    def test_pinned_weight_and_ratio_override_resolution(self):
        track = rolling_track(3000.0, 60.0)
        params = PacingParams(tobler_weight=0.25, max_speed_ratio=1.5)
        _, settings = predict_elapsed(track, SportType.RUNNING, 1500.0, params)
        assert settings.tobler_weight == 0.25
        assert settings.max_speed_ratio == 1.5

    def test_a_tighter_bound_flattens_the_pace(self):
        """Lower max_speed_ratio must narrow the spread of leg times, not just cap it."""
        track = rolling_track(3000.0, 150.0)

        def spread(ratio: float) -> float:
            elapsed, _ = predict_elapsed(
                track, SportType.HIKING, 3000.0, PacingParams(max_speed_ratio=ratio)
            )
            legs = [later - earlier for earlier, later in zip(elapsed, elapsed[1:])]
            return max(legs) / min(legs)

        assert spread(1.3) < spread(2.5)

    def test_verticality_is_higher_on_steeper_terrain(self):
        def settings_for(climb_per_km: float):
            track = rolling_track(3000.0, climb_per_km)
            return resolve(TrackModel.build(track), SportType.HIKING, 3000.0, PacingParams())

        assert settings_for(300.0).verticality > settings_for(30.0).verticality

    def test_rejects_a_track_too_short_to_pace(self):
        with pytest.raises(ValueError):
            predict_elapsed(flat_track([0.0]), SportType.RUNNING, 100.0, PacingParams())

    def test_rejects_non_positive_active_seconds(self):
        with pytest.raises(ValueError):
            predict_elapsed(flat_track([0.0, 50.0, 100.0]), SportType.RUNNING, 0.0, PacingParams())
