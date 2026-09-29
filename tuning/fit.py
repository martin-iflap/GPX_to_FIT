"""Search for better pacing numbers against a corpus of real activities.

Two independent searches over two different sets of numbers. Neither calls
the other nor reads the other's output; each backs its own command.

- **`sweep_activity` (the `sweep` command): per-workout settings, one activity
  at a time.** The resolvers turn a workout's terrain into two settings,
  `tobler_weight` and `max_speed_ratio`. This pins those two settings directly,
  bypassing the resolvers, and finds the pair one activity scores best at. No
  constant changes. It is a diagnostic: it shows whether the resolvers picked
  badly for that activity or whether no setting could have helped.

- **`fit_constants` (the `fit` command): the constants, across the corpus.**
  Searches the constants the resolvers and the curve shape read (the fills,
  Minetti's descent cost slope, the per-sport speed bounds, the hilliness
  threshold) so that one set of them
  paces every activity of a sport well. Block coordinate descent over
  `STAGES`: each block's knobs are searched jointly on a full grid while
  everything else stays frozen, and the blocks repeat until a round moves
  nothing. Its output is the proposal, `proposed.json`.

A typical session runs `sweep` to see where the model is wrong and then `fit`
to change it, but that link is a person reading a table, not code.

Grid search because the objective is only piecewise smooth (medians,
smoothsteps, a tanh), an evaluation takes milliseconds, and a full grid also
shows how flat the optimum is (`StageStep.plateau`).

Why `fit` can't just average what `sweep` found: one activity's optimum isn't
the right constant. Someone who faded on the last climb "wants" a bound the
terrain doesn't justify. So `fit` searches constants shared by every activity
of a sport, and reports a held-out split to show whether they generalize.
"""

import itertools
import math
import random
from dataclasses import dataclass, fields, replace

from gpx2fit.core.models import SportType
from gpx2fit.core.pacing.curve_selection import MAX_SPEED_RATIO_BOUNDS
from tuning.compare import DEFAULT_BUCKET_M, ActivityContext, objective_of, report_of
from tuning.model import PacingParams, ParamsBySport, TrackModel
from tuning.prepare import PreparedActivity

# The sweep's search space: a coarse grid, then a few rounds of halving steps
# around the best cell. Two decimals is all these numbers are ever read to.
_WEIGHT_GRID = [index / 10 for index in range(11)]
_RATIO_GRID = [1.1 + index * 0.1 for index in range(30)]
_REFINE_ROUNDS = 3


def _grid(low: float, high: float, step: float) -> list[float]:
    """Values from `low` to `high` inclusive, `step` apart.

    Each value is computed from the endpoints rather than by adding `step`
    repeatedly, so `high` comes out exactly and `boundary_hits` can recognize it.

    Raises:
        ValueError: If `step` doesn't divide the range evenly.
    """
    count = round((high - low) / step)
    if count < 1:
        return [low]
    if not math.isclose(low + count * step, high, rel_tol=1e-9, abs_tol=1e-12):
        raise ValueError(f"A step of {step} doesn't divide {low}-{high} evenly.")
    return [low + (high - low) * index / count for index in range(count + 1)]


@dataclass(frozen=True)
class Stage:
    """One block of the corpus fit: knobs searched jointly while the rest stay frozen.

    Attributes:
        name: What the block tunes, for the progress trace.
        knobs: name -> (low, high, step). Every combination is tried, so keep
            it to a knob or two.
        per_sport: True fits a separate value per sport, on that sport's
            activities. False fits one value on the whole training split.
    Raises:
        ValueError: At construction, so an edit to `STAGES` that the search
            couldn't run fails on import instead of after the corpus has loaded.
    """
    name: str
    knobs: dict[str, tuple[float, float, float]]
    per_sport: bool

    def __post_init__(self) -> None:
        known = {param.name for param in fields(PacingParams)}
        for knob, (low, high, step) in self.knobs.items():
            if knob not in known:
                raise ValueError(f"Stage {self.name!r}: {knob!r} isn't a PacingParams field.")
            if not low < high or step <= 0:
                raise ValueError(f"Stage {self.name!r}: {knob} needs low < high and a positive step,"
                                 f" got ({low}, {high}, {step}).")
            try:
                _grid(low, high, step)
            except ValueError as error:
                raise ValueError(f"Stage {self.name!r}, {knob}: {error}") from None


