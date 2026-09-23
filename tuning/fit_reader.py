"""Decode a real FIT activity into the project's own Track model.

The inverse of core/fit_writer.py, and deliberately written against the same
conventions: timestamps are Unix epoch milliseconds, positions are already in
degrees by the time fit-tool hands them over, and a timer STOP/START pair marks
a real pause. Decoding a FIT file re-emits enum fields as plain ints, so every
comparison here is against `SomeEnum.MEMBER.value` rather than the member.

Reading is deliberately split in two:

- `decode_fit_bytes` is the `fit_tool` walk, and it is by far the most
  expensive thing the harness does — fit-tool builds a field object per field
  per record, which is tens of millions of objects for a corpus. It extracts
  raw numbers and nothing else, so `tuning/cache.py` can keep its result on
  disk and never pay for the same file twice.
- `read_decoded_activity` is every judgment call made about those numbers —
  which points to drop, which sport to pace as, what to report as suspect.
  None of it is cached, so changing a rule takes effect on the next run
  without anyone having to remember to clear a cache.

`read_reference_activity` is the two of them together, which is what a caller
with no cache (and every test) wants.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

import numpy as np
from fit_tool.fit_file import FitFile
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.messages.sport_message import SportMessage
from fit_tool.profile.profile_type import Event, EventType, Sport

from gpx2fit.core.models import SportType, Track, TrackPoint
from gpx2fit.core.pacing.anchors import distance_meters


class UnreadableActivity(Exception):
    """Raised when a FIT file can't be used as a pacing reference.
     - The message always says which file and why.
    """


# FIT records a walk and a hike as different sports, but the pacing model has
# one gait for both. Everything absent from this map is rejected rather than
# guessed — a ride or a swim would pace as nonsense, and silently mislabeling
# one would quietly poison the corpus it was tuned on.
_FIT_SPORT_TO_SPORT_TYPE = {
    Sport.RUNNING.value: SportType.RUNNING,
    Sport.WALKING.value: SportType.HIKING,
    Sport.HIKING.value: SportType.HIKING,
}

# A recorded track can carry the odd backward timestamp — a GPS second
# arriving late, a device clock nudged mid-activity. Those are isolated bad
# points, not a broken file, so they are dropped. Files with a ratio of
# these bad points above this are rejected as untrustworthy.
_MAX_NONMONOTONIC_SHARE = 0.01

# A single record at null island, or with a flipped coordinate sign, puts a
# leg of several thousand kilometres in the middle of an 8 km hike and inflates
# the whole activity's distance by a factor of 500. prepare.py's spike gate
# counts legs above 10 m/s but tolerates a small share of them, so a couple of
# teleports pass it as a note; the magnitude is what gives them away. Anything
# implying more than this is not a gait, so the point is dropped. Deliberately
# far above the spike threshold: a genuinely fast leg (a car segment in a
# mislabeled recording) must still reach the spike gate and be rejected there
# as a whole activity, not quietly patched out here one point at a time.
_TELEPORT_MPS = 50.0
_MAX_TELEPORT_SHARE = 0.01

# Some writers (seen from phone apps) stamp a whole batch of records with one
# second — up to a dozen records and a few hundred metres sharing a timestamp,
# then time catches up. The positions are real but the timing between them is
# gone, so speed over that ground is unmeasurable. Clean recordings put under
# 2.5% of their distance into zero-time legs; the batched ones put 66-88%.
_MAX_UNTIMED_DISTANCE_SHARE = 0.05

# Any of these ends a timer run; devices differ in which they emit.
_TIMER_STOP_EVENT_TYPES = frozenset({
    EventType.STOP.value,
    EventType.STOP_ALL.value,
    EventType.STOP_DISABLE.value,
    EventType.STOP_DISABLE_ALL.value,
})

# Columns of DecodedFit.records and DecodedFit.events, named so the arrays can
# be read without counting.
_TIMESTAMP, _LAT, _LON, _ELEVATION, _DISTANCE = range(5)
# The same, for modules that patch the records before they're read (elevation.py).
RECORD_COLUMNS = {"timestamp": _TIMESTAMP, "lat": _LAT, "lon": _LON, "elevation": _ELEVATION, "distance": _DISTANCE}
_EVENT_TIMESTAMP, _EVENT, _EVENT_TYPE = range(3)


@dataclass(frozen=True)
class DecodedFit:
    """The raw numbers one FIT file carried, with no interpretation applied.

    Deliberately plain arrays rather than objects: this is what gets cached, so
    it has to survive a round trip through a file and stay meaningful if the
    rules elsewhere in this module change. A missing field is NaN — the "no
    elevation" sentinel and the rest of the reading rules live in
    `read_decoded_activity`, on the uncached side.

    Attributes:
        records: One row per positioned record, in file order, with columns
            (timestamp_ms, lat, lon, elevation, distance).
        events: One row per timestamped event, in file order, with columns
            (timestamp_ms, event, event_type).
        sport_value: The raw FIT sport, from the SportMessage where there is
            one and the SessionMessage otherwise.
        elapsed_seconds: SessionMessage.total_elapsed_time, if present.
        timer_seconds: SessionMessage.total_timer_time, if present.
        distance_m: SessionMessage.total_distance, if present.
        ascent_m: SessionMessage.total_ascent, if present.
    """
    records: np.ndarray
    events: np.ndarray
    sport_value: int | None
    elapsed_seconds: float | None
    timer_seconds: float | None
    distance_m: float | None
    ascent_m: float | None

    @property
    def totals(self) -> "ActivityTotals":
        """The device's own summary, as the rest of the harness reads it."""
        return ActivityTotals(
            elapsed_seconds=self.elapsed_seconds,
            timer_seconds=self.timer_seconds,
            distance_m=self.distance_m,
            ascent_m=self.ascent_m,
        )


