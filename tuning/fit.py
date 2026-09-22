"""Fit the pacing constants to a corpus of real activities.

Two levels, because the constants sit at two levels.

**Stage one, per activity.** `resolve_tobler_weight` and `resolve_max_speed_ratio`
each collapse a whole workout to a single scalar, so for one activity there are
only two numbers that matter. Sweeping them directly finds what that activity
*wanted*, independently of any resolver. The result is a small table — one row
per activity, its terrain features beside its empirical optimum — which is both
the thing to read during review and the evidence stage two fits against.

**Stage two, corpus.** Move the constants so the resolvers predict those optima,
minimizing the pooled objective directly rather than regressing against stage
one's numbers. Coordinate descent over a 1-D grid per constant: the objective is
only piecewise smooth (medians, smoothsteps, a tanh), evaluations are
milliseconds, and this stays deterministic with no extra dependency.

Some constants are global and some are per-sport, so the two are fitted in
alternating passes until they stop moving.

The standing caveat: an activity's empirical optimum is not the best constant.
Someone who faded on the last climb "wants" a bound the terrain doesn't justify.
That is why the constants are fitted as a smooth function of terrain features
rather than averaged from the optima, and why the held-out split is reported.
"""

import math
import random
from dataclasses import dataclass, replace

from gpx2fit.core.models import SportType
from gpx2fit.core.pacing.curve_selection import MAX_SPEED_RATIO_BOUNDS
from gpx2fit.core.pacing.gradient import calculate_gradient
from tuning.compare import DEFAULT_BUCKET_M, compare
from tuning.model import PacingParams
from tuning.prepare import PreparedActivity

# Stage one's search space. The coarse grid brackets the optimum; the refine
# pass then bisects around it, which is enough for a quantity we only ever read
# to two decimals.
_WEIGHT_GRID = [index / 10 for index in range(11)]
_RATIO_GRID = [1.1 + index * 0.1 for index in range(30)]
_REFINE_ROUNDS = 3

# Stage two's search space, as name -> (low, high, steps). Ranges are wide
# enough to contain an answer that contradicts today's guesses — a fit that can
# only confirm the prior isn't worth running.
GLOBAL_KNOBS = {
    "uphill_fill": (0.30, 0.95, 14),
    "downhill_fill": (0.10, 0.70, 13),
    "curve_reference_grade": (0.15, 0.40, 11),
    "hilly_verticality": (0.02, 0.14, 13),
    "hilly_verticality_band": (0.02, 0.08, 7),
}
SPORT_KNOBS = {
    "ratio_flat": (1.3, 3.0, 18),
    "ratio_hilly": (1.5, 4.0, 26),
}

# A leg's speed bound has to leave room to swing; at exactly 1.0 the curve
# shape's log_limit is zero and every exponent blows up.
_MIN_RATIO = 1.05


@dataclass(frozen=True)
class ActivityOptimum:
    """What one activity wanted, next to what the resolvers actually gave it.

    Attributes:
        name: The activity's identifier.
        sport: Its sport.
        distance_m: Route distance.
        moving_seconds: Moving time.
        verticality: Climb and descent per metre traveled — the regressor
            `resolve_max_speed_ratio` uses.
        flat_equivalent_mps: Terrain-normalized speed — the regressor
            `resolve_tobler_weight` uses.
        resolved_tobler_weight: What the current constants chose.
        resolved_max_speed_ratio: What the current constants chose.
        resolved_objective: The objective at those choices.
        best_tobler_weight: What this activity actually wanted.
        best_max_speed_ratio: What this activity actually wanted.
        best_objective: The objective there — a floor on how well any set of
            constants could do for this activity.
    """
    name: str
    sport: SportType
    distance_m: float
    moving_seconds: float
    verticality: float
    flat_equivalent_mps: float
    resolved_tobler_weight: float
    resolved_max_speed_ratio: float
    resolved_objective: float
    best_tobler_weight: float
    best_max_speed_ratio: float
    best_objective: float

    @property
    def headroom(self) -> float:
        """How much of the current error is the resolvers' fault rather than the model's.

        The gap between the objective at today's constants and the best any
        choice of those two scalars could reach. A large headroom means this
        activity is being let down by the resolvers; a small one with a high
        objective means the curve shape itself is wrong, which is stage two's
        global knobs rather than the per-sport ones.
        """
        return self.resolved_objective - self.best_objective