# The fit's blocks, in the order they run. Shape first, since it is the most
# directly measured (uphill buckets pin the uphill fill, downhill buckets the
# downhill one), then Minetti's descent cost. Then the speed bound with the
# shape frozen, then the hilliness ramp.

# Why these blocks:
# - Fills are per sport. Shared fills let the running-heavy corpus flatten the
#   hiking curve, and the hiking ratio drifted up to compensate.
# - The two ratios share a block, so `ratio_hilly >= ratio_flat` only removes
#   grid cells. Searched one at a time, each blocked the other.
# - `minetti_downhill_cost_slope` is shared, since core has one Minetti curve.
#   It moves where descents cross flat speed and `downhill_fill` scales them,
#   so the two trade-off across rounds; read the pair's plateaus together.
#   It only reaches Minetti-paced activities and the Tobler-weight probe.
# - `curve_reference_grade` isn't fitted. It only rescales the exponents the
#   fills already set, so the two slide along a ridge of equal scores.
# - `hilly_verticality_band` isn't fitted: a corpus this size can't tell a
#   wide ramp from a narrow one.

# Ranges are wide enough to contradict today's values. A fit that can only
# confirm the prior isn't worth running.
STAGES = (
    Stage("curve shape", {
        "uphill_fill": (0.10, 0.95, 0.05),
        # Exactly 0 is an invalid exponent. Wide at the top: once the Minetti
        # descent slope puts descents below flat speed, the fill scales how far
        # below, and at slope 20 the corpus wants about 0.8 (plateau 0.6-1.1).
        "downhill_fill": (0.01, 1.50, 0.01),
    }, per_sport=True),
    Stage("descent cost", {
        "minetti_downhill_cost_slope": (0.0, 40.0, 2.0),
    }, per_sport=False),
    Stage("speed bound", {
        "ratio_flat": (1.2, 3.0, 0.1),
        "ratio_hilly": (1.2, 4.0, 0.1),
    }, per_sport=True),
    Stage("hilliness ramp", {
        "hilly_verticality": (0.02, 0.16, 0.01),
    }, per_sport=False),
)

GLOBAL_KNOBS = {name: bounds for stage in STAGES if not stage.per_sport for name, bounds in stage.knobs.items()}
SPORT_KNOBS = {name: bounds for stage in STAGES if stage.per_sport for name, bounds in stage.knobs.items()}

# Scores within this relative share of the block's best count as equally good,
# and of those, the cell nearest the incumbent wins (the incumbent itself if it
# qualifies). So a block only moves for a real gain, and a knob the corpus
# can't see stays put instead of drifting. On the 2026-09-24 corpus, without
# this, `ratio_flat` and `hilly_verticality` wandered across their whole
# ranges, round after round, for about 0.05% of objective, and hiking's
# `ratio_flat` landed on a search bound it had no evidence for.
MIN_GAIN_SHARE = 0.001

# A knob's plateau is every grid value whose best score is within this share
# of the block's best. It shows how firmly the corpus pins the value; it is not
# a confidence interval. A first guess.
PLATEAU_SHARE = 0.005

# Two activities of one sport count as the same route when their distances
# differ by about this share or less, and their verticality (mean |gradient|)
# by about this much. Repeats of one loop in the corpus land within ~3% and
# ~0.01. The two are combined as an ellipse (see `_shape_gap`).
SAME_ROUTE_DISTANCE_TOLERANCE = 0.08
SAME_ROUTE_VERTICALITY_TOLERANCE = 0.012

# The sweep's lowest speed bound. At 1.0 the bound's log is zero and every
# curve exponent divides by it.
_MIN_RATIO = 1.05


