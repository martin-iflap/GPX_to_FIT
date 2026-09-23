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
one's numbers. This works as block coordinate descent over `STAGES`. Each block
is a knob or two that the data can pin down together, searched jointly on a full
grid while every other constant stays frozen. The blocks run in order, and the
whole sequence repeats until a round moves nothing. Grid search because the
objective is only piecewise smooth (medians, smoothsteps, a tanh), evaluations
are milliseconds, and it stays deterministic with no extra dependency.

It used to be plain one-knob-at-a-time descent over every constant at once, and
on the real corpus most constants ended on a search bound. Three things about
the model made that happen, and each shaped the current layout:

- `curve_reference_grade` only rescales the exponent that the fills already set
  (exponent = fill·ln(ratio) / |ln raw(reference_grade)|). The knobs slid along
  a ridge of equal scores, and where they stopped depended on search order. So
  the reference grade isn't fitted at all.
- The fills are fitted per sport. Shared fills made the running-heavy corpus
  flatten the hiking curve, and the hiking ratio drifted up to compensate.
- `ratio_hilly >= ratio_flat` was checked one knob at a time, so each could
  block the other: hiking's `ratio_flat` sat at exactly `ratio_hilly` without
  it counting as a bound. Searched as a pair, the constraint only removes
  cells from the grid. When the two land on the same value anyway,
  `boundary_hits` says so.