@dataclass(frozen=True)
class ActivityTotals:
    """The device's own summary of the activity, from its SessionMessage.

    Used only to cross-check what this module extracted: if the summed record
    distances disagree with the device's own total, the extraction is wrong and
    nothing computed from it can be trusted. Every field is optional because
    not every writer sets every one.

    Attributes:
        elapsed_seconds: Wall-clock duration including pauses.
        timer_seconds: Moving time, i.e. elapsed minus paused.
        distance_m: Total distance the device recorded.
        ascent_m: Total ascent the device recorded.
    """
    elapsed_seconds: float | None
    timer_seconds: float | None
    distance_m: float | None
    ascent_m: float | None


@dataclass
class ReferenceActivity:
    """One real activity, ready to be reshaped into a pacing-model input.

    Attributes:
        name: Identifier for reports, normally the source file's stem.
        sport: Mapped SportType, which picks the model's per-sport constants.
        track: The recorded points, in file order, each carrying its own real
            timestamp. Unlike everywhere else in this project, these timestamps
            are an input rather than something pacing produces.
        pauses: Timer pause intervals (stop, resume), in file order — the
            athlete's actual auto-pauses, as the device recorded them.
        totals: The device's own summary, for cross-checking.
        fit_sport: The raw FIT sport value, kept for diagnostics.
        nonmonotonic_dropped: How many points were discarded for carrying a
            timestamp earlier than one already kept. Reported as a note rather
            than hidden — a handful is normal GPS noise, a lot is a warning.
        teleports_dropped: How many points were discarded for sitting an
            impossible distance from the rest of the route.
        sport_overridden: True when the caller supplied the sport instead of
            the file declaring a usable one.
    """
    name: str
    sport: SportType
    track: Track
    pauses: list[tuple[datetime, datetime]]
    totals: ActivityTotals
    fit_sport: int
    nonmonotonic_dropped: int = 0
    teleports_dropped: int = 0
    sport_overridden: bool = False

    @property
    def paused_seconds(self) -> float:
        """Total time inside a timer pause."""
        return sum((resume - stop).total_seconds() for stop, resume in self.pauses)


def _to_datetime(fit_timestamp: int | float) -> datetime:
    """Convert a FIT timestamp (Unix epoch milliseconds) to an aware UTC datetime.

    The inverse of fit_writer._fit_timestamp, which is what produced the
    timestamps in any file this project wrote.
    """
    return datetime.fromtimestamp(fit_timestamp / 1000, tz=timezone.utc)


def _enum_value(field: object) -> int:
    """Normalize a message field to a plain int.

    Decoding re-emits enum fields as ints, but fit-tool's setters accept and
    store the enum member, so a message can hold either depending on where it
    came from.
    """
    return int(field.value) if isinstance(field, Enum) else int(field)  # pyrefly: ignore


def _sport_value(sport: Sport | int | None) -> int | None:
    """A message's sport field as a plain int — see `_enum_value`."""
    return None if sport is None else _enum_value(sport)