@dataclass(frozen=True)
class ActivityOptimum:
    """What one activity wanted, next to what the resolvers gave it.

    Attributes:
        name: The activity's identifier.
        sport: Its sport.
        distance_m: Route distance.
        moving_seconds: Moving time.
        verticality: Mean |gradient|, the feature `resolve_max_speed_ratio`
            reads.
        flat_equivalent_mps: Terrain-normalized speed, the feature
            `resolve_tobler_weight` reads.
        resolved_tobler_weight: What the current constants chose.
        resolved_max_speed_ratio: What the current constants chose.
        resolved_objective: The objective at those choices.
        best_tobler_weight: What this activity wanted.
        best_max_speed_ratio: What this activity wanted.
        best_objective: The objective there: the best any resolver could do
            for this activity with the other constants unchanged.
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
        """How much of this activity's error better resolver choices could remove.

        Large headroom: the resolvers picked the wrong weight or bound. Small
        headroom with a high objective: no weight or bound fixes it, so the
        curve shape (the fills) is what's wrong.
        """
        return self.resolved_objective - self.best_objective


@dataclass(frozen=True)
class StageStep:
    """One block's result in one round of the corpus fit.

    Attributes:
        round: 1-based round number.
        stage: The stage's name.
        sport: The sport it was fitted for, or None for a shared stage.
        values: The block's knob values afterward.
        changed: Whether the block moved.
        train_objective: The whole training split's objective afterward.
        plateau: knob -> (low, high), the grid values scoring within
            PLATEAU_SHARE of the block's best. A wide plateau means the corpus
            barely constrains that knob, so read its value loosely.
    """
    round: int
    stage: str
    sport: SportType | None
    values: dict[str, float]
    changed: bool
    train_objective: float
    plateau: dict[str, tuple[float, float]]


@dataclass(frozen=True)
class FitResult:
    """The outcome of fitting the constants to a corpus.

    Attributes:
        base_params: The constants the fit started from.
        global_params: `base_params.shared` with the fitted shared constants.
        sport_params: The per-sport knobs the fit searched, keyed by sport.
            Only sports present in the corpus, and only the fitted knobs.
        proposal: The whole result as `--params` reads it back: the fitted
            shared constants, and per sport the fitted knobs on top of any
            other overrides `base_params` carried (a sport the corpus didn't
            have keeps its overrides untouched).
        train_before: Training-split objective before the fit.
        train_after: The same, after.
        test_before: Held-out objective before the fit.
        test_after: The same, after. If this improves far less than
            `train_after`, the fit is memorizing the training activities.
        train_names: Which activities were trained on.
        test_names: Which were held out.
        history: Every block of every round, in order. A block that still moves
            in the last round hasn't settled.
        route_groups: Groups `group_by_shape` kept on one side of the split.
            Singletons are left out.
    """
    base_params: ParamsBySport
    global_params: PacingParams
    sport_params: dict[SportType, dict[str, float]]
    proposal: ParamsBySport
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
        return self.proposal.for_sport(sport)

    def boundary_hits(self) -> list[str]:
        """Constants that settled on the edge of their search range.

        An edge value is a floor or ceiling, not an answer: the corpus wanted
        to go further and the grid stopped it. Widen the range and refit, or
        read it as "at least this much".
        """
        hits = []

        def check(label: str, check_value: float, check_low: float, check_high: float) -> None:
            for edge, bound in (("low", check_low), ("high", check_high)):
                if math.isclose(check_value, bound):
                    hits.append(f"{label} = {check_value:.4f} (at the {edge} end of {check_low}-{check_high})")

        for name, (low, high, _) in GLOBAL_KNOBS.items():
            value = getattr(self.global_params, name)
            if value is not None:
                check(name, value, low, high)
        for sport, values in self.sport_params.items():
            for name, value in values.items():
                low, high, _ = SPORT_KNOBS[name]
                check(f"{sport.value} {name}", value, low, high)
            # The constraint is an edge too: the corpus wanted hilly
            # activities to swing no more than flat ones, possibly less,
            # which the model can't express.
            if math.isclose(values["ratio_hilly"], values["ratio_flat"]):
                hits.append(f"{sport.value} ratio_hilly = {values['ratio_hilly']:.4f}"
                            f" (at the ratio_flat floor; hilly can't swing less than flat)")
        return hits


class _Evaluator:
    """Scores activities, building each one's ActivityContext once up front.

    A context holds everything no searched constant can change: gradients,
    raw curves, bucket layout, real seconds per bucket. A fit makes thousands
    of evaluations over the same activities, so building it once is most of
    the speed.

    Each context is built for the `gradient_window_m` its sport runs with in
    `base`, not the default, so a `--params` file that changes the window is
    honored. `compare._evaluate` rejects any later evaluation at a different
    window.
    """

    def __init__(
        self,
        activities: list[PreparedActivity],
        base: ParamsBySport | None = None,
        bucket_m: float = DEFAULT_BUCKET_M,
    ):
        base = base or ParamsBySport()
        self.activities = activities
        self.bucket_m = bucket_m
        self._contexts = {
            activity.name: ActivityContext.build(activity, base.for_sport(activity.sport), bucket_m)
            for activity in activities
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

        Descent revisits points constantly: each block re-scores its incumbent,
        and a settled round repeats the last one's grid. PacingParams is frozen
        and hashable, so a revisit costs a dict lookup.
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

        Weighted by distance, like the buckets inside an activity, so a long
        mountain day counts for more than a short jog.

        Args:
            params_for: SportType -> the PacingParams to run that sport with.
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
    """Find the per-workout settings (tobler_weight, max_speed_ratio) this one activity wanted.

    The `sweep` command's search, independent of `fit_constants`: it changes
    no constant, only the two settings the resolvers would otherwise derive
    from the terrain. Pins both in `params` (bypassing the resolvers), scores
    a coarse grid, then refines around the best cell one setting at a time,
    halving the step each round. Every constant stays as given.

    Args:
        activity: The prepared activity.
        params: The constants to hold fixed, for this activity's sport.
            Defaults to the shipped values.
        bucket_m: Residual bucket length.
    Returns:
        The optimum, the activity's features, and what the resolvers chose.
    """
    params = params or PacingParams()
    context = ActivityContext.build(activity, params, bucket_m)

    def score(s_weight: float, s_ratio: float) -> float:
        return objective_of(context, replace(params, tobler_weight=s_weight, max_speed_ratio=s_ratio))

    best_objective, best_weight, best_ratio = min(
        (score(weight, ratio), weight, ratio) for weight in _WEIGHT_GRID for ratio in _RATIO_GRID
    )

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


def track_shape(activity: PreparedActivity) -> tuple[float, float]:
    """(distance in metres, verticality), what `group_by_shape` compares."""
    model = TrackModel.build(activity.track)
    return model.total_distance_m, model.verticality


def _shape_gap(first: tuple[float, float], second: tuple[float, float]) -> float:
    """How far apart two track shapes are, where 1.0 is the edge of "same route".

    Distance is compared as a log ratio so the tolerance is a share, not metres.
    """
    distance_gap = abs(math.log(first[0] / second[0])) / SAME_ROUTE_DISTANCE_TOLERANCE
    verticality_gap = abs(first[1] - second[1]) / SAME_ROUTE_VERTICALITY_TOLERANCE
    return math.hypot(distance_gap, verticality_gap)


def group_by_shape(
    activities: list[PreparedActivity],
    shapes: dict[str, tuple[float, float]] | None = None,
) -> list[list[PreparedActivity]]:
    """Group activities that look like the same route, so the split can keep them together.

    A corpus is mostly a few home routes done repeatedly. Training on one run
    of a loop and holding out another measures memory of the loop, not
    generalization.

    Compares shape (distance, verticality) rather than start location, because
    shape is what the model sees. Uses agglomerative clustering with complete
    linkage: two groups merge only while every pair across them is within
    tolerance. Single linkage would chain a dense 6, 6.5, 7, 7.5 km run range
    into one blob.

    Args:
        activities: The corpus.
        shapes: name -> (distance_m, verticality), if already known. Computed
            with `track_shape` otherwise.
    Returns:
        The groups, each sorted by name, ordered by their first name.
    """
    if shapes is None:
        shapes = {activity.name: track_shape(activity) for activity in activities}

    groups = [[activity] for activity in sorted(activities, key=lambda act: act.name)]

    def linkage(first: list[PreparedActivity], second: list[PreparedActivity]) -> float:
        if first[0].sport != second[0].sport:
            return math.inf
        return max(_shape_gap(shapes[a.name], shapes[b.name]) for a in first for b in second)

    # Merge the closest pair until no pair is within tolerance.
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
    """Split into train and test per sport, moving same-route groups whole.

    Per sport, because the per-sport constants need training activities of
    their own sport. Groups are shuffled, and each one is held out only if
    that brings the held-out count closer to the target, so the share lands
    near `holdout` rather than exactly on it.

    Args:
        activities: The corpus.
        holdout: Fraction to hold out; 0.0 trains on everything.
        seed: Fixes the shuffle, so a fit is reproducible.
        groups: From `group_by_shape`, if already computed.
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
        # Always leave a sport at least two activities to fit on.
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


