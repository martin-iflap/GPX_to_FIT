"""Measure how far the model's pacing sits from what the athlete actually did.

Per-point timestamp differences don't work as a measure, for two reasons:

- `combine()` scales each segment to match its anchors exactly, so with only a
  start and end anchor the total error is zero by construction. Only the
  *shape* of the time-vs-distance curve is a prediction.
- At 1 Hz a leg is about three metres and its measured speed is mostly GPS
  jitter. So residuals are taken over distance buckets no shorter than the
  gradient smoothing window, below which the model claims nothing.

The residual is `log(model_speed / real_speed)` per bucket. Log space is the
space the model works in, and it makes 12% too fast and 12% too slow the same
size.

The objective scores only the *shape*: each residual minus the activity's
distance-weighted mean residual. Recordings aren't fully trustworthy, so the
scoring is also robust:

- Buckets steeper than `MAX_SCORED_GRADIENT` aren't scored, because that much
  gradient means bad elevation data.
- Flat buckets far slower than the activity's typical flat speed aren't
  scored either. That is dawdling the device didn't pause for, and no
  gradient curve can explain it.
- Residuals past `HUBER_DELTA` count linearly instead of squared.

`compare` builds the full report and `objective_of` returns only the score, for
the thousands of evaluations a fit makes. Both go through `_evaluate`, so the
number a fit minimizes is the number a report prints. `ActivityContext` keeps
the second path cheap by precomputing everything that doesn't depend on the
constants being searched.
"""

import bisect
import math
from dataclasses import dataclass

import numpy as np

from gpx2fit.core.models import SportType
from tuning.model import PacingParams, ResolvedSettings, TrackModel, elapsed_from_model
from tuning.prepare import PreparedActivity

# Minimum residual bucket length. It is raised to the gradient window if that
# is longer.
DEFAULT_BUCKET_M = 70.0

# Upper edges of the gradient bands in the report. Narrow around flat, where
# most of the distance is, and wide at the extremes, where there is little.
# The outer +/-40% edges match MAX_SCORED_GRADIENT, so unscored buckets get
# their own rows.
GRADIENT_BAND_EDGES = (
    -0.40, -0.25, -0.18, -0.12, -0.08, -0.04, -0.015, 0.015, 0.04, 0.08, 0.12, 0.18, 0.25, 0.40,
)

# Buckets steeper than this in either direction are paced but not scored. In
# the real corpus, running buckets above +40% were covered at about 4x the mean
# speed, which means bad elevation data rather than running. Up to 40%, speed
# still falls with steepness the way real climbing does.
MAX_SCORED_GRADIENT = 0.40

# Log residuals beyond this (about +/-35%) count linearly instead of squared,
# so a few broken buckets can't dominate an activity's score.
HUBER_DELTA = 0.3

# A bucket this flat (mean |gradient|) and slower than DAWDLE_SPEED_SHARE of
# the activity's median flat speed is dawdling, and isn't scored. Only flat
# buckets are checked: on slopes, slowness is what the curve has to explain.
# Leaving those buckets in pulls the flat band slow, which teaches the fit the
# wrong flat-ground pace. Both values are first guesses.
FLAT_GRADIENT = 0.04
DAWDLE_SPEED_SHARE = 0.6

# The gate needs this many flat buckets for the median flat speed to be
# meaningful. With fewer, nothing is marked as dawdling.
_MIN_FLAT_BUCKETS = 10

# A bucket spans mixed terrain when its gradient's distance-weighted standard
# deviation is above this and also above its mean |gradient|. That is a slope
# changing direction, such as a hilltop where +10% and -10% average to 0%. A
# steady 30% climb varying by +/-8% is not mixed. A mixed bucket's residual
# is still valid, so it stays in the objective. But its mean gradient
# misrepresents it, so it is left out of the band table and not tested for
# dawdling.
MAX_BAND_GRADIENT_SPREAD = 0.06