@dataclass(frozen=True)
class FitResult:
    """The outcome of fitting the constants to a corpus.

    Attributes:
        base_params: The constants the fit started from.
        global_params: The fitted global constants, as a PacingParams.
        sport_params: Fitted per-sport overrides, keyed by sport.
        train_before: Corpus objective on the training split, before.
        train_after: The same, after.
        test_before: Corpus objective on the held-out split, before.
        test_after: The same, after. If this barely improves while
            `train_after` does, the fit is memorizing the training activities.
        train_names: Which activities were trained on.
        test_names: Which were held out.
    """
    base_params: PacingParams
    global_params: PacingParams
    sport_params: dict[SportType, dict[str, float]]
    train_before: float
    train_after: float
    test_before: float
    test_after: float
    train_names: list[str]
    test_names: list[str]

    def params_for(self, sport: SportType) -> PacingParams:
        """The fitted constants as one sport sees them."""
        return replace(self.global_params, **self.sport_params.get(sport, {}))

    def boundary_hits(self) -> list[str]:
        """Constants that settled on the edge of their search range.

        A value pinned to an edge isn't an answer, it's a floor or a ceiling:
        the corpus wanted to keep going and the grid stopped it. Either widen
        the range and refit, or treat the number as "at least this much" —
        never as a calibrated result.
        """
        hits = []
        for name, (low, high, _) in GLOBAL_KNOBS.items():
            value = getattr(self.global_params, name)
            if value is not None and (math.isclose(value, low) or math.isclose(value, high)):
                edge = "low" if math.isclose(value, low) else "high"
                hits.append(f"{name} = {value:.4f} (at the {edge} end of {low}-{high})")
        for sport, values in self.sport_params.items():
            for name, value in values.items():
                low, high, _ = SPORT_KNOBS[name]
                if math.isclose(value, low) or math.isclose(value, high):
                    edge = "low" if math.isclose(value, low) else "high"
                    hits.append(f"{sport.value} {name} = {value:.4f} (at the {edge} end of {low}-{high})")
        return hits


class _Evaluator:
    """Evaluates the corpus objective, caching what doesn't depend on the parameters.

    Gradients depend only on the track and `gradient_window_m`, which no fit
    varies, so they're computed once per activity and reused across every one
    of the thousands of evaluations a coordinate descent makes.
    """

    def __init__(self, activities: list[PreparedActivity], bucket_m: float = DEFAULT_BUCKET_M):
        self.activities = activities
        self.bucket_m = bucket_m
        self._gradients = {
            activity.name: calculate_gradient(activity.track) for activity in activities
        }

    def objective(self, activity: PreparedActivity, params: PacingParams) -> float:
        return compare(
            activity, params, self.bucket_m, gradients=self._gradients[activity.name]
        ).objective

    def corpus_objective(
        self,
        params_for,
        activities: list[PreparedActivity] | None = None,
    ) -> float:
        """Distance-weighted mean objective over the given activities.

        Distance-weighted so a three-hour mountain day counts for more than a
        twenty-minute jog, which is also how the buckets inside each activity
        are weighted.

        Args:
            params_for: Callable taking a SportType and returning the
                PacingParams to run that sport's activities with.
            activities: Which activities to score. Defaults to all of them.
        """
        chosen = self.activities if activities is None else activities
        total_distance = sum(activity.distance_m for activity in chosen)
        if total_distance <= 0:
            return 0.0
        return sum(
            self.objective(activity, params_for(activity.sport)) * activity.distance_m
            for activity in chosen
        ) / total_distance


