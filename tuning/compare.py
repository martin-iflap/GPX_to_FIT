"""Measure how far the model's pacing sits from what the athlete actually did.

Two things make the naive comparison — per-point timestamp differences —
useless here.

First, `combine()` scales every segment so its total time matches the anchors
exactly, so with start and end as the only anchors the total error is zero by
construction. Only the *shape* of the time-vs-distance curve is a prediction.

Second, a 1 Hz recording has legs about three metres long, where the measured
speed is mostly GPS jitter. Residuals are therefore computed on distance
buckets at or above `calculate_gradient`'s own 70 m smoothing window — below
that the model isn't claiming anything and the recording can't answer.

The residual itself is `log(model_speed / real_speed)` per bucket. Log-space
because that is the space the model works in (curve blending, the tanh bound
and the smoothness scale are all log-linear), and because it makes "12% too
fast" symmetric with "12% too slow". Both speeds are over the same distance
with the same total time, so absolute fitness has already canceled — which is
exactly the part of an athlete that shouldn't generalize anyway.

Recorded elevation can't be fully trusted, so two parts of the scoring are
robust on purpose. Buckets steeper than `MAX_SCORED_GRADIENT` are left out of
the objective. A residual past `HUBER_DELTA` counts linearly rather than
squared, so no single stretch can dominate.

**Two ways in, one calculation.** `compare` answers "how did this activity do?"
and builds the whole report. `objective_of` answers only "what is the single
number?", for the thousands of evaluations a sweep or a fit makes. Both go
through `_evaluate`, so the number a fit minimizes is by construction the
number a report prints. What makes the second cheap is `ActivityContext`: the
bucket layout, the real seconds per bucket and the `TrackModel` behind them
depend on the activity and the gradient window, never on the constants being
searched, so they are computed once and reused.
"""

import math
from dataclasses import dataclass

import numpy as np

from gpx2fit.core.models import SportType
from tuning.model import PacingParams, ResolvedSettings, TrackModel, elapsed_from_model
from tuning.prepare import PreparedActivity

# Residual buckets are at least this long. Anything shorter is inside the
# gradient smoothing window, where the model has deliberately stopped
# resolving detail and the GPS never did.
DEFAULT_BUCKET_M = 100.0

# Upper edges of the gradient bands the residuals are grouped into. Narrow
# around flat (where most distance falls and small errors matter) and wide at
# the extremes (where there is rarely enough distance to say much). The outer
# +/-40% edges match MAX_SCORED_GRADIENT, so unscored buckets get their own rows.
GRADIENT_BAND_EDGES = (
    -0.40, -0.25, -0.18, -0.12, -0.08, -0.04, -0.015, 0.015, 0.04, 0.08, 0.12, 0.18, 0.25, 0.40,
)

# Buckets steeper than this either way are paced but not scored. On the real
# corpus (2026-09-23), running buckets above +40% made up 3% of running distance
# and were covered at 4.2x the activity's mean speed. Those are elevation jumps,
# not running. Between 25% and 40% speeds still fall with steepness the way real
# climbing does, so the cut sits where the data breaks, not at the last band.
MAX_SCORED_GRADIENT = 0.40

# Residuals beyond this (in log space, about +/-35%) count linearly instead of
# squared. Squaring let a few bad buckets outweigh everything else: before the cut
# above, the broken running buckets alone were about half of running's squared
# error. Inside the threshold the loss is exactly the squared residual, so a
# clean activity scores the same as it did under plain RMS.
HUBER_DELTA = 0.3


@dataclass(frozen=True)
class Bucket:
    """One distance bucket's model-versus-reality comparison.

    Attributes:
        start_m: Distance from the activity start where the bucket begins.
        distance_m: The bucket's own length.
        gradient: Distance-weighted mean gradient over the bucket's legs.
        model_seconds: Time the model gave this bucket.
        real_seconds: Time the athlete actually took.
        model_relative_speed: Model speed as a multiple of the activity's mean.
        real_relative_speed: Real speed as a multiple of the activity's mean.
        log_residual: log(model_speed / real_speed). Positive means the model
            ran this stretch faster than the athlete did.
        scored: Whether this bucket counts toward the objective. False past
            MAX_SCORED_GRADIENT, where the elevation data can't be trusted.
    """
    start_m: float
    distance_m: float
    gradient: float
    model_seconds: float
    real_seconds: float
    model_relative_speed: float
    real_relative_speed: float
    log_residual: float
    scored: bool = True


