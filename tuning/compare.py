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
"""

import math
from dataclasses import dataclass

from gpx2fit.core.models import SportType
from gpx2fit.core.pacing.gradient import GRADIENT_WINDOW_M, calculate_gradient
from tuning.model import PacingParams, ResolvedSettings, leg_distances_of, predict_elapsed
from tuning.prepare import PreparedActivity

# Residual buckets are at least this long. Anything shorter is inside the
# gradient smoothing window, where the model has deliberately stopped
# resolving detail and the GPS never did.
DEFAULT_BUCKET_M = 100.0

# Upper edges of the gradient bands the residuals are grouped into. Narrow
# around flat (where most distance falls and small errors matter) and wide at
# the extremes (where there is rarely enough distance to say much).
GRADIENT_BAND_EDGES = (-0.25, -0.18, -0.12, -0.08, -0.04, -0.015, 0.015, 0.04, 0.08, 0.12, 0.18, 0.25)


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
    """
    start_m: float
    distance_m: float
    gradient: float
    model_seconds: float
    real_seconds: float
    model_relative_speed: float
    real_relative_speed: float
    log_residual: float


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
        objective: Distance-weighted RMS of the bucketed log residual. The
            single number a sweep minimizes.
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


def _bucket_boundaries(distances: list[float], bucket_m: float) -> list[int]:
    """Point indexes splitting the track into buckets of at least `bucket_m`.

    Always starts at 0 and ends at the last point. A short final bucket is
    merged backwards rather than left to stand on its own, since a 12 m tail
    would carry a meaningless speed.
    """
    boundaries = [0]
    accumulated = 0.0
    for index, leg in enumerate(distances, start=1):
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
        bucket_m: Residual bucket length. Values below `GRADIENT_WINDOW_M` are
            raised to it — finer than the gradient smoothing there is nothing
            for the model to be right or wrong about.
        multipliers: Optional per-leg surface multipliers, for the surface phase.
        gradients: Already-computed gradients for this track and
            `params.gradient_window_m`. A sweep holds the window fixed and
            varies everything else, so recomputing them per evaluation is pure
            waste — see `model.resolve`.

    Returns:
        The report.
    Raises:
        ValueError: If the prepared activity has no distance or no moving time.
    """
    params = params or PacingParams()
    bucket_m = max(bucket_m, GRADIENT_WINDOW_M)

    track = prepared.track
    reference = prepared.reference_elapsed
    moving_seconds = prepared.moving_seconds
    total_distance = track.total_distance
    if total_distance <= 0 or moving_seconds <= 0:
        raise ValueError(f"{prepared.name}: needs positive distance and moving time to compare.")

    if gradients is None:
        gradients = calculate_gradient(track, params.gradient_window_m)
    predicted, settings = predict_elapsed(
        track, prepared.sport, moving_seconds, params, multipliers, gradients
    )
    distances = leg_distances_of(track)
    mean_speed = total_distance / moving_seconds

    boundaries = _bucket_boundaries(distances, bucket_m)
    buckets: list[Bucket] = []
    for lo, hi in zip(boundaries, boundaries[1:]):
        bucket_distance = track.points[hi].distance_from_start - track.points[lo].distance_from_start
        model_seconds = predicted[hi] - predicted[lo]
        real_seconds = reference[hi] - reference[lo]
        if bucket_distance <= 0 or model_seconds <= 0 or real_seconds <= 0:
            # A zero-distance or zero-time bucket carries no pace information.
            # prepare() already removed stops, so this is rare and not an error.
            continue

        model_speed = bucket_distance / model_seconds
        real_speed = bucket_distance / real_seconds
        legs = list(zip(gradients[lo:hi], distances[lo:hi]))
        span = sum(leg for _, leg in legs)
        gradient = sum(g * leg for g, leg in legs) / span if span > 0 else 0.0

        buckets.append(Bucket(
            start_m=track.points[lo].distance_from_start,
            distance_m=bucket_distance,
            gradient=gradient,
            model_seconds=model_seconds,
            real_seconds=real_seconds,
            model_relative_speed=model_speed / mean_speed,
            real_relative_speed=real_speed / mean_speed,
            log_residual=math.log(model_speed / real_speed),
        ))

    if not buckets:
        raise ValueError(f"{prepared.name}: no usable residual buckets.")

    bucket_distance_total = sum(bucket.distance_m for bucket in buckets)
    objective = math.sqrt(
        sum(bucket.log_residual ** 2 * bucket.distance_m for bucket in buckets) / bucket_distance_total
    )
    mean_log_residual = (
        sum(bucket.log_residual * bucket.distance_m for bucket in buckets) / bucket_distance_total
    )

    errors = [model - real for model, real in zip(predicted, reference)]
    worst = max(range(len(errors)), key=lambda index: abs(errors[index]))
    rms_error = math.sqrt(sum(error ** 2 for error in errors) / len(errors))

    return ActivityReport(
        name=prepared.name,
        sport=prepared.sport,
        distance_m=total_distance,
        moving_seconds=moving_seconds,
        settings=settings,
        objective=objective,
        mean_log_residual=mean_log_residual,
        max_time_error_seconds=errors[worst],
        max_time_error_at_m=track.points[worst].distance_from_start,
        rms_time_error_seconds=rms_error,
        buckets=buckets,
        bands=_pool_into_bands(buckets, mean_log_residual),
        notes=list(prepared.notes),
    )