@dataclass(frozen=True)
class Bucket:
    """One distance bucket's model-versus-reality comparison.

    Attributes:
        start_m: Distance from the activity start where the bucket begins.
        distance_m: The bucket's length.
        gradient: Distance-weighted mean gradient over the bucket.
        gradient_spread: Distance-weighted standard deviation of the gradient
            over the bucket.
        mixed: Whether the bucket spans mixed terrain (see
            MAX_BAND_GRADIENT_SPREAD) and is left out of the band table.
        model_seconds: Time the model gave this bucket.
        real_seconds: Time the athlete actually took.
        model_relative_speed: Model speed as a multiple of the activity's mean.
        real_relative_speed: Real speed as a multiple of the activity's mean.
        log_residual: log(model_speed / real_speed). Positive means the model
            covered this stretch faster than the athlete did.
        excluded: Why the bucket isn't scored: "steep" or "dawdle". None if
            it is scored.
    """
    start_m: float
    distance_m: float
    gradient: float
    gradient_spread: float
    mixed: bool
    model_seconds: float
    real_seconds: float
    model_relative_speed: float
    real_relative_speed: float
    log_residual: float
    excluded: str | None = None


@dataclass(frozen=True)
class GradientBand:
    """Every scored, single-slope bucket in one gradient range, pooled.

    `real_relative_speed` across the bands is the athlete's measured
    gradient-to-speed curve, so it can be read directly against Minetti and
    Tobler. Dawdling and mixed-terrain buckets would distort that curve, so
    they're left out. Steep buckets sit outside the outer edges and get their
    own rows.

    Attributes:
        low: Lower gradient edge, -inf for the first band.
        high: Upper gradient edge, +inf for the last.
        bucket_count: How many buckets fell in this band.
        distance_m: Total distance in the band.
        model_relative_speed: Band distance over the model's band time,
            relative to the activity's mean speed.
        real_relative_speed: The same for the athlete.
        log_residual: Distance-weighted mean log residual.
        shape_residual: `log_residual` minus the activity's mean residual. This
            is the column to act on (see ActivityReport.mean_log_residual).
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
    """Everything one activity says about the model at one set of parameters.

    Attributes:
        name: The activity's identifier.
        sport: Which sport's constants were used.
        distance_m: Route distance.
        moving_seconds: Moving time, which the model matches exactly.
        settings: What the model resolved for this workout, and the features
            it resolved them from.
        objective: Distance-weighted root-mean Huber loss of the scored
            buckets' shape residuals (log residual minus `mean_log_residual`).
            It equals their RMS when none exceeds HUBER_DELTA. This is what a
            fit minimizes.
        steep_distance_m: Distance in buckets past MAX_SCORED_GRADIENT.
        dawdle_distance_m: Distance in buckets left out as dawdling.
        mixed_distance_m: Distance in mixed-terrain buckets, which are scored
            but left out of the band table.
        mean_log_residual: Distance-weighted mean log residual of the scored
            buckets. It is close to zero because the total time is matched, so
            it isn't a finding: being too fast in one band forces being too
            slow elsewhere. Only the spread around it can be identified, so
            the objective leaves it out.
        max_time_error_seconds: Largest signed gap between the model's clock
            and the real clock at the same point on the route.
        max_time_error_at_m: Where that gap happened.
        rms_time_error_seconds: RMS of that signed error over all points.
        buckets: Every bucket, in route order.
        bands: The buckets pooled by gradient.
        notes: Quality notes carried over from preparation.
    """
    name: str
    sport: SportType
    distance_m: float
    moving_seconds: float
    settings: ResolvedSettings
    objective: float
    steep_distance_m: float
    dawdle_distance_m: float
    mixed_distance_m: float
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

    Always starts at 0 and ends at the last point. A final bucket shorter than
    half of `bucket_m` is merged into the one before it, because a short tail
    has an unreliable speed.
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
    """One activity laid out for scoring, with everything parameter-free precomputed.

    Between evaluations only the model's own seconds change. The bucket layout,
    each bucket's distance, gradient and real time, and the `TrackModel` are
    computed once, here. They do depend on `gradient_window_m`, so `_evaluate`
    refuses params that use a different window.

    Attributes:
        prepared: The activity this was built from.
        model: Its TrackModel.
        bucket_m: The bucket length used, after raising it to the gradient
            window.
        multipliers: Optional per-leg surface multipliers.
        lo: First point index of each bucket.
        hi: Last point index of each bucket.
        start_m: Where each bucket begins, in metres from the start.
        bucket_distance: Each bucket's length.
        bucket_gradient: Each bucket's distance-weighted mean gradient.
        gradient_spread: Each bucket's distance-weighted gradient standard
            deviation.
        real_seconds: What the athlete took over each bucket.
        mean_speed: The activity's mean speed, which relative speeds are
            measured against.
        steep: Per bucket, past the steepness cut.
        dawdle: Per bucket, caught by the dawdle gate.
        mixed: Per bucket, spanning mixed terrain (see MAX_BAND_GRADIENT_SPREAD).
        scored: Per bucket, whether it counts toward the objective: neither
            steep nor dawdle.
        huber_delta: Where the loss turns from squared to linear. None means
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
    gradient_spread: np.ndarray
    real_seconds: np.ndarray
    mean_speed: float
    steep: np.ndarray
    dawdle: np.ndarray
    mixed: np.ndarray
    scored: np.ndarray
    huber_delta: float | None

    @classmethod
    def build(
        cls,
        prepared: PreparedActivity,
        params: PacingParams | None = None,
        bucket_m: float = DEFAULT_BUCKET_M,
        multipliers: list[float] | None = None,
        max_gradient: float | None = MAX_SCORED_GRADIENT,
        dawdle_share: float | None = DAWDLE_SPEED_SHARE,
        huber_delta: float | None = HUBER_DELTA,
    ) -> "ActivityContext":
        """Lay an activity out for scoring.

        Args:
            prepared: The activity, from `prepare.prepare_reference`.
            params: Only `gradient_window_m` is read. Every later evaluation
                must use the same value.
            bucket_m: Residual bucket length, raised to the gradient window if
                it is shorter.
            multipliers: Optional per-leg surface multipliers.
            max_gradient: Buckets steeper than this in either direction are
                not scored. None disables the cut.
            dawdle_share: See DAWDLE_SPEED_SHARE. None disables the gate.
            huber_delta: See HUBER_DELTA. None scores with plain RMS.

        Raises:
            ValueError: If the activity has no distance, no moving time, or no
                bucket that can be scored.
        """
        params = params or PacingParams()
        track = prepared.track
        if track.total_distance <= 0 or prepared.moving_seconds <= 0:
            raise ValueError(f"{prepared.name}: needs positive distance and moving time to compare.")

        model = TrackModel.build(track, params.gradient_window_m)
        bucket_m = max(bucket_m, params.gradient_window_m)

        boundaries = np.asarray(_bucket_boundaries(model.leg_distances, bucket_m))
        lo, hi = boundaries[:-1], boundaries[1:]

        point_distance = np.asarray([point.distance_from_start for point in track.points], dtype=float)
        reference = np.asarray(prepared.reference_elapsed, dtype=float)
        bucket_distance = point_distance[hi] - point_distance[lo]
        real_seconds = reference[hi] - reference[lo]

        # A bucket with no distance or no time says nothing about pace.
        # prepare() already removed stops, so this is rare.
        keep = (bucket_distance > 0) & (real_seconds > 0)
        lo, hi = lo[keep], hi[keep]
        bucket_distance, real_seconds = bucket_distance[keep], real_seconds[keep]
        if len(lo) == 0:
            raise ValueError(f"{prepared.name}: no usable residual buckets.")

        # Distance-weighted mean and spread of the gradient per bucket, from
        # running totals. The legs are consecutive differences of
        # distance_from_start, so the weights over a bucket add up to
        # bucket_distance, which is positive.
        def bucket_mean(per_leg: np.ndarray) -> np.ndarray:
            running = np.concatenate(([0.0], np.cumsum(per_leg * model.leg_distances)))
            return (running[hi] - running[lo]) / bucket_distance

        bucket_gradient = bucket_mean(model.gradients)
        gradient_spread = np.sqrt(np.maximum(bucket_mean(model.gradients ** 2) - bucket_gradient ** 2, 0.0))
        mixed = (gradient_spread > MAX_BAND_GRADIENT_SPREAD) & (gradient_spread > np.abs(bucket_gradient))

        steep = (
            np.zeros(len(lo), dtype=bool) if max_gradient is None
            else np.abs(bucket_gradient) > max_gradient
        )

        dawdle = np.zeros(len(lo), dtype=bool)
        flat = (np.abs(bucket_gradient) <= FLAT_GRADIENT) & ~mixed
        if dawdle_share is not None and flat.sum() >= _MIN_FLAT_BUCKETS:
            real_speed = bucket_distance / real_seconds
            dawdle = flat & (real_speed < dawdle_share * np.median(real_speed[flat]))

        scored = ~(steep | dawdle)
        if not scored.any():
            raise ValueError(f"{prepared.name}: every bucket is too steep or dawdling; nothing to score.")

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
            gradient_spread=gradient_spread,
            real_seconds=real_seconds,
            mean_speed=track.total_distance / prepared.moving_seconds,
            steep=steep,
            dawdle=dawdle,
            mixed=mixed,
            scored=scored,
            huber_delta=huber_delta,
        )