def _ratios_ordered(values: dict[str, float]) -> bool:
    """Whether `ratio_hilly >= ratio_flat`, which the model assumes: hills swing at least as much as flats."""
    return values["ratio_hilly"] >= values["ratio_flat"]


def _search_block(
    evaluator: _Evaluator,
    activities: list[PreparedActivity],
    apply,
    knobs: dict[str, tuple[float, float, float]],
    current: dict[str, float],
) -> tuple[dict[str, float], dict[str, tuple[float, float]]]:
    """Search one block's knobs jointly over their full grid.

    Joint, because knobs in a block trade off: a one-at-a-time search stalls
    where only a move in both would help.

    Every cell within MIN_GAIN_SHARE of the best counts as equally good, and
    the one nearest the incumbent wins. The incumbent is kept if it is among
    them.

    Args:
        evaluator: Scores the corpus.
        activities: Which activities to score.
        apply: trial knob dict -> the SportType-to-PacingParams callable to
            score with.
        knobs: name -> (low, high, step) for the block's knobs.
        current: Every knob's value, including those outside the block, which
            stay fixed. Not mutated.
    Returns:
        (the knob values with the chosen cell in place, each block knob's
        plateau; see StageStep.plateau).
    """
    names = list(knobs)
    grids = [_grid(*knobs[name]) for name in names]
    constrained = "ratio_flat" in names or "ratio_hilly" in names

    cells: list[tuple[tuple[float, ...], float]] = []
    for cell in itertools.product(*grids):
        trial = {**current, **dict(zip(names, cell))}
        if constrained and not _ratios_ordered(trial):
            continue
        cells.append((cell, evaluator.corpus_objective(apply(trial), activities)))
    if not cells:
        return dict(current), {}

    best_score = min(score for _, score in cells)
    plateau = {}
    for index, name in enumerate(names):
        # Profile each knob: its best score at each value, over the rest of the block.
        profile: dict[float, float] = {}
        for cell, score in cells:
            profile[cell[index]] = min(score, profile.get(cell[index], math.inf))
        near = [value for value, score in profile.items() if score <= best_score * (1 + PLATEAU_SHARE)]
        plateau[name] = (min(near), max(near))

    good_enough = best_score * (1 + MIN_GAIN_SHARE)
    if evaluator.corpus_objective(apply(current), activities) <= good_enough:
        return dict(current), plateau

    def distance_from_current(_cell: tuple[float, ...]) -> float:
        return sum(
            ((value - current[name]) / (knobs[name][1] - knobs[name][0])) ** 2
            for name, value in zip(names, _cell)
        )

    chosen = min((cell for cell, score in cells if score <= good_enough), key=distance_from_current)
    return {**current, **dict(zip(names, chosen))}, plateau