@dataclass(frozen=True)
class GradientBand:
    """Every bucket in one gradient range, pooled.

    `real_relative_speed` is the empirically observed gradient-to-speed curve —
    directly comparable to what Minetti and Tobler predict, which is what makes
    this table the thing to read when deciding where the curve is wrong.

    Attributes:
        low: Lower gradient edge, -inf for the first band.
        high: Upper gradient edge, +inf for the last.
        bucket_count: How many buckets fell in this band.
        distance_m: Total distance in the band.
        model_relative_speed: Distance-weighted mean model speed, relative to
            the activity's mean.
        real_relative_speed: The same for the athlete.
        log_residual: Distance-weighted mean log residual.
        shape_residual: `log_residual` with the activity's overall mean
            residual subtracted. This is the part worth acting on — see
            ActivityReport.mean_log_residual for why the level isn't.
    """
    low: float
    high: float
    bucket_count: int
    distance_m: float
    model_relative_speed: float
    real_relative_speed: float
    log_residual: float
    shape_residual: float


@dataclass(frozen=True)
class ActivityReport:
    """Everything one activity says about the model, at one set of parameters.

    Attributes:
        name: The activity's identifier.
        sport: Which sport's constants were used.
        distance_m: Route distance.
        moving_seconds: Moving time, which the model matched exactly.
        settings: What the model resolved for this workout, and the features
            behind it — the regressors stage two fits against.
        objective: Distance-weighted root-mean Huber loss of the scored
            buckets' log residuals. It equals their RMS whenever no residual
            exceeds HUBER_DELTA. The single number a sweep minimizes.
        unscored_distance_m: Distance in buckets past MAX_SCORED_GRADIENT,
            which are paced and listed but left out of the objective.
        mean_log_residual: Distance-weighted mean log residual. Near zero by
            construction and *not* a finding: the model matched the activity's
            total time exactly, so running too fast in one band forces running
            too slow across the rest. Only the spread of residuals across
            gradient bands is identifiable, which is what `shape_residual`
            isolates — read that column, not this one.
        max_time_error_seconds: Largest gap between the modeled and real
            clock at the same point on the route, signed.
        max_time_error_at_m: Where that happened.
        rms_time_error_seconds: RMS of the same signed error over all points.
        buckets: Every bucket, in route order.
        bands: The buckets pooled by gradient.
        notes: Quality observations carried through from preparation.
    """
    name: str
    sport: SportType
    distance_m: float
    moving_seconds: float
    settings: ResolvedSettings
    objective: float
    unscored_distance_m: float
    mean_log_residual: float
    max_time_error_seconds: float
    max_time_error_at_m: float
    rms_time_error_seconds: float
    buckets: list[Bucket]
    bands: list[GradientBand]
    notes: list[str]

    @property
    def time_error_share(self) -> float:
        """The worst clock gap as a fraction of the activity's moving time."""
        return abs(self.max_time_error_seconds) / self.moving_seconds if self.moving_seconds > 0 else 0.0


def _bucket_boundaries(distances: np.ndarray, bucket_m: float) -> list[int]:
    """Point indexes splitting the track into buckets of at least `bucket_m`.

    Always starts at 0 and ends at the last point. A short final bucket is
    merged backwards rather than left to stand on its own, since a 12 m tail
    would carry a meaningless speed.
    """
    boundaries = [0]
    accumulated = 0.0
    for index, leg in enumerate(distances.tolist(), start=1):
        accumulated += leg
        if accumulated >= bucket_m:
            boundaries.append(index)
            accumulated = 0.0
    last = len(distances)
    if boundaries[-1] != last:
        if len(boundaries) > 1 and accumulated < bucket_m / 2:
            boundaries[-1] = last
        else:
            boundaries.append(last)
    return boundaries