The standing caveat: an activity's empirical optimum is not the best constant.
Someone who faded on the last climb "wants" a bound the terrain doesn't justify.
That is why the constants are fitted as a smooth function of terrain features
rather than averaged from the optima, and why the held-out split is reported.
"""

import itertools
import math
import random
from dataclasses import dataclass, replace

from gpx2fit.core.models import SportType
from gpx2fit.core.pacing.curve_selection import MAX_SPEED_RATIO_BOUNDS
from tuning.compare import DEFAULT_BUCKET_M, ActivityContext, objective_of, report_of
from tuning.model import PacingParams, TrackModel
from tuning.prepare import PreparedActivity

# Stage one's search space. The coarse grid brackets the optimum; the refine
# pass then bisects around it, which is enough for a quantity we only ever read
# to two decimals.
_WEIGHT_GRID = [index / 10 for index in range(11)]
_RATIO_GRID = [1.1 + index * 0.1 for index in range(30)]
_REFINE_ROUNDS = 3

@dataclass(frozen=True)
class Stage:
    """One block of the fit: knobs searched jointly while everything else is frozen.

    Attributes:
        name: What the block tunes, for the progress trace.
        knobs: name -> (low, high, steps). Every combination is tried, so keep
            it to a knob or two.
        per_sport: True fits each sport separately on its own activities.
            False fits one shared value on the whole training split.
    """
    name: str
    knobs: dict[str, tuple[float, float, int]]
    per_sport: bool


# Stage two's blocks, in the order they run. Ranges are wide enough to contain
# an answer that contradicts today's guesses. A fit that can only confirm the
# prior isn't worth running.
#
# The shape comes first because it is the most directly measured: the uphill
# buckets pin the uphill fill and the downhill buckets pin the downhill one,
# with little interference between them. The speed bound comes next, with the
# shape frozen. The hilliness ramp comes last and alone. Its width
# (`hilly_verticality_band`) stays at the shipped value, since a corpus this
# size can't tell a wide ramp from a narrow one.
STAGES = (
    Stage("curve shape", {
        "uphill_fill": (0.10, 0.95, 18),
        # Down to near zero: every band table so far shows descents barely
        # faster than the flat, and an exponent of exactly 0 is invalid.
        "downhill_fill": (0.02, 0.70, 18),
    }, per_sport=True),
    Stage("speed bound", {
        "ratio_flat": (1.2, 3.0, 19),
        "ratio_hilly": (1.2, 4.0, 29),
    }, per_sport=True),
    Stage("hilliness ramp", {
        "hilly_verticality": (0.02, 0.16, 15),
    }, per_sport=False),
)

GLOBAL_KNOBS = {name: bounds for stage in STAGES if not stage.per_sport for name, bounds in stage.knobs.items()}
SPORT_KNOBS = {name: bounds for stage in STAGES if stage.per_sport for name, bounds in stage.knobs.items()}

# Two activities of one sport count as the same route for the train/held-out
# split when their distances differ by about this share or less...
SAME_ROUTE_DISTANCE_TOLERANCE = 0.08
# ...and their verticality (mean |gradient|) by about this much. Both are
# measured on this corpus: repeats of one loop land within ~3% on distance and
# ~0.01 on verticality, since recorded elevation differs a little each time.
# The two are combined as an ellipse, so a pair near both limits doesn't count.
SAME_ROUTE_VERTICALITY_TOLERANCE = 0.012

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
class StageStep:
    """What one stage did in one round: the trace that shows whether the fit settled.

    Attributes:
        round: 1-based round number.
        stage: The stage's name.
        sport: The sport it was fitted for, or None for a shared stage.
        values: The block's knob values after the stage.
        changed: Whether the stage moved any of them.
        train_objective: The whole training split's objective afterward.
    """
    round: int
    stage: str
    sport: SportType | None
    values: dict[str, float]
    changed: bool
    train_objective: float


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
        history: Every stage of every round, in order. If a knob still swings
            between rounds on the last one, its blocks are still trading off
            and the value isn't settled.
        route_groups: Activities `group_by_shape` judged to be the same route
            and kept on one side of the split. Singletons are left out.
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
    history: list[StageStep]
    route_groups: list[list[str]]

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
            # A bound set by another knob rather than the range: the corpus
            # found no difference between flat and hilly activities for this
            # sport, or wanted hilly ones to swing less, which the model can't express.
            if math.isclose(values["ratio_hilly"], values["ratio_flat"]):
                hits.append(f"{sport.value} ratio_hilly = ratio_flat = {values['ratio_flat']:.4f}"
                            f" (the hilly bound can't go below the flat one)")
        return hits


class _Evaluator:
    """Evaluates the corpus objective, laying each activity out once up front.

    An ActivityContext holds everything about an activity that no constant in
    the search can change — its gradients, curves, bucket layout and the real
    seconds per bucket. A coordinate descent makes thousands of evaluations
    over the same handful of activities, so this is the difference between
    doing that work once and doing it every time.

    None of the fitted knobs is `gradient_window_m`, but the window still has
    to come from the parameters being fitted rather than from the default, or a
    `--params` file that changed it would be quietly ignored. It is taken from
    `base` and the contexts are built for it; `compare._evaluate` rejects any
    later evaluation that disagrees.
    """

    def __init__(
        self,
        activities: list[PreparedActivity],
        base: PacingParams | None = None,
        bucket_m: float = DEFAULT_BUCKET_M,
    ):
        self.activities = activities
        self.bucket_m = bucket_m
        self._contexts = {
            activity.name: ActivityContext.build(activity, base, bucket_m) for activity in activities
        }
        self._scores: dict[tuple[str, PacingParams], float] = {}

    def shapes(self) -> dict[str, tuple[float, float]]:
        """name -> (distance_m, verticality), from the models already built."""
        return {
            name: (context.model.total_distance_m, context.model.verticality)
            for name, context in self._contexts.items()
        }

    def objective(self, activity: PreparedActivity, params: PacingParams) -> float:
        """One activity's objective, memoized on (activity, constants).

        Coordinate descent revisits the same points constantly: every pass
        re-scores the incumbent, and once the knobs stop moving a whole round
        repeats the previous one's grid exactly. PacingParams is a frozen
        dataclass, so it keys this directly, and a hit costs a dict lookup
        instead of pacing the activity again.
        """
        key = (activity.name, params)
        score = self._scores.get(key)
        if score is None:
            score = objective_of(self._contexts[activity.name], params)
            self._scores[key] = score
        return score

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
    context = ActivityContext.build(activity, params, bucket_m)

    def score(s_weight: float, s_ratio: float) -> float:
        trial = replace(params, tobler_weight=s_weight, max_speed_ratio=max(s_ratio, _MIN_RATIO))
        return objective_of(context, trial)

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

    baseline = report_of(context, params)
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


def track_shape(activity: PreparedActivity) -> tuple[float, float]:
    """(distance in metres, verticality) — what `group_by_shape` compares."""
    model = TrackModel.build(activity.track)
    return model.total_distance_m, model.verticality


def _shape_gap(first: tuple[float, float], second: tuple[float, float]) -> float:
    """How far apart two track shapes are, where 1.0 is the edge of "same route"."""
    distance_gap = abs(math.log(first[0] / second[0])) / SAME_ROUTE_DISTANCE_TOLERANCE
    verticality_gap = abs(first[1] - second[1]) / SAME_ROUTE_VERTICALITY_TOLERANCE
    return math.hypot(distance_gap, verticality_gap)


def group_by_shape(
    activities: list[PreparedActivity],
    shapes: dict[str, tuple[float, float]] | None = None,
) -> list[list[PreparedActivity]]:
    """Group activities that look like the same route: same sport, distance and elevation.

    A corpus is mostly a few home routes done over and over. If one run of a
    loop is trained on and another is held out, the held-out score measures
    memory of that loop rather than generalization. So the split moves whole
    groups, never single activities.

    Grouping is by the track's overall shape (distance and verticality), not
    its start location. The shape is what the pacing model actually sees, so
    two different routes with the same profile are just as much a repeat to it.

    Complete linkage: two groups merge only while *every* pair across them is
    within tolerance. Single linkage would chain 6, 6.5, 7, 7.5 km runs into
    one blob, since a home corpus is dense in exactly that range.

    Args:
        activities: The corpus.
        shapes: name -> (distance_m, verticality), if the caller already has
            them. Computed with `track_shape` otherwise.
    Returns:
        The groups, each sorted by name, in order of their first name.
    """
    if shapes is None:
        shapes = {activity.name: track_shape(activity) for activity in activities}

    groups = [[activity] for activity in sorted(activities, key=lambda act: act.name)]

    def linkage(first: list[PreparedActivity], second: list[PreparedActivity]) -> float:
        if first[0].sport != second[0].sport:
            return math.inf
        return max(_shape_gap(shapes[a.name], shapes[b.name]) for a in first for b in second)

    while True:
        best_gap, best_pair = math.inf, None
        for i, j in itertools.combinations(range(len(groups)), 2):
            gap = linkage(groups[i], groups[j])
            if gap < best_gap:
                best_gap, best_pair = gap, (i, j)
        if best_pair is None or best_gap > 1.0:
            break
        i, j = best_pair
        groups[i] = sorted(groups[i] + groups[j], key=lambda act: act.name)
        del groups[j]

    return sorted(groups, key=lambda group: group[0].name)


def split_corpus(
    activities: list[PreparedActivity],
    holdout: float,
    seed: int,
    groups: list[list[PreparedActivity]] | None = None,
) -> tuple[list[PreparedActivity], list[PreparedActivity]]:
    """Split into train and test, stratified by sport, moving same-route groups whole.

    Stratified because the per-sport constants are fitted per sport — a split
    that happened to put most of the hikes in the test set would leave the
    hiking bounds fitted on almost nothing. Grouped because a held-out
    activity whose twin was trained on isn't held out (see `group_by_shape`).

    Groups are drawn in shuffled order, and each is held out only if that
    brings the held-out count closer to `holdout`. So the held-out share
    lands near the target, not exactly on it.

    Args:
        activities: The corpus.
        holdout: Fraction to hold out, 0.0 to train on everything.
        seed: Fixes the shuffle, so a fit is reproducible.
        groups: From `group_by_shape`, if the caller already has them.
    Returns:
        (train, test). `test` is empty when `holdout` rounds to nothing.
    """
    if groups is None:
        groups = group_by_shape(activities)

    rng = random.Random(seed)
    train: list[PreparedActivity] = []
    test: list[PreparedActivity] = []

    by_sport: dict[SportType, list[list[PreparedActivity]]] = {}
    for group in groups:
        by_sport.setdefault(group[0].sport, []).append(group)

    for sport in sorted(by_sport, key=lambda value: value.value):
        sport_groups = list(by_sport[sport])
        rng.shuffle(sport_groups)
        size = sum(len(group) for group in sport_groups)
        target = round(size * holdout)
        # Never hold out so much that a sport has nothing left to fit on.
        most = max(0, size - 2)
        held = 0
        for group in sport_groups:
            after = held + len(group)
            if after <= most and abs(after - target) < abs(held - target):
                test.extend(group)
                held = after
            else:
                train.extend(group)

    return train, test


def _search_block(
    evaluator: _Evaluator,
    activities: list[PreparedActivity],
    apply,
    knobs: dict[str, tuple[float, float, int]],
    current: dict[str, float],
) -> dict[str, float]:
    """Search one block's knobs jointly over their full grid, and keep the best cell.

    Joint rather than one knob at a time. Knobs in a block trade off against
    each other, and a one-at-a-time search can stall at a point where only a
    move in both at once would help.

    Args:
        evaluator: Scores the corpus.
        activities: Which activities to score against.
        apply: Given a trial knob dict, returns the sport-to-PacingParams
            callable to score with.
        knobs: name -> (low, high, steps), the block being searched.
        current: Every knob value, including ones outside this block. Only
            the block's own are varied. Not mutated.
    Returns:
        The knob values with the block's best cell in place. The incumbent
        is kept unless a cell strictly beats it.
    """
    names = list(knobs)
    best = dict(current)
    best_score = evaluator.corpus_objective(apply(best), activities)

    for cell in itertools.product(*(_grid(*knobs[name]) for name in names)):
        trial = {**current, **dict(zip(names, cell))}
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

    Runs `STAGES` in order, each block searched jointly with every other
    constant frozen, and repeats the sequence until a whole round moves
    nothing or `rounds` runs out. Each block minimizes the pooled pacing
    objective directly rather than regressing against stage one's optima, so
    an activity counts for as much as it actually contributes.

    Args:
        activities: The prepared corpus.
        base: Constants to start from. Defaults to today's shipped values, so
            the result reads as a diff against what ships.
        rounds: The most passes through `STAGES` to make.
        holdout: Fraction of the corpus to hold out, stratified by sport,
            with same-route groups kept together (`group_by_shape`).
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
    evaluator = _Evaluator(activities, base, bucket_m)
    groups = group_by_shape(activities, evaluator.shapes())
    train, test = split_corpus(activities, holdout, seed, groups)
    sports = sorted({activity.sport for activity in activities}, key=lambda value: value.value)

    global_values = {name: getattr(base, name) for name in GLOBAL_KNOBS}
    sport_values: dict[SportType, dict[str, float]] = {}
    for sport in sports:
        # Start each sport from its own shipped bounds rather than a shared
        # default, so the fit begins exactly where the app currently is and the
        # result reads as a diff against it.
        bounds = MAX_SPEED_RATIO_BOUNDS[sport]
        sport_values[sport] = {
            "uphill_fill": base.uphill_fill,
            "downhill_fill": base.downhill_fill,
            "ratio_flat": base.ratio_flat if base.ratio_flat is not None else bounds.flat,
            "ratio_hilly": base.ratio_hilly if base.ratio_hilly is not None else bounds.hilly,
        }

    def build(globals_: dict[str, float], per_sport: dict[SportType, dict[str, float]]):
        def params_for(p_sport: SportType) -> PacingParams:
            return replace(base, **globals_, **per_sport.get(p_sport, {}))
        return params_for

    before_params = build(dict(global_values), {sport: dict(values) for sport, values in sport_values.items()})
    train_before = evaluator.corpus_objective(before_params, train)
    test_before = evaluator.corpus_objective(before_params, test) if test else 0.0

    history: list[StageStep] = []
    for round_number in range(1, rounds + 1):
        moved = False
        for stage in STAGES:
            if stage.per_sport:
                for sport in sports:
                    sport_train = [activity for activity in train if activity.sport == sport]
                    if not sport_train:
                        continue
                    before = sport_values[sport]
                    sport_values[sport] = _search_block(
                        evaluator, sport_train,
                        lambda trial, s=sport: build(global_values, {**sport_values, s: trial}),
                        stage.knobs, before,
                    )
                    changed = sport_values[sport] != before
                    moved |= changed
                    history.append(StageStep(
                        round_number, stage.name, sport,
                        {name: sport_values[sport][name] for name in stage.knobs}, changed,
                        evaluator.corpus_objective(build(global_values, sport_values), train),
                    ))
            else:
                before = global_values
                global_values = _search_block(
                    evaluator, train,
                    lambda trial: build(trial, sport_values),
                    stage.knobs, before,
                )
                changed = global_values != before
                moved |= changed
                history.append(StageStep(
                    round_number, stage.name, None,
                    {name: global_values[name] for name in stage.knobs}, changed,
                    evaluator.corpus_objective(build(global_values, sport_values), train),
                ))
        if not moved:
            break

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
        history=history,
        route_groups=[[activity.name for activity in group] for group in groups if len(group) > 1],
    )


# todo: review the claude newest answer. read more code.