@dataclass(frozen=True)
class _Evaluation:
    """One activity scored at one set of parameters, per bucket of the context."""
    settings: ResolvedSettings
    predicted: np.ndarray
    model_seconds: np.ndarray
    log_residual: np.ndarray
    mean_log_residual: float
    objective: float


def _root_mean_loss(residual: np.ndarray, weight: np.ndarray, huber_delta: float | None) -> float:
    """Weighted root-mean Huber loss, scaled so it reads as an RMS.

    The linear part, 2·delta·|r| - delta², meets the squared part smoothly at
    the threshold. With no residual past it, the result is exactly the
    weighted RMS.
    """
    magnitude = np.abs(residual)
    if huber_delta is None:
        loss = magnitude ** 2
    else:
        loss = np.where(magnitude <= huber_delta, magnitude ** 2, 2 * huber_delta * magnitude - huber_delta ** 2)
    return math.sqrt((loss * weight).sum() / weight.sum())


def _evaluate(context: ActivityContext, params: PacingParams) -> _Evaluation:
    """Pace the activity at these parameters and reduce it to bucket residuals.

    Unscored buckets are still paced, as the app would pace them. The
    objective scores each residual minus the mean, so a uniform shift can't
    change it. That covers both the Jensen offset from matching total time
    and the time the model gives unscored buckets, which would otherwise move
    every scored residual by an amount that depends on the parameters. The
    model's time is still re-matched over the scored buckets, so that the
    reported residuals sit near zero on the same basis.

    Every bucket has positive distance and every modeled leg speed is
    positive, so every bucket gets a positive model time.

    Raises:
        ValueError: If `params` uses a different gradient window than the
            context was built for.
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

    scored = context.scored
    model_seconds = predicted[context.hi] - predicted[context.lo]
    model_seconds *= context.real_seconds[scored].sum() / model_seconds[scored].sum()
    # log(model_speed / real_speed) over the same distance.
    log_residual = np.log(context.real_seconds / model_seconds)

    scored_residual = log_residual[scored]
    scored_distance = context.bucket_distance[scored]
    mean_log_residual = float((scored_residual * scored_distance).sum() / scored_distance.sum())

    return _Evaluation(
        settings=settings,
        predicted=predicted,
        model_seconds=model_seconds,
        log_residual=log_residual,
        mean_log_residual=mean_log_residual,
        objective=_root_mean_loss(scored_residual - mean_log_residual, scored_distance, context.huber_delta),
    )


def objective_of(context: ActivityContext, params: PacingParams) -> float:
    """The activity's objective alone, without building the report.

    Identical to `compare(...).objective`. See `_evaluate` for what is raised.
    """
    return _evaluate(context, params).objective


def _band_edges(index: int) -> tuple[float, float]:
    """The (low, high) gradient edges of a band, unbounded at the extremes."""
    low = -math.inf if index == 0 else GRADIENT_BAND_EDGES[index - 1]
    high = math.inf if index == len(GRADIENT_BAND_EDGES) else GRADIENT_BAND_EDGES[index]
    return low, high


def _in_band_table(bucket: Bucket) -> bool:
    """Whether a bucket is pooled into the band table: not dawdling or mixed terrain."""
    return bucket.excluded != "dawdle" and not bucket.mixed


def _pool_into_bands(buckets: list[Bucket], mean_speed: float, mean_log_residual: float) -> list[GradientBand]:
    """Group buckets by gradient band.

    A band's speed is its distance over its time. A distance-weighted mean of
    the bucket speeds would be biased toward the faster buckets.
    """
    grouped: dict[int, list[Bucket]] = {}
    for bucket in filter(_in_band_table, buckets):
        grouped.setdefault(bisect.bisect_right(GRADIENT_BAND_EDGES, bucket.gradient), []).append(bucket)

    bands = []
    for index in sorted(grouped):
        members = grouped[index]
        distance = sum(bucket.distance_m for bucket in members)
        model_seconds = sum(bucket.model_seconds for bucket in members)
        real_seconds = sum(bucket.real_seconds for bucket in members)
        residual = sum(bucket.log_residual * bucket.distance_m for bucket in members) / distance
        low, high = _band_edges(index)
        bands.append(GradientBand(
            low=low,
            high=high,
            bucket_count=len(members),
            distance_m=distance,
            model_relative_speed=distance / model_seconds / mean_speed,
            real_relative_speed=distance / real_seconds / mean_speed,
            log_residual=residual,
            shape_residual=residual - mean_log_residual,
        ))
    return bands


def report_of(context: ActivityContext, params: PacingParams | None = None) -> ActivityReport:
    """The full report for an already-built context.

    Call this directly when the same context is also used for repeated scoring.
    """
    params = params or PacingParams()
    prepared = context.prepared
    evaluation = _evaluate(context, params)

    model_speed = context.bucket_distance / evaluation.model_seconds
    real_speed = context.bucket_distance / context.real_seconds
    excluded = np.where(context.steep, "steep", np.where(context.dawdle, "dawdle", ""))
    buckets = [
        Bucket(
            start_m=start,
            distance_m=distance,
            gradient=gradient,
            gradient_spread=spread,
            mixed=mixed,
            model_seconds=model_seconds,
            real_seconds=real_seconds,
            model_relative_speed=model / context.mean_speed,
            real_relative_speed=real / context.mean_speed,
            log_residual=residual,
            excluded=reason or None,
        )
        for (start, distance, gradient, spread, mixed, model_seconds, real_seconds, model, real, residual,
             reason) in zip(
            context.start_m.tolist(),
            context.bucket_distance.tolist(),
            context.bucket_gradient.tolist(),
            context.gradient_spread.tolist(),
            context.mixed.tolist(),
            evaluation.model_seconds.tolist(),
            context.real_seconds.tolist(),
            model_speed.tolist(),
            real_speed.tolist(),
            evaluation.log_residual.tolist(),
            excluded.tolist(),
        )
    ]

    def distance_where(mask: np.ndarray) -> float:
        return float(context.bucket_distance[mask].sum())

    errors = evaluation.predicted - np.asarray(prepared.reference_elapsed, dtype=float)
    worst = int(np.argmax(np.abs(errors)))

    return ActivityReport(
        name=prepared.name,
        sport=prepared.sport,
        distance_m=prepared.track.total_distance,
        moving_seconds=prepared.moving_seconds,
        settings=evaluation.settings,
        objective=evaluation.objective,
        steep_distance_m=distance_where(context.steep),
        dawdle_distance_m=distance_where(context.dawdle),
        mixed_distance_m=distance_where(context.mixed),
        mean_log_residual=evaluation.mean_log_residual,
        max_time_error_seconds=float(errors[worst]),
        max_time_error_at_m=prepared.track.points[worst].distance_from_start,
        rms_time_error_seconds=math.sqrt((errors ** 2).mean()),
        buckets=buckets,
        bands=_pool_into_bands(buckets, context.mean_speed, evaluation.mean_log_residual),
        notes=list(prepared.notes),
    )


def compare(
    prepared: PreparedActivity,
    params: PacingParams | None = None,
    bucket_m: float = DEFAULT_BUCKET_M,
    multipliers: list[float] | None = None,
) -> ActivityReport:
    """Run the model over a prepared activity and measure it against the recording.

    Args:
        prepared: The activity, from `prepare.prepare_reference`.
        params: The constants to run with. Defaults to the shipped values.
        bucket_m: Residual bucket length, raised to the gradient window if
            it is shorter.
        multipliers: Optional per-leg surface multipliers.

    Raises:
        ValueError: If the activity has no distance, no moving time, or no
            bucket that can be scored.
    """
    params = params or PacingParams()
    return report_of(ActivityContext.build(prepared, params, bucket_m, multipliers), params)