def fit_constants(
    activities: list[PreparedActivity],
    base: ParamsBySport | PacingParams | None = None,
    rounds: int = 3,
    holdout: float = 0.3,
    seed: int = 20260920,
    bucket_m: float = DEFAULT_BUCKET_M,
) -> FitResult:
    """Fit the constants on a training split and score them on a held-out one.

    The `fit` command's search, independent of `sweep_activity`: it searches
    the constants the resolvers read, never the per-workout settings directly.
    Runs the `STAGES` blocks in order (per-sport blocks once for each sport,
    on that sport's training activities) and repeats until a round moves
    nothing or `rounds` runs out.

    Per-sport knobs start from each sport's own value in `base`, so a
    previous `proposed.json` passed back in resumes where it left off. A
    per-sport override of a knob the fit shares across sports is dropped:
    that knob gets one fitted value for everyone.

    Args:
        activities: The prepared corpus.
        base: Constants to start from. A bare PacingParams means the same
            constants for every sport. Defaults to the shipped values, so the
            result reads as a diff against what ships.
        rounds: The most passes through `STAGES`.
        holdout: Fraction to hold out, per sport and by same-route group
            (see `split_corpus`).
        seed: Fixes the split.
        bucket_m: Residual bucket length.
    Returns:
        The fitted constants with before/after objectives on both splits.
    Raises:
        ValueError: If the corpus is empty.
    """
    if not activities:
        raise ValueError("Need at least one prepared activity to fit against.")

    if base is None:
        base = ParamsBySport()
    elif isinstance(base, PacingParams):
        base = ParamsBySport(base)
    evaluator = _Evaluator(activities, base, bucket_m)
    groups = group_by_shape(activities, evaluator.shapes())
    train, test = split_corpus(activities, holdout, seed, groups)
    sports = sorted({activity.sport for activity in activities}, key=lambda value: value.value)

    global_values = {name: getattr(base.shared, name) for name in GLOBAL_KNOBS}
    sport_values: dict[SportType, dict[str, float]] = {}
    for sport in sports:
        start = base.for_sport(sport)
        # A PacingParams leaves the ratios unset to mean "the sport's shipped
        # bounds", so fill those in explicitly: the search needs a number to
        # start from.
        bounds = MAX_SPEED_RATIO_BOUNDS[sport]
        sport_values[sport] = {
            "uphill_fill": start.uphill_fill,
            "downhill_fill": start.downhill_fill,
            "ratio_flat": start.ratio_flat if start.ratio_flat is not None else bounds.flat,
            "ratio_hilly": start.ratio_hilly if start.ratio_hilly is not None else bounds.hilly,
        }

    # Overrides in `base` that no block searches ride along unchanged.
    carried = {
        sport: {name: value for name, value in overrides.items() if name not in GLOBAL_KNOBS}
        for sport, overrides in base.per_sport.items()
    }

    def proposal_of(globals_: dict[str, float], per_sport: dict[SportType, dict[str, float]]) -> ParamsBySport:
        """These knob values as a complete set of constants."""
        return ParamsBySport(replace(base.shared, **globals_), {
            s: {**carried.get(s, {}), **per_sport.get(s, {})}
            for s in carried.keys() | per_sport.keys()
        })

    def build(globals_: dict[str, float], per_sport: dict[SportType, dict[str, float]]):
        """The SportType -> PacingParams callable for these knob values."""
        return proposal_of(globals_, per_sport).for_sport

    before_params = build(dict(global_values), {sport: dict(values) for sport, values in sport_values.items()})
    train_before = evaluator.corpus_objective(before_params, train)
    test_before = evaluator.corpus_objective(before_params, test) if test else 0.0

    history: list[StageStep] = []

    def record(r_round_number: int, r_stage: Stage, r_sport: SportType | None, r_values: dict[str, float],
               r_changed: bool, r_plateau: dict[str, tuple[float, float]]) -> None:
        history.append(StageStep(
            r_round_number, r_stage.name, r_sport, {name: r_values[name] for name in r_stage.knobs}, r_changed,
            evaluator.corpus_objective(build(global_values, sport_values), train), r_plateau,
        ))

    for round_number in range(1, rounds + 1):
        moved = False
        for stage in STAGES:
            if stage.per_sport:
                for sport in sports:
                    sport_train = [activity for activity in train if activity.sport == sport]
                    if not sport_train:
                        continue
                    before = sport_values[sport]
                    sport_values[sport], plateau = _search_block(
                        evaluator, sport_train,
                        lambda trial, s=sport: build(global_values, {**sport_values, s: trial}),
                        stage.knobs, before,
                    )
                    changed = sport_values[sport] != before
                    moved |= changed
                    record(round_number, stage, sport, sport_values[sport], changed, plateau)
            else:
                before = global_values
                global_values, plateau = _search_block(
                    evaluator, train, lambda trial: build(trial, sport_values), stage.knobs, before,
                )
                changed = global_values != before
                moved |= changed
                record(round_number, stage, None, global_values, changed, plateau)
        if not moved:
            break

    proposal = proposal_of(global_values, sport_values)
    return FitResult(
        base_params=base,
        global_params=proposal.shared,
        sport_params=sport_values,
        proposal=proposal,
        train_before=train_before,
        train_after=evaluator.corpus_objective(proposal.for_sport, train),
        test_before=test_before,
        test_after=evaluator.corpus_objective(proposal.for_sport, test) if test else 0.0,
        train_names=[activity.name for activity in train],
        test_names=[activity.name for activity in test],
        history=history,
        route_groups=[[activity.name for activity in group] for group in groups if len(group) > 1],
    )