@dataclass(frozen=True)
class ActivityContext:
    """One activity laid out for comparison, with everything parameter-free precomputed.

    A sweep scores the same activity hundreds of times and a fit thousands,
    always over the same buckets against the same recording. Only the model's
    own seconds change between evaluations, so everything else — the bucket
    layout, each bucket's distance, mean gradient and real time, and the
    `TrackModel` carrying the curves — is computed once, here.

    The one thing that *would* invalidate it is a different
    `gradient_window_m`, since that changes both the gradients and the minimum
    bucket length. `_evaluate` checks for that rather than trusting a caller to
    remember it.

    Attributes:
        prepared: The activity this was built from.
        model: Its TrackModel, holding the gradients, leg distances and curves.
        bucket_m: The bucket length actually used, after the window floor.
        multipliers: Optional per-leg surface multipliers, for the surface phase.
        lo: First point index of each bucket.
        hi: Last point index of each bucket.
        start_m: Where each bucket begins, in metres from the start.
        bucket_distance: Each bucket's length.
        bucket_gradient: Each bucket's distance-weighted mean gradient.
        real_seconds: What the athlete took over each bucket.
        mean_speed: The activity's overall mean speed, which the reported
            relative speeds are expressed against.
        scored: Per bucket, whether it counts toward the objective.
        huber_delta: Where the loss turns from squared to linear; None for
            plain RMS.
    """
    prepared: PreparedActivity
    model: TrackModel
    bucket_m: float
    multipliers: np.ndarray | None
    lo: np.ndarray
    hi: np.ndarray
    start_m: np.ndarray
    bucket_distance: np.ndarray
    bucket_gradient: np.ndarray
    real_seconds: np.ndarray
    mean_speed: float
    scored: np.ndarray
    huber_delta: float | None

    @classmethod
    def build(
        cls,
        prepared: PreparedActivity,
        params: PacingParams | None = None,
        bucket_m: float = DEFAULT_BUCKET_M,
        multipliers: list[float] | None = None,
        gradients: list[float] | None = None,
        max_gradient: float | None = MAX_SCORED_GRADIENT,
        huber_delta: float | None = HUBER_DELTA,
    ) -> "ActivityContext":
        """Lay an activity out for scoring.

        Args:
            prepared: The activity, from `prepare.prepare_reference`.
            params: The constants this context will be scored at. Only
                `gradient_window_m` is read — it fixes the gradients and the
                bucket floor, and every later evaluation must agree with it.
            bucket_m: Residual bucket length. Values below the gradient window
                are raised to it: finer than the gradient smoothing there is
                nothing for the model to be right or wrong about.
            multipliers: Optional per-leg surface multipliers.
            gradients: Already-computed gradients for this track and window.
            max_gradient: Buckets steeper than this either way are left out of
                the objective. None scores every bucket.
            huber_delta: See HUBER_DELTA. None scores with plain RMS.

        Raises:
            ValueError: If the activity has no distance, no moving time, or no
                bucket that carries pace information.
        """
        params = params or PacingParams()
        track = prepared.track
        if track.total_distance <= 0 or prepared.moving_seconds <= 0:
            raise ValueError(f"{prepared.name}: needs positive distance and moving time to compare.")

        model = TrackModel.build(track, params, gradients)
        bucket_m = max(bucket_m, params.gradient_window_m)

        boundaries = np.asarray(_bucket_boundaries(model.leg_distances, bucket_m))
        lo, hi = boundaries[:-1], boundaries[1:]

        point_distance = np.asarray([point.distance_from_start for point in track.points], dtype=float)
        reference = np.asarray(prepared.reference_elapsed, dtype=float)
        bucket_distance = point_distance[hi] - point_distance[lo]
        real_seconds = reference[hi] - reference[lo]

        # A zero-distance or zero-time bucket carries no pace information.
        # prepare() already removed stops, so this is rare and not an error.
        keep = (bucket_distance > 0) & (real_seconds > 0)
        lo, hi = lo[keep], hi[keep]
        bucket_distance, real_seconds = bucket_distance[keep], real_seconds[keep]
        if len(lo) == 0:
            raise ValueError(f"{prepared.name}: no usable residual buckets.")

        # Distance-weighted mean gradient per bucket, from running totals so
        # each bucket costs a subtraction rather than a pass over its legs.
        weighted = np.concatenate(([0.0], np.cumsum(model.gradients * model.leg_distances)))
        spanned = np.concatenate(([0.0], np.cumsum(model.leg_distances)))
        span = spanned[hi] - spanned[lo]
        bucket_gradient = np.where(span > 0, (weighted[hi] - weighted[lo]) / np.where(span > 0, span, 1.0), 0.0)

        scored = (
            np.ones(len(lo), dtype=bool) if max_gradient is None
            else np.abs(bucket_gradient) <= max_gradient
        )
        if not scored.any():
            raise ValueError(f"{prepared.name}: every bucket is steeper than {max_gradient:.0%}; nothing to score.")

        return cls(
            prepared=prepared,
            model=model,
            bucket_m=bucket_m,
            multipliers=None if multipliers is None else np.asarray(multipliers, dtype=float),
            lo=lo,
            hi=hi,
            start_m=point_distance[lo],
            bucket_distance=bucket_distance,
            bucket_gradient=bucket_gradient,
            real_seconds=real_seconds,
            mean_speed=track.total_distance / prepared.moving_seconds,
            scored=scored,
            huber_delta=huber_delta,
        )