def _resolve_sport(name: str, sport_value: int | None) -> SportType:
    """Map a raw FIT sport value onto the model's SportType.

    Raises:
        UnreadableActivity: If the file declares no sport, or one the pacing
            model has no gait for (a ride, a swim, an indoor workout).
    """
    if sport_value is None:
        raise UnreadableActivity(f"{name}: the file declares no sport, so it can't be paced.")
    if sport_value not in _FIT_SPORT_TO_SPORT_TYPE:
        known = ", ".join(sorted(Sport(value).name.lower() for value in _FIT_SPORT_TO_SPORT_TYPE))
        raise UnreadableActivity(
            f"{name}: FIT sport {sport_value} ({Sport(sport_value).name.lower() if sport_value in Sport._value2member_map_ else 'unknown'}) "
            f"has no pacing model. Only {known} can be used as a reference."
        )
    return _FIT_SPORT_TO_SPORT_TYPE[sport_value]


def _extract_pauses(events: np.ndarray) -> list[tuple[datetime, datetime]]:
    """Pair up timer stop/start events into (stop, resume) pause intervals.

    Only Event.TIMER events are considered, in file order. A stop with no
    matching resume (the file's final STOP_ALL) closes the activity rather than
    opening a pause, so it's dropped. A résumé with no preceding stop — which
    the opening START always is — is ignored the same way.
    """
    pauses: list[tuple[datetime, datetime]] = []
    stopped_at: datetime | None = None

    for row in events.tolist():
        event, event_type = row[_EVENT], row[_EVENT_TYPE]
        if event != Event.TIMER.value:
            continue
        timestamp = _to_datetime(row[_EVENT_TIMESTAMP])
        if event_type in _TIMER_STOP_EVENT_TYPES:
            # A second stop without an intervening start keeps the earlier one:
            # the pause began at the first.
            if stopped_at is None:
                stopped_at = timestamp
        elif event_type == EventType.START.value:
            if stopped_at is not None and timestamp > stopped_at:
                pauses.append((stopped_at, timestamp))
            stopped_at = None

    return pauses


def _record_row(record: RecordMessage) -> list[float] | None:
    """One record's raw numbers, or None if it can't anchor a position in time.

    A record without a position or without a timestamp is not an error — a
    watch emits some before it has a GPS — and it can't be used by any
    downstream rule either. So it is dropped here rather than cached as a row
    of holes. Everything else that is absent stays absent, as NaN.
    """
    if record.timestamp is None or record.position_lat is None or record.position_long is None:
        return None

    elevation = record.enhanced_altitude
    if elevation is None:
        elevation = record.altitude

    return [
        float(record.timestamp),
        float(record.position_lat),
        float(record.position_long),
        math.nan if elevation is None else float(elevation),
        math.nan if record.distance is None else float(record.distance),
    ]


def _points_from_records(records: np.ndarray) -> list[TrackPoint]:
    """Build the TrackPoints for a decoded activity.

    Elevation falls back to 0.0, which is this project's sentinel for "no
    elevation data" (see gpx_reader.parse_gpx_bytes); prepare.py's quality
    gates reject a track that is mostly sentinel.
    """
    return [
        TrackPoint(
            lat=row[_LAT],
            lon=row[_LON],
            elevation=0.0 if math.isnan(row[_ELEVATION]) else row[_ELEVATION],
            # Provisional: the device's own cumulative distance where it has
            # one. prepare.py recomputes this with the project's own haversine
            # after resampling, so the model sees the same distance basis a
            # parsed GPX would give it. Kept here so `totals` can be
            # cross-checked as read.
            distance_from_start=0.0 if math.isnan(row[_DISTANCE]) else row[_DISTANCE],
            timestamp=_to_datetime(row[_TIMESTAMP]),
        )
        for row in records.tolist()
    ]


def _drop_nonmonotonic(points: list[TrackPoint]) -> tuple[list[TrackPoint], int]:
    """Keep only the points whose timestamps advance, and say how many went.

    A point timestamped before one already kept is discarded outright rather
    than reordered: its position is as suspect as its clock, and a recorded
    track has thousands of points to spare. Ties are kept — a device emitting
    two records for the same second is ordinary, and the elapsed-time maths
    downstream already tolerates a zero-length leg.
    """
    kept: list[TrackPoint] = []
    latest: datetime | None = None

    for point in points:
        if point.timestamp is None or (latest is not None and point.timestamp < latest):
            continue
        kept.append(point)
        latest = point.timestamp

    return kept, len(points) - len(kept)


