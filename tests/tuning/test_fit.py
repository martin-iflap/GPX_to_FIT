"""Fitting the constants: per-activity optima, then the corpus fit."""

from functools import lru_cache

import pytest

from gpx2fit.core.fit_writer import write_fit
from gpx2fit.core.models import SportType
from tuning.fit import (
    GLOBAL_KNOBS,
    SPORT_KNOBS,
    fit_constants,
    split_corpus,
    sweep_activity,
)
from tuning.fit_reader import read_reference_activity
from tuning.model import PacingParams
from tuning.prepare import prepare_reference
from tests.tuning.conftest import synthetic_activity


def _prepared(name: str, **kwargs):
    track = synthetic_activity(**kwargs)
    return prepare_reference(read_reference_activity(write_fit(track), name))


@lru_cache(maxsize=1)
def _built_corpus() -> tuple:
    """Build the corpus once; reading and preparing six activities is the slow part.

    Safe to share: nothing in tuning/fit.py mutates a PreparedActivity, and the
    split functions copy before sorting or shuffling.
    """
    return tuple(_corpus_specs())


def _corpus():
    """A small corpus spanning both sports and a range of terrain."""
    return list(_built_corpus())


def _corpus_specs():
    """Six activities: three hikes and three runs, flat through alpine."""
    return [
        _prepared("hike-steep", sport=SportType.HIKING, amplitude=200,
                  uphill_factor=3.8, flat_speed_mps=1.4, point_count=400),
        _prepared("hike-rolling", sport=SportType.HIKING, amplitude=80,
                  uphill_factor=3.0, flat_speed_mps=1.6, point_count=400),
        _prepared("hike-gentle", sport=SportType.HIKING, amplitude=30,
                  uphill_factor=2.6, flat_speed_mps=1.7, point_count=400),
        _prepared("run-hilly", sport=SportType.RUNNING, amplitude=80,
                  uphill_factor=3.2, flat_speed_mps=3.0, point_count=400),
        _prepared("run-rolling", sport=SportType.RUNNING, amplitude=35,
                  uphill_factor=2.8, flat_speed_mps=3.2, point_count=400),
        _prepared("run-flat", sport=SportType.RUNNING, amplitude=10,
                  uphill_factor=2.4, flat_speed_mps=3.4, point_count=400),
    ]


class TestSweepActivity:
    def test_the_optimum_is_never_worse_than_what_the_resolvers_chose(self):
        """The sweep searches the same space the resolvers pick a point in."""
        best = sweep_activity(_prepared("probe", amplitude=120, uphill_factor=4.0))
        assert best.best_objective <= best.resolved_objective + 1e-9

    def test_headroom_is_the_gap_between_them(self):
        best = sweep_activity(_prepared("probe", amplitude=120, uphill_factor=4.0))
        assert best.headroom == pytest.approx(best.resolved_objective - best.best_objective)
        assert best.headroom >= 0.0

    def test_the_optimum_stays_inside_the_searched_ranges(self):
        best = sweep_activity(_prepared("probe", amplitude=150, uphill_factor=4.5))
        assert 0.0 <= best.best_tobler_weight <= 1.0
        assert best.best_max_speed_ratio > 1.0

    def test_features_are_reported_for_the_corpus_stage(self):
        best = sweep_activity(_prepared("probe", amplitude=150))
        assert best.verticality > 0.0
        assert best.flat_equivalent_mps > 0.0
        assert best.distance_m > 0.0

    def test_steeper_terrain_reports_higher_verticality(self):
        gentle = sweep_activity(_prepared("gentle", amplitude=20))
        steep = sweep_activity(_prepared("steep", amplitude=220))
        assert steep.verticality > gentle.verticality


class TestSplitCorpus:
    def test_the_split_is_stratified_by_sport(self):
        """Each sport must keep enough training activities to fit its own bounds."""
        train, test = split_corpus(_corpus(), holdout=0.34, seed=1)
        assert {activity.sport for activity in train} == {SportType.HIKING, SportType.RUNNING}
        assert len(train) + len(test) == 6

    def test_the_same_seed_gives_the_same_split(self):
        corpus = _corpus()
        first = [activity.name for activity in split_corpus(corpus, 0.34, seed=7)[1]]
        second = [activity.name for activity in split_corpus(corpus, 0.34, seed=7)[1]]
        assert first == second

    def test_a_zero_holdout_trains_on_everything(self):
        train, test = split_corpus(_corpus(), holdout=0.0, seed=1)
        assert len(train) == 6 and test == []

    def test_a_sport_is_never_stripped_below_two_training_activities(self):
        """Otherwise a greedy holdout would leave a sport's constants fitted on nothing."""
        train, _ = split_corpus(_corpus(), holdout=1.0, seed=1)
        by_sport: dict[SportType, int] = {}
        for activity in train:
            by_sport[activity.sport] = by_sport.get(activity.sport, 0) + 1
        assert all(count >= 2 for count in by_sport.values())


class TestFitConstants:
    def test_fitting_improves_the_training_objective(self):
        result = fit_constants(_corpus(), rounds=1, holdout=0.0)
        assert result.train_after <= result.train_before

    def test_the_fit_reports_which_activities_it_trained_on(self):
        result = fit_constants(_corpus(), rounds=1, holdout=0.34)
        assert set(result.train_names) & set(result.test_names) == set()
        assert len(result.train_names) + len(result.test_names) == 6

    def test_every_sport_gets_its_own_bounds(self):
        result = fit_constants(_corpus(), rounds=1, holdout=0.0)
        assert set(result.sport_params) == {SportType.HIKING, SportType.RUNNING}
        for values in result.sport_params.values():
            assert set(values) == set(SPORT_KNOBS)

    def test_the_hilly_bound_never_comes_out_below_the_flat_one(self):
        """Inverting them would describe a model where flat terrain swings more than hills."""
        result = fit_constants(_corpus(), rounds=2, holdout=0.0)
        for values in result.sport_params.values():
            assert values["ratio_hilly"] >= values["ratio_flat"]

    def test_params_for_merges_global_and_per_sport_constants(self):
        result = fit_constants(_corpus(), rounds=1, holdout=0.0)
        params = result.params_for(SportType.HIKING)
        assert params.ratio_flat == result.sport_params[SportType.HIKING]["ratio_flat"]
        assert params.uphill_fill == result.global_params.uphill_fill

    def test_fitted_globals_stay_inside_their_search_ranges(self):
        result = fit_constants(_corpus(), rounds=1, holdout=0.0)
        for name, (low, high, _) in GLOBAL_KNOBS.items():
            assert low <= getattr(result.global_params, name) <= high

    def test_boundary_hits_name_the_constants_that_hit_an_edge(self):
        result = fit_constants(_corpus(), rounds=1, holdout=0.0)
        for hit in result.boundary_hits():
            assert "at the" in hit

    def test_an_empty_corpus_is_rejected(self):
        with pytest.raises(ValueError, match="at least one"):
            fit_constants([])

    def test_the_fit_starts_from_the_constants_it_is_given(self):
        base = PacingParams(uphill_fill=0.5)
        result = fit_constants(_corpus(), base=base, rounds=1, holdout=0.0)
        assert result.base_params.uphill_fill == 0.5