@dataclass(frozen=True)
class _Evaluation:
    """One activity scored at one set of parameters — everything both callers need."""
    settings: ResolvedSettings
    predicted: np.ndarray
    keep: np.ndarray
    scored: np.ndarray
    model_seconds: np.ndarray
    model_speed: np.ndarray
    real_speed: np.ndarray
    log_residual: np.ndarray
    objective: float
    unscored_distance_m: float
    mean_log_residual: float


def _root_mean_loss(residual: np.ndarray, weight: np.ndarray, huber_delta: float | None) -> float:
    """Weighted root-mean Huber loss, scaled so it reads as an RMS.

    Squared inside `huber_delta` and linear outside it. The linear part is
    written as 2·delta·|r| - delta², which joins the squared part smoothly at
    the threshold. So with no residual past the threshold this is exactly the
    weighted RMS, and numbers stay comparable with runs from before the
    change.
    """
    magnitude = np.abs(residual)
    if huber_delta is None:
        loss = magnitude ** 2
    else:
        loss = np.where(magnitude <= huber_delta, magnitude ** 2, 2 * huber_delta * magnitude - huber_delta ** 2)
    return float(math.sqrt((loss * weight).sum() / weight.sum()))


def _evaluate(context: ActivityContext, params: PacingParams) -> _Evaluation:
    """Pace the activity at these parameters and reduce it to bucketed residuals.

    The single place the objective is defined, so a fit and a report can never
    disagree about what they are measuring.

    Unscored buckets are still paced, since the app would pace them too, but
    the model's time is re-matched over the scored buckets alone. Without
    that, the time the model gives an unscored bucket would push every scored
    bucket's residual by the same amount. That shift depends on the
    parameters, so the fit would chase it, which is how bad elevation
    data steered the constants before. With nothing unscored the factor is
    1.0 and nothing changes.

    Raises:
        ValueError: If `params` uses a different gradient window than the
            context was laid out for, or if no bucket survives.
    """
    if params.gradient_window_m != context.model.gradient_window_m:
        raise ValueError(
            f"{context.prepared.name}: this context was built for a "
            f"{context.model.gradient_window_m:.0f} m gradient window but was scored at "
            f"{params.gradient_window_m:.0f} m. Build a new context for that window."
        )

    prepared = context.prepared
    predicted, settings = elapsed_from_model(
        context.model, prepared.sport, prepared.moving_seconds, params, context.multipliers
    )

    model_seconds = predicted[context.hi] - predicted[context.lo]
    keep = model_seconds > 0
    scored = context.scored[keep]
    if not scored.any():
        raise ValueError(f"{prepared.name}: no usable residual buckets.")

    real_seconds = context.real_seconds[keep]
    model_seconds = model_seconds[keep]
    model_seconds = model_seconds * (real_seconds[scored].sum() / model_seconds[scored].sum())
    distance = context.bucket_distance[keep]

    model_speed = distance / model_seconds
    real_speed = distance / real_seconds
    log_residual = np.log(model_speed / real_speed)

    scored_distance = distance[scored]
    return _Evaluation(
        settings=settings,
        predicted=predicted,
        keep=keep,
        scored=scored,
        model_seconds=model_seconds,
        model_speed=model_speed,
        real_speed=real_speed,
        log_residual=log_residual,
        objective=_root_mean_loss(log_residual[scored], scored_distance, context.huber_delta),
        unscored_distance_m=float(distance[~scored].sum()),
        mean_log_residual=float((log_residual[scored] * scored_distance).sum() / scored_distance.sum()),
    )


def objective_of(context: ActivityContext, params: PacingParams) -> float:
    """The activity's objective alone — the fast path a sweep or a fit runs.

    Identical to `compare(...).objective`, without building the report.
    See `_evaluate` for what is raised.
    """
    return _evaluate(context, params).objective


def _band_index(gradient: float) -> int:
    """Which gradient band a gradient falls in."""
    for index, edge in enumerate(GRADIENT_BAND_EDGES):
        if gradient < edge:
            return index
    return len(GRADIENT_BAND_EDGES)