def _drop_teleports(points: list[TrackPoint]) -> tuple[list[TrackPoint], int]:
    """Drop points no plausible gait could have reached, and say how many went.

    Measured against the last *kept* point, so a run of consecutive bad
    records is removed as a block rather than each one re-anchoring the check.
    A zero-second gap counts as one second: two records sharing a timestamp
    are ordinary, two records sharing a timestamp from opposite sides of the
    planet are not.

    Requires timestamps that already advance, so run it after
    `_drop_nonmonotonic`.
    """
    kept = points[:1] # first point is always kept (nothing to compare it to)
    for point in points[1:]:
        previous = kept[-1]
        if previous.timestamp is None or point.timestamp is None:
            continue
        seconds = max((point.timestamp - previous.timestamp).total_seconds(), 1.0)
        metres = distance_meters(previous.lat, previous.lon, point.lat, point.lon)
        if metres / seconds > _TELEPORT_MPS:
            continue
        kept.append(point)

    return kept, len(points) - len(kept)


def _untimed_distance(points: list[TrackPoint]) -> tuple[float, float]:
    """Metres covered between records sharing a timestamp, and metres in total.

    Haversine rather than the device's distance field, which the writers that
    batch timestamps tend to leave empty. Run it after `_drop_teleports`, so a
    null-island record can't inflate either figure.
    """
    untimed = total = 0.0
    for previous, point in zip(points, points[1:]):
        metres = distance_meters(previous.lat, previous.lon, point.lat, point.lon)
        total += metres
        if point.timestamp == previous.timestamp:
            untimed += metres
    return untimed, total


def _fill_missing_distances(points: list[TrackPoint]) -> None:
    """Replace a run of device distances with cumulative haversine, if the device gave none.

    Some writers omit RecordMessage.distance entirely, which would leave every
    point at 0.0. Detected by the last point still being at zero after a track
    that plainly moved.
    """
    if len(points) < 2 or points[-1].distance_from_start > 0:
        return

    cumulative = 0.0
    points[0].distance_from_start = 0.0
    for previous, current in zip(points, points[1:]):
        cumulative += distance_meters(previous.lat, previous.lon, current.lat, current.lon)
        current.distance_from_start = cumulative


def decode_fit_bytes(fit_bytes: bytes, name: str) -> DecodedFit:
    """Walk a FIT file once and pull out the raw numbers, with no interpretation.

    This is the expensive half of reading an activity — fit-tool rebuilds a
    field object per field per record — and the only half whose result depends
    on nothing but the file's own bytes. That is what makes it cacheable; see
    `tuning/cache.py`.

    Args:
        fit_bytes: The complete .fit file contents.
        name: Identifier used in the error message if it can't be decoded.

    Returns:
        The file's records, events, declared sport and summary totals.
    Raises:
        UnreadableActivity: If fit-tool can't decode the file at all.
    """
    try:
        decoded = FitFile.from_bytes(fit_bytes, allow_trailing_bytes=True)
    except Exception as error:  # fit-tool raises several unrelated types here.
        raise UnreadableActivity(f"{name}: could not decode the FIT file ({error}).") from error

    record_rows: list[list[float]] = []
    event_rows: list[list[float]] = []
    session: SessionMessage | None = None
    sport_value: int | None = None

    for record in decoded.records:
        message = record.message
        if isinstance(message, RecordMessage):
            row = _record_row(message)
            if row is not None:
                record_rows.append(row)
        elif isinstance(message, EventMessage):
            if message.timestamp is not None and message.event is not None and message.event_type is not None:
                event_rows.append([
                    float(message.timestamp),
                    float(_enum_value(message.event)),
                    float(_enum_value(message.event_type)),
                ])
        elif isinstance(message, SportMessage):
            if message.sport is not None:
                sport_value = _sport_value(message.sport)
        elif isinstance(message, SessionMessage):
            session = message
            # Only a fallback: a SportMessage, when present, is the file's
            # own declaration and wins.
            if sport_value is None and message.sport is not None:
                sport_value = _sport_value(message.sport)

    ascent = session.total_ascent if session is not None else None
    return DecodedFit(
        records=np.array(record_rows, dtype=float).reshape(len(record_rows), 5),
        events=np.array(event_rows, dtype=float).reshape(len(event_rows), 3),
        sport_value=sport_value,
        elapsed_seconds=session.total_elapsed_time if session is not None else None,
        timer_seconds=session.total_timer_time if session is not None else None,
        distance_m=session.total_distance if session is not None else None,
        ascent_m=float(ascent) if ascent is not None else None,
    )


