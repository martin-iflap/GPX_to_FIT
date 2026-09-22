"""Render an ActivityReport as a compact text block and as JSON.

The text form is the review artifact: it has to be readable in a terminal and
dense enough that the whole corpus can be gone through activity by activity
without scrolling past anything that matters. The gradient-band table is the
point of it — `real` is the athlete's own measured gradient-to-speed curve, so
setting it beside `model` says directly where the curve is wrong and in which
direction.

The JSON form carries everything, including per-bucket detail, for the corpus
stage to pool.
"""

import json
import math
from dataclasses import asdict

from tuning.compare import ActivityReport

# How many of the worst-fitting buckets to list. A handful is enough to spot a
# stretch of bad data; more just crowds out the band table.
_WORST_BUCKETS = 4


def format_duration(seconds: float) -> str:
    """Seconds as H:MM:SS, or M:SS under an hour. Sign is kept."""
    sign = "-" if seconds < 0 else ""
    total = round(abs(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{sign}{hours}:{minutes:02d}:{secs:02d}"
    return f"{sign}{minutes}:{secs:02d}"


def _format_band_label(low: float, high: float) -> str:
    """A gradient band as a readable percentage range."""
    if low == -math.inf:
        return f"  <= {high * 100:+.0f}%"
    if high == math.inf:
        return f"  >= {low * 100:+.0f}%"
    return f"{low * 100:+.1f}% .. {high * 100:+.1f}%"


def format_report(report: ActivityReport, show_worst: int = _WORST_BUCKETS) -> str:
    """Render one activity's comparison as a text block."""
    settings = report.settings
    lines = [
        f"{report.name}  |  {report.sport.value}  |  {report.distance_m / 1000:.2f} km"
        f"  |  moving {format_duration(report.moving_seconds)}",
        f"  terrain     verticality {settings.verticality:.4f}"
        f"   flat-equivalent {settings.flat_equivalent_mps:.2f} m/s"
        f"   mean {report.distance_m / report.moving_seconds:.2f} m/s",
        f"  resolved    tobler_weight {settings.tobler_weight:.3f}"
        f"   max_speed_ratio {settings.max_speed_ratio:.3f}"
        f"   minetti exp {settings.curve_shape.minetti.uphill:.3f}/{settings.curve_shape.minetti.downhill:.3f}",
    ]
    for note in report.notes:
        lines.append(f"  note        {note}")

    lines += [
        "",
        f"  objective   {report.objective:.4f}   (distance-weighted RMS log residual; 0 is perfect)",
        f"  clock drift max {format_duration(report.max_time_error_seconds)}"
        f" ({report.time_error_share * 100:.1f}% of moving time) at {report.max_time_error_at_m / 1000:.2f} km"
        f"   |   rms {format_duration(report.rms_time_error_seconds)}",
        "",
        "  gradient band      buckets     dist    model     real    resid    shape",
        "  " + "-" * 69,
    ]

    for band in report.bands:
        lines.append(
            f"  {_format_band_label(band.low, band.high):<17}"
            f"{band.bucket_count:>8}"
            f"{band.distance_m / 1000:>8.2f} km"
            f"{band.model_relative_speed:>8.2f}"
            f"{band.real_relative_speed:>9.2f}"
            f"{band.log_residual:>+9.3f}"
            f"{band.shape_residual:>+9.3f}"
        )

    lines += [
        "  " + "-" * 69,
        "  model/real are speeds relative to this activity's mean.",
        "  resid = log(model/real): positive means the model ran it faster than the athlete did.",
        f"  shape = resid minus this activity's mean resid ({report.mean_log_residual:+.3f}).",
        "  Read shape, not resid: the model matched total time exactly, so being wrong in one",
        "  band forces an offsetting error across every other. Only the spread is a finding.",
    ]

    if show_worst:
        worst = sorted(report.buckets, key=lambda b: -abs(b.log_residual))[:show_worst]
        lines += ["", f"  worst {len(worst)} buckets (check these for bad data before believing them):"]
        for bucket in worst:
            lines.append(
                f"    {bucket.start_m / 1000:>6.2f} km  {bucket.distance_m:>5.0f} m"
                f"  grade {bucket.gradient * 100:>+6.1f}%"
                f"  model {format_duration(bucket.model_seconds):>7}"
                f"  real {format_duration(bucket.real_seconds):>7}"
                f"  resid {bucket.log_residual:>+7.3f}"
            )

    return "\n".join(lines)


def report_to_dict(report: ActivityReport, include_buckets: bool = True) -> dict:
    """An ActivityReport as plain JSON-serializable data."""
    settings = report.settings
    data = {
        "name": report.name,
        "sport": report.sport.value,
        "distance_m": report.distance_m,
        "moving_seconds": report.moving_seconds,
        "mean_speed_mps": report.distance_m / report.moving_seconds,
        "objective": report.objective,
        "mean_log_residual": report.mean_log_residual,
        "max_time_error_seconds": report.max_time_error_seconds,
        "max_time_error_at_m": report.max_time_error_at_m,
        "rms_time_error_seconds": report.rms_time_error_seconds,
        "time_error_share": report.time_error_share,
        "settings": {
            "tobler_weight": settings.tobler_weight,
            "max_speed_ratio": settings.max_speed_ratio,
            "verticality": settings.verticality,
            "flat_equivalent_mps": settings.flat_equivalent_mps,
            "curve_shape": {
                "minetti": asdict(settings.curve_shape.minetti),
                "tobler": asdict(settings.curve_shape.tobler),
            },
        },
        "bands": [
            {
                # inf doesn't survive a JSON round trip through other readers,
                # so the open-ended bands are written as nulls.
                "low": None if band.low == -math.inf else band.low,
                "high": None if band.high == math.inf else band.high,
                "bucket_count": band.bucket_count,
                "distance_m": band.distance_m,
                "model_relative_speed": band.model_relative_speed,
                "real_relative_speed": band.real_relative_speed,
                "log_residual": band.log_residual,
                "shape_residual": band.shape_residual,
            }
            for band in report.bands
        ],
        "notes": report.notes,
    }
    if include_buckets:
        data["buckets"] = [asdict(bucket) for bucket in report.buckets]
    return data


def write_json(path, payload: dict) -> None:
    """Write `payload` as indented JSON, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