def sweep_activity(
    activity: PreparedActivity,
    params: PacingParams | None = None,
    bucket_m: float = DEFAULT_BUCKET_M,
) -> ActivityOptimum:
    """Stage one: find the (tobler_weight, max_speed_ratio) this activity actually wanted.

    A coarse grid over both, then a few bisecting refinement rounds around the
    best cell. Everything else in `params` is held fixed, so the answer is about
    these two scalars alone.

    Args:
        activity: The prepared activity.
        params: The constants to hold fixed. Defaults to today's values.
        bucket_m: Residual bucket length.
    Returns:
        Its optimum, its features, and what the current resolvers gave it.
    """
    params = params or PacingParams()
    gradients = calculate_gradient(activity.track, params.gradient_window_m)

    def score(s_weight: float, s_ratio: float) -> float:
        trial = replace(params, tobler_weight=s_weight, max_speed_ratio=max(s_ratio, _MIN_RATIO))
        return compare(activity, trial, bucket_m, gradients=gradients).objective

    best_weight, best_ratio = _WEIGHT_GRID[0], _RATIO_GRID[0]
    best_objective = math.inf
    for weight in _WEIGHT_GRID:
        for ratio in _RATIO_GRID:
            value = score(weight, ratio)
            if value < best_objective:
                best_objective, best_weight, best_ratio = value, weight, ratio

    weight_step, ratio_step = 0.05, 0.05
    for _ in range(_REFINE_ROUNDS):
        for weight in (best_weight - weight_step, best_weight + weight_step):
            clamped = min(1.0, max(0.0, weight))
            value = score(clamped, best_ratio)
            if value < best_objective:
                best_objective, best_weight = value, clamped
        for ratio in (best_ratio - ratio_step, best_ratio + ratio_step):
            clamped = max(_MIN_RATIO, ratio)
            value = score(best_weight, clamped)
            if value < best_objective:
                best_objective, best_ratio = value, clamped
        weight_step /= 2
        ratio_step /= 2

    baseline = compare(activity, params, bucket_m, gradients=gradients)
    return ActivityOptimum(
        name=activity.name,
        sport=activity.sport,
        distance_m=activity.distance_m,
        moving_seconds=activity.moving_seconds,
        verticality=baseline.settings.verticality,
        flat_equivalent_mps=baseline.settings.flat_equivalent_mps,
        resolved_tobler_weight=baseline.settings.tobler_weight,
        resolved_max_speed_ratio=baseline.settings.max_speed_ratio,
        resolved_objective=baseline.objective,
        best_tobler_weight=best_weight,
        best_max_speed_ratio=best_ratio,
        best_objective=best_objective,
    )


def _grid(low: float, high: float, steps: int) -> list[float]:
    """`steps` evenly spaced values from `low` to `high` inclusive."""
    if steps < 2:
        return [low]
    return [low + (high - low) * index / (steps - 1) for index in range(steps)] # todo: wouldn't it be better to use step as difference?


def split_corpus(
    activities: list[PreparedActivity], holdout: float, seed: int
) -> tuple[list[PreparedActivity], list[PreparedActivity]]:
    """Split into train and test, stratified by sport.

    Stratified because the per-sport constants are fitted per sport — a split
    that happened to put most of the hikes in the test set would leave the
    hiking bounds fitted on almost nothing.

    Args:
        activities: The corpus.
        holdout: Fraction to hold out, 0.0 to train on everything.
        seed: Fixes the shuffle, so a fit is reproducible.
    Returns:
        (train, test). `test` is empty when `holdout` rounds to nothing.
    """
    rng = random.Random(seed)
    train: list[PreparedActivity] = []
    test: list[PreparedActivity] = []

    by_sport: dict[SportType, list[PreparedActivity]] = {}
    for activity in activities:
        by_sport.setdefault(activity.sport, []).append(activity)

    for sport in sorted(by_sport, key=lambda value: value.value):
        group = sorted(by_sport[sport], key=lambda act: act.name)
        rng.shuffle(group)
        cut = round(len(group) * holdout)
        # Never hold out so much that a sport has nothing left to fit on.
        cut = min(cut, max(0, len(group) - 2))
        test.extend(group[:cut])
        train.extend(group[cut:])

    return train, test


def _descend(
    evaluator: _Evaluator,
    activities: list[PreparedActivity],
    apply,
    knobs: dict[str, tuple[float, float, int]],
    current: dict[str, float],
) -> dict[str, float]:
    """One coordinate-descent pass: try every value of each knob, keep what helps.

    Args:
        evaluator: Scores the corpus.
        activities: Which activities to score against.
        apply: Given a trial knob dict, returns the sport-to-PacingParams
            callable to score with.
        knobs: name -> (low, high, steps).
        current: The knob values to improve on. Not mutated.
    Returns:
        The improved knob values.
    """
    best = dict(current)
    best_score = evaluator.corpus_objective(apply(best), activities)

    for name, (low, high, steps) in knobs.items():
        for value in _grid(low, high, steps):
            trial = dict(best)
            trial[name] = value
            # The hilly bound is the looser of the two by definition; a fit
            # that inverted them would be describing a different model.
            if "ratio_flat" in trial and "ratio_hilly" in trial and trial["ratio_hilly"] < trial["ratio_flat"]:
                continue
            score = evaluator.corpus_objective(apply(trial), activities)
            if score < best_score:
                best_score, best = score, trial

    return best


