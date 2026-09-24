"""Measuring the model against a recording.

The load-bearing test here is the self-consistency one: if an activity's
reference timing is replaced by what the model itself predicts, every residual
must vanish. An instrument that reports error where there is none would send
the whole tuning exercise chasing its own artifacts.
"""

import math

import pytest

from gpx2fit.core.fit_writer import write_fit
from gpx2fit.core.models import SportType
from tuning.compare import DEFAULT_BUCKET_M, _in_band_table, compare
from tuning.fit_reader import read_reference_activity
from tuning.model import PacingParams, TrackModel, elapsed_from_model
from tuning.prepare import prepare_reference
from tests.tuning.conftest import synthetic_activity


def _prepared(**kwargs):
    track = synthetic_activity(**kwargs)
    return prepare_reference(read_reference_activity(write_fit(track), "probe"))


def _self_consistent(prepared, params=None):
    """Replace the reference timing with the model's own, so the truth is the model."""
    params = params or PacingParams()
    predicted, _ = elapsed_from_model(
        TrackModel.build(prepared.track), prepared.sport, prepared.moving_seconds, params
    )
    prepared.reference_elapsed = predicted.tolist()
    return prepared


class TestSelfConsistency:
    def test_a_perfectly_modelled_activity_has_no_error(self):
        report = compare(_self_consistent(_prepared()))

        assert report.objective == pytest.approx(0.0, abs=1e-9)
        assert report.max_time_error_seconds == pytest.approx(0.0, abs=1e-6)
        for band in report.bands:
            assert band.log_residual == pytest.approx(0.0, abs=1e-9)

    def test_model_and_real_relative_speeds_agree_band_by_band(self):
        for band in compare(_self_consistent(_prepared())).bands:
            assert band.model_relative_speed == pytest.approx(band.real_relative_speed, rel=1e-9)

    def test_comparing_against_different_parameters_does_show_error(self):
        """Guards the test above from passing because compare() always returns zero."""
        prepared = _self_consistent(_prepared())
        assert compare(prepared, PacingParams(uphill_fill=0.3, downhill_fill=0.65)).objective > 0.01


class TestRecoversAPlantedResponse:
    def test_an_athlete_who_struggles_uphill_shows_a_positive_climbing_residual(self):
        """Plant a known gradient response; the shape column must point at it."""
        report = compare(_prepared(uphill_factor=5.0, downhill_factor=0.5, amplitude=120))
        climbs = [band for band in report.bands if band.low >= 0.08]
        descents = [band for band in report.bands if band.high <= -0.08]

        assert climbs and descents
        # Model too fast where the athlete is slow, too slow where they are fast.
        assert all(band.shape_residual > 0 for band in climbs)
        assert all(band.shape_residual < 0 for band in descents)

    def test_the_residual_shape_is_monotonic_in_gradient(self):
        report = compare(_prepared(uphill_factor=5.0, downhill_factor=0.5, amplitude=120))
        shapes = [band.shape_residual for band in report.bands]
        # Near flat, this athlete's response is almost level, so adjacent
        # bands can tie to within rounding noise.
        assert all(later >= earlier - 1e-3 for earlier, later in zip(shapes, shapes[1:]))

    def test_a_flatter_athlete_produces_a_smaller_objective(self):
        gentle = compare(_prepared(uphill_factor=2.6, amplitude=40)).objective
        extreme = compare(_prepared(uphill_factor=5.5, amplitude=200)).objective
        assert gentle < extreme


class TestErrorMeasures:
    def test_the_mean_residual_is_near_zero_because_total_time_is_matched(self):
        """combine() matches the total exactly, so only the spread is identifiable."""
        report = compare(_prepared(uphill_factor=5.0, amplitude=150))
        assert abs(report.mean_log_residual) < 0.3
        assert report.objective > abs(report.mean_log_residual)

    def test_shape_residuals_are_the_residuals_less_their_mean(self):
        report = compare(_prepared(uphill_factor=4.0, amplitude=100))
        for band in report.bands:
            assert band.shape_residual == pytest.approx(band.log_residual - report.mean_log_residual)

    def test_clock_drift_is_zero_at_both_ends(self):
        """Start and end are pinned by construction; the drift lives in between."""
        prepared = _prepared(uphill_factor=5.0, amplitude=150)
        report = compare(prepared)
        assert 0.0 < report.max_time_error_at_m < prepared.distance_m

    def test_time_error_share_is_relative_to_moving_time(self):
        report = compare(_prepared(uphill_factor=5.0, amplitude=150))
        assert report.time_error_share == pytest.approx(
            abs(report.max_time_error_seconds) / report.moving_seconds
        )


class TestBucketing:
    def test_buckets_are_never_finer_than_the_gradient_window(self):
        """Below the smoothing window the model isn't claiming anything to be wrong about."""
        report = compare(_prepared(), bucket_m=5.0)
        assert min(bucket.distance_m for bucket in report.buckets) >= 60.0

    def test_a_longer_bucket_yields_fewer_buckets(self):
        prepared = _prepared()
        assert len(compare(prepared, bucket_m=400.0).buckets) < len(
            compare(prepared, bucket_m=DEFAULT_BUCKET_M).buckets
        )

    def test_every_banded_bucket_lands_in_exactly_one_band(self):
        report = compare(_prepared())
        banded = [bucket for bucket in report.buckets if _in_band_table(bucket)]
        assert sum(band.bucket_count for band in report.bands) == len(banded)

    def test_band_distances_sum_to_the_banded_distance(self):
        report = compare(_prepared())
        assert sum(band.distance_m for band in report.bands) == pytest.approx(
            sum(bucket.distance_m for bucket in report.buckets if _in_band_table(bucket))
        )

    def test_bands_come_back_in_ascending_gradient_order(self):
        report = compare(_prepared())
        assert [band.low for band in report.bands] == sorted(band.low for band in report.bands)

    def test_open_ended_bands_are_unbounded(self):
        report = compare(_prepared(amplitude=250))
        assert report.bands[0].low == -math.inf
        assert report.bands[-1].high == math.inf


class TestSportsAreHandledSeparately:
    def test_running_and_hiking_resolve_different_settings(self):
        running = compare(_prepared(sport=SportType.RUNNING, flat_speed_mps=3.2))
        hiking = compare(_prepared(sport=SportType.HIKING, flat_speed_mps=1.6))
        assert running.settings.tobler_weight != hiking.settings.tobler_weight