def read_decoded_activity(
    decoded: DecodedFit, name: str, sport_override: SportType | None = None
) -> ReferenceActivity:
    """Turn one file's raw numbers into a ReferenceActivity, applying every reading rule.

    Split from `decode_fit_bytes` so that all of this — which points to drop,
    which sport to pace as, what counts as untrustworthy — runs on every read,
    including a cached one. A rule changed here takes effect immediately; no
    cache has to be cleared for it.

    Args:
        decoded: The file's raw numbers, from `decode_fit_bytes`.
        name: Identifier used in reports and error messages, normally the
            source file's stem.
        sport_override: Pace as this sport whatever the file declares. Exports
            routinely mislabel the sport — a run recorded on a bike computer
            comes through as cycling, and several platforms write a hike as
            "generic" — and the recording is perfectly good pacing evidence
            either way. The caller is asserting what the activity really was,
            so nothing is guessed here; without it the declared sport still
            has to map onto a gait the model knows.

    Returns:
        The activity, with its points carrying their real recorded timestamps.
    Raises:
        UnreadableActivity: If the file declares a sport the pacing model has
            no gait for (and none was supplied), carries fewer than two
            positioned records, has mostly backward timestamps, covers much of
            its distance between records sharing one timestamp, or is mostly
            positions no gait could have reached.
    """
    points = _points_from_records(decoded.records)
    sport_value = decoded.sport_value

    if len(points) < 2:
        raise UnreadableActivity(
            f"{name}: only {len(points)} record(s) carried both a position and a timestamp, "
            "so there's no route to pace."
        )

    points, dropped = _drop_nonmonotonic(points)
    if dropped > _MAX_NONMONOTONIC_SHARE * (len(points) + dropped):
        raise UnreadableActivity(
            f"{name}: {dropped} of {len(points) + dropped} record timestamps run backwards, "
            "so the recorded timing can't be trusted."
        )
    if len(points) < 2:
        raise UnreadableActivity(
            f"{name}: only {len(points)} record(s) left after dropping backward timestamps."
        )

    points, teleports = _drop_teleports(points)
    # Checked before the teleport share: with the one-second floor in
    # `_drop_teleports`, ordinary GPS scatter inside a batch of shared
    # timestamps reads as 50+ m/s, and would be reported as teleports.
    untimed, total = _untimed_distance(points)
    if untimed > _MAX_UNTIMED_DISTANCE_SHARE * total:
        raise UnreadableActivity(
            f"{name}: {untimed / total:.0%} of the distance ({untimed / 1000:.1f} of "
            f"{total / 1000:.1f} km) was recorded between records sharing a timestamp, "
            "so the recorded timing can't be trusted."
        )
    if teleports > _MAX_TELEPORT_SHARE * (len(points) + teleports):
        raise UnreadableActivity(
            f"{name}: {teleports} of {len(points) + teleports} records are nowhere near the "
            "rest of the route, so the GPS trace can't be trusted."
        )
    if len(points) < 2:
        raise UnreadableActivity(
            f"{name}: only {len(points)} record(s) left after dropping unreachable positions."
        )

    _fill_missing_distances(points)

    sport = sport_override if sport_override is not None else _resolve_sport(name, sport_value)

    return ReferenceActivity(
        name=name,
        sport=sport,
        track=Track(points=points, sport=sport, activity_name=name),
        pauses=_extract_pauses(decoded.events),
        totals=decoded.totals,
        fit_sport=sport_value if sport_value is not None else -1,
        nonmonotonic_dropped=dropped,
        teleports_dropped=teleports,
        sport_overridden=sport_override is not None,
    )


def read_reference_activity(
    fit_bytes: bytes, name: str, sport_override: SportType | None = None
) -> ReferenceActivity:
    """Decode a real FIT activity into a ReferenceActivity, in one step.

    `decode_fit_bytes` followed by `read_decoded_activity` — what a caller with
    no decode cache wants. See those two for the arguments and what is raised.
    """
    return read_decoded_activity(decode_fit_bytes(fit_bytes, name), name, sport_override)