def _band_edges(index: int) -> tuple[float, float]:
    """The (low, high) gradient edges of a band, unbounded at the extremes."""
    low = -math.inf if index == 0 else GRADIENT_BAND_EDGES[index - 1]
    high = math.inf if index == len(GRADIENT_BAND_EDGES) else GRADIENT_BAND_EDGES[index]
    return low, high


def _pool_into_bands(buckets: list[Bucket], mean_log_residual: float) -> list[GradientBand]:
    """Group buckets by gradient band, distance-weighting everything inside each."""
    grouped: dict[int, list[Bucket]] = {}
    for bucket in buckets:
        grouped.setdefault(_band_index(bucket.gradient), []).append(bucket)

    bands = []
    for index in sorted(grouped):
        members = grouped[index]
        total_distance = sum(bucket.distance_m for bucket in members)
        if total_distance <= 0:
            continue

        def weighted(value_of) -> float:
            return sum(value_of(b) * b.distance_m for b in members) / total_distance

        low, high = _band_edges(index)
        residual = weighted(lambda buck: buck.log_residual)
        bands.append(GradientBand(
            low=low,
            high=high,
            bucket_count=len(members),
            distance_m=total_distance,
            model_relative_speed=weighted(lambda buck: buck.model_relative_speed),
            real_relative_speed=weighted(lambda buck: buck.real_relative_speed),
            log_residual=residual,
            shape_residual=residual - mean_log_residual,
        ))
    return bands


def report_of(context: ActivityContext, params: PacingParams | None = None) -> ActivityReport:
    """The full report for an already-laid-out activity.

    What `compare` does once it has a context; call this directly when the same
    activity is also being scored repeatedly, so the layout is shared.
    """
    params = params or PacingParams()
    prepared = context.prepared
    evaluation = _evaluate(context, params)
    keep = evaluation.keep

    buckets = [
        Bucket(
            start_m=start,
            distance_m=distance,
            gradient=gradient,
            model_seconds=model_seconds,
            real_seconds=real_seconds,
            model_relative_speed=model_speed / context.mean_speed,
            real_relative_speed=real_speed / context.mean_speed,
            log_residual=residual,
            scored=scored,
        )
        for start, distance, gradient, model_seconds, real_seconds, model_speed, real_speed, residual, scored in zip(
            context.start_m[keep].tolist(),
            context.bucket_distance[keep].tolist(),
            context.bucket_gradient[keep].tolist(),
            evaluation.model_seconds.tolist(),
            context.real_seconds[keep].tolist(),
            evaluation.model_speed.tolist(),
            evaluation.real_speed.tolist(),
            evaluation.log_residual.tolist(),
            evaluation.scored.tolist(),
        )
    ]

    errors = evaluation.predicted - np.asarray(prepared.reference_elapsed, dtype=float)
    worst = int(np.argmax(np.abs(errors)))

    return ActivityReport(
        name=prepared.name,
        sport=prepared.sport,
        distance_m=prepared.track.total_distance,
        moving_seconds=prepared.moving_seconds,
        settings=evaluation.settings,
        objective=evaluation.objective,
        unscored_distance_m=evaluation.unscored_distance_m,
        mean_log_residual=evaluation.mean_log_residual,
        max_time_error_seconds=float(errors[worst]),
        max_time_error_at_m=prepared.track.points[worst].distance_from_start,
        rms_time_error_seconds=float(math.sqrt((errors ** 2).sum() / len(errors))),
        buckets=buckets,
        bands=_pool_into_bands(buckets, evaluation.mean_log_residual),
        notes=list(prepared.notes),
    )


def compare(
    prepared: PreparedActivity,
    params: PacingParams | None = None,
    bucket_m: float = DEFAULT_BUCKET_M,
    multipliers: list[float] | None = None,
    gradients: list[float] | None = None,
) -> ActivityReport:
    """Run the model over a prepared activity and measure it against the recording.

    Args:
        prepared: The activity, from `prepare.prepare_reference`.
        params: The constants to run with. Defaults to today's shipped values.
        bucket_m: Residual bucket length. Values below the gradient window are
            raised to it — finer than the gradient smoothing there is nothing
            for the model to be right or wrong about.
        multipliers: Optional per-leg surface multipliers, for the surface phase.
        gradients: Already-computed gradients for this track and
            `params.gradient_window_m`.

    Returns:
        The report.
    Raises:
        ValueError: If the prepared activity has no distance, no moving time,
            or no bucket that carries pace information.
    """
    params = params or PacingParams()
    return report_of(ActivityContext.build(prepared, params, bucket_m, multipliers, gradients), params)