def fit_constants(
    activities: list[PreparedActivity],
    base: PacingParams | None = None,
    rounds: int = 3,
    holdout: float = 0.3,
    seed: int = 20260920,
    bucket_m: float = DEFAULT_BUCKET_M,
) -> FitResult:
    """Stage two: fit the constants to the corpus, and report how they hold up held out.

    Alternates a global pass (the curve shape and the hilliness ramp, shared by
    every sport) with a per-sport pass (each sport's speed-swing bounds), until
    neither moves. Both minimize the pooled pacing objective directly rather
    than regressing against stage one's optima, so an activity counts for as
    much as it actually contributes.

    Args:
        activities: The prepared corpus.
        base: Constants to start from. Defaults to today's shipped values, so
            the result reads as a diff against what ships.
        rounds: How many global/per-sport alternations to run.
        holdout: Fraction of the corpus to hold out, stratified by sport.
        seed: Fixes the split.
        bucket_m: Residual bucket length.

    Returns:
        The fitted constants with before/after objectives on both splits.
    Raises:
        ValueError: If the corpus is empty.
    """
    if not activities:
        raise ValueError("Need at least one prepared activity to fit against.")

    base = base or PacingParams()
    evaluator = _Evaluator(activities, bucket_m)
    train, test = split_corpus(activities, holdout, seed)
    sports = sorted({activity.sport for activity in activities}, key=lambda value: value.value)

    global_values = {name: getattr(base, name) for name in GLOBAL_KNOBS}
    sport_values: dict[SportType, dict[str, float]] = {}
    for sport in sports:
        # Start each sport from its own shipped bounds rather than a shared
        # default, so the fit begins exactly where the app currently is and the
        # result reads as a diff against it.
        bounds = MAX_SPEED_RATIO_BOUNDS[sport]
        sport_values[sport] = {
            "ratio_flat": base.ratio_flat if base.ratio_flat is not None else bounds.flat,
            "ratio_hilly": base.ratio_hilly if base.ratio_hilly is not None else bounds.hilly,
        }

    def build(globals_: dict[str, float], per_sport: dict[SportType, dict[str, float]]):
        def params_for(p_sport: SportType) -> PacingParams:
            return replace(base, **globals_, **per_sport.get(p_sport, {}))
        return params_for

    before_params = build(
        {name: getattr(base, name) for name in GLOBAL_KNOBS},
        {sport: dict(values) for sport, values in sport_values.items()},
    )
    train_before = evaluator.corpus_objective(before_params, train)
    test_before = evaluator.corpus_objective(before_params, test) if test else 0.0

    for _ in range(rounds):
        global_values = _descend(
            evaluator, train,
            lambda trial: build(trial, sport_values),
            GLOBAL_KNOBS, global_values,
        )
        for sport in sports:
            sport_train = [activity for activity in train if activity.sport == sport]
            if not sport_train:
                continue
            sport_values[sport] = _descend(
                evaluator, sport_train,
                lambda trial, s=sport: build(global_values, {**sport_values, s: trial}),
                SPORT_KNOBS, sport_values[sport],
            )

    after_params = build(global_values, sport_values)
    return FitResult(
        base_params=base,
        global_params=replace(base, **global_values),
        sport_params=sport_values,
        train_before=train_before,
        train_after=evaluator.corpus_objective(after_params, train),
        test_before=test_before,
        test_after=evaluator.corpus_objective(after_params, test) if test else 0.0,
        train_names=[activity.name for activity in train],
        test_names=[activity.name for activity in test],
    )


# todo: then download better files for the corpus, rerun the fit, play with the constants.
# todo: check how we could speed things up a bit, perhaps use some cython or numpy, this module doesnt need to run with pyodide.
