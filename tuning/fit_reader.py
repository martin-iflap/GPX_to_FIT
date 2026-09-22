"""Decode a real FIT activity into the project's own Track model.

The inverse of core/fit_writer.py, and deliberately written against the same
conventions: timestamps are Unix epoch milliseconds, positions are already in
degrees by the time fit-tool hands them over, and a timer STOP/START pair marks
a real pause. Decoding a FIT file re-emits enum fields as plain ints, so every
comparison here is against `SomeEnum.MEMBER.value` rather than the member.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

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

# Any of these ends a timer run; devices differ in which they emit.
_TIMER_STOP_EVENT_TYPES = frozenset({
    EventType.STOP.value,
    EventType.STOP_ALL.value,
    EventType.STOP_DISABLE.value,
    EventType.STOP_DISABLE_ALL.value,
})


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


def _sport_value(sport: Sport | int | None) -> int | None:
    """Normalize a message's sport field to a plain int.

    Decoding re-emits enum fields as ints, but fit-tool's setters accept and
    store the enum member, so a message can hold either depending on where it
    came from.
    """
    if sport is None:
        return None
    return sport.value if isinstance(sport, Sport) else sport


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


def _extract_pauses(events: list[EventMessage]) -> list[tuple[datetime, datetime]]:
    """Pair up timer stop/start events into (stop, resume) pause intervals.

    Only Event.TIMER events are considered, in file order. A stop with no
    matching resume (the file's final STOP_ALL) closes the activity rather than
    opening a pause, so it's dropped. A résumé with no preceding stop — which
    the opening START always is — is ignored the same way.
    """
    pauses: list[tuple[datetime, datetime]] = []
    stopped_at: datetime | None = None

    for event in events:
        if event.event != Event.TIMER.value or event.timestamp is None:
            continue
        timestamp = _to_datetime(event.timestamp)
        if event.event_type in _TIMER_STOP_EVENT_TYPES:
            # A second stop without an intervening start keeps the earlier one:
            # the pause began at the first.
            if stopped_at is None:
                stopped_at = timestamp
        elif event.event_type == EventType.START.value:
            if stopped_at is not None and timestamp > stopped_at:
                pauses.append((stopped_at, timestamp))
            stopped_at = None

    return pauses


def _point_from_record(record: RecordMessage) -> TrackPoint | None:
    """Build a TrackPoint from one RecordMessage, or None if it can't anchor a position in time.

    A record without a position or without a timestamp is not an error — a
    watch emits some before it has a GPS fix — so those are skipped rather
    than raised on. Elevation falls back to 0.0, which is this project's
    sentinel for "no elevation data" (see gpx_reader.parse_gpx_bytes);
    prepare.py's quality gates reject a track that is mostly sentinel.
    """
    if record.timestamp is None or record.position_lat is None or record.position_long is None:
        return None

    elevation = record.enhanced_altitude
    if elevation is None:
        elevation = record.altitude
    if elevation is None:
        elevation = 0.0

    return TrackPoint(
        lat=record.position_lat,
        lon=record.position_long,
        elevation=elevation,
        # Provisional: the device's own cumulative distance where it has one.
        # prepare.py recomputes this with the project's own haversine after
        # resampling, so the model sees the same distance basis a parsed GPX
        # would give it. Kept here so `totals` can be cross-checked as read.
        distance_from_start=record.distance if record.distance is not None else 0.0,
        timestamp=_to_datetime(record.timestamp),
    )


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


def read_reference_activity(
    fit_bytes: bytes, name: str, sport_override: SportType | None = None
) -> ReferenceActivity:
    """Decode a real FIT activity into a ReferenceActivity.

    Walks the decoded records once, collecting position/elevation/time from
    every RecordMessage, timer pauses from the EventMessages, and the sport and
    summary totals from the SportMessage/SessionMessage.

    Args:
        fit_bytes: The complete .fit file contents.
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
        UnreadableActivity: If the file can't be decoded, declares a sport the
            pacing model has no gait for (and none was supplied), carries fewer
            than two positioned records, has mostly backward timestamps, or
            is mostly positions no gait could have reached.
    """
    try:
        decoded = FitFile.from_bytes(fit_bytes, allow_trailing_bytes=True)
    except Exception as error:  # fit-tool raises several unrelated types here.
        raise UnreadableActivity(f"{name}: could not decode the FIT file ({error}).") from error

    points: list[TrackPoint] = []
    events: list[EventMessage] = []
    session: SessionMessage | None = None
    sport_value: int | None = None

    for record in decoded.records:
        message = record.message
        if isinstance(message, RecordMessage):
            point = _point_from_record(message)
            if point is not None:
                points.append(point)
        elif isinstance(message, EventMessage):
            events.append(message)
        elif isinstance(message, SportMessage):
            if message.sport is not None:
                sport_value = _sport_value(message.sport)
        elif isinstance(message, SessionMessage):
            session = message
            # Only a fallback: a SportMessage, when present, is the file's
            # own declaration and wins.
            if sport_value is None and message.sport is not None:
                sport_value = _sport_value(message.sport)

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
    totals = ActivityTotals(
        elapsed_seconds=session.total_elapsed_time if session is not None else None,
        timer_seconds=session.total_timer_time if session is not None else None,
        distance_m=session.total_distance if session is not None else None,
        ascent_m=float(session.total_ascent) if session is not None and session.total_ascent is not None else None,
    )

    return ReferenceActivity(
        name=name,
        sport=sport,
        track=Track(points=points, sport=sport, activity_name=name),
        pauses=_extract_pauses(events),
        totals=totals,
        fit_sport=sport_value if sport_value is not None else -1,
        nonmonotonic_dropped=dropped,
        teleports_dropped=teleports,
        sport_overridden=sport_override is not None,
    )
