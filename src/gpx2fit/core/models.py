"""Shared data model: the point/track/anchor types every core module reads and writes."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum


class InputError(ValueError):
    """Raised when a problem traces back to invalid user-supplied data (e.g.
    contradictory anchor/stop times, a corrupt GPX file) rather than a bug in
    this code.

    The GUI (`gui/js/pyodideBridge.js`'s `classifyPyError`) distinguishes
    this from every other exception by name, to show its message directly
    instead of a raw traceback. That means every `raise InputError(...)`
    message must be written for the end user, not a developer: plain
    language, no internal identifiers, and (where useful) a hint at how to
    fix it.
    """


class SportType(Enum):
    """Allowed sport types of the activity, used as a dispatch key for pacing and FIT file generation."""
    RUNNING = "running"
    HIKING = "hiking"
    # CYCLING = "cycling" will be added later


@dataclass
class TrackPoint:
    """A single point in a track.

    Attributes:
        lat: Latitude in degrees.
        lon: Longitude in degrees.
        elevation: Elevation in meters.
        distance_from_start: Cumulative distance in meters from the track's
            first point. Computed while parsing — not read from the GPX file.
        timestamp: The point's time, assigned by pacing. None until then.
    """
    lat: float
    lon: float
    elevation: float
    distance_from_start: float
    timestamp: datetime | None = None


@dataclass
class Track:
    """A sequence of TrackPoints forming a route or activity.

    Attributes:
        points: The track's points, in route order.
        sport: Sport type used for pacing and FIT generation. Optional for now.
        device: Device name to embed in the FIT file, if available. Written
            as `product_name`, which Strava ignores.
        device_manufacturer: FIT manufacturer ID (Garmin is 1). Together
            with `device_product` this is what Strava matches against its
            own device table to show a device name. None writes a neutral
            "development" ID that no platform labels as a real device.
        device_product: FIT product ID within that manufacturer. Only
            meaningful alongside `device_manufacturer`.
        device_serial: FIT serial number. Not shown by Strava; None omits it.
        activity_name: Cosmetic activity name, shown in Strava's feed.
    """
    points: list[TrackPoint]
    sport: SportType | None = None
    device: str | None = None
    device_manufacturer: int | None = None
    device_product: int | None = None
    device_serial: int | None = None
    activity_name: str | None = None

    @property
    def start_time(self) -> datetime | None:
        """Return the timestamp of the first point in the track, or None if no points exist or the first point has no timestamp."""
        if not self.points or self.points[0].timestamp is None:
            return None
        return self.points[0].timestamp

    @property
    def total_distance(self) -> float:
        """Return the total distance of the track based on the last point's distance_from_start."""
        if not self.points:
            return 0.0
        return self.points[-1].distance_from_start

    @property
    def total_elevation_gain(self) -> float:
        """Return the total elevation gain of the track: the sum of every positive elevation delta between consecutive points (descents don't subtract)."""
        gain = 0.0
        for prev, curr in zip(self.points, self.points[1:]):
            delta = curr.elevation - prev.elevation
            if delta > 0:
                gain += delta
        return gain


@dataclass
class RawAnchor:
    """A frontend-provided anchor candidate before normalization.

    Exactly one of `distance_from_start` or (`lat`, `lon`) must be provided;
    `pacing.anchors.build_user_anchors` resolves whichever is given into a
    concrete `Anchor`.

    Attributes:
        timestamp: The known time at this point along the route.
        distance_from_start: Distance in meters from the track start, if known directly.
        lat: Latitude of the anchor, used for nearest-point resolution when distance_from_start isn't given.
        lon: Longitude of the anchor, used the same way as lat.
        source: "user" | "photo".
    """
    timestamp: datetime
    distance_from_start: float | None = None
    lat: float | None = None
    lon: float | None = None
    source: str = "user"


@dataclass
class Anchor:
    """A resolved point along the track with a known timestamp.

    Anchors are the one mechanism for "known timestamp at some distance along
    the route" — pacing.combine fits per-leg speeds between each consecutive
    pair so the modeled time matches the anchor-to-anchor duration exactly.

    Attributes:
        distance_from_start: Distance in meters from the track start.
        timestamp: The known time at this point.
        source: "start" | "end" (the boundary anchors from
            pacing.anchors.add_start_end_anchors) | "user" | "photo" |
            "stop_arrival" | "stop_departure". Used only to word error
            messages.
    """
    distance_from_start: float
    timestamp: datetime
    source: str


@dataclass
class RawStop:
    """A frontend-provided stop candidate before normalization.

    A stop is a real pause at one location: distinct arrival and departure
    timestamps at the same distance_from_start. Exactly one of
    `distance_from_start` or (`lat`, `lon`) must be given (resolved the same
    way as `RawAnchor`). Exactly one of `duration` (Mode A: duration-only —
    the arrival time is derived from the normal pacing model, departure is
    arrival + duration) or (`start_timestamp`, `end_timestamp`) (Mode B:
    explicit arrival and departure) must be given.
    `pacing.stops.resolve_stops` resolves a RawStop into a concrete
    `ResolvedStop`.

    Attributes:
        distance_from_start: Distance in meters from the track start, if known directly.
        lat: Latitude of the stop, used for nearest-point resolution when distance_from_start isn't given.
        lon: Longitude of the stop, used the same way as lat.
        duration: Length of the pause, for Mode A (duration-only).
        start_timestamp: Arrival time, for Mode B (explicit start/end).
        end_timestamp: Departure time, for Mode B (explicit start/end).
    """
    distance_from_start: float | None = None
    lat: float | None = None
    lon: float | None = None
    duration: timedelta | None = None
    start_timestamp: datetime | None = None
    end_timestamp: datetime | None = None


@dataclass
class ResolvedStop:
    """A stop resolved to a concrete distance and (arrival, departure) pair.

    Attributes:
        distance_from_start: Distance in meters from the track start.
        arrival: The time the stop began.
        departure: The time the stop ended. Always later than arrival.
    """
    distance_from_start: float
    arrival: datetime
    departure: datetime


@dataclass
class ModeAStop:
    """A duration-only stop pending pacing (Mode A): not yet resolved to a
    fixed arrival/departure — pacing.combine derives its arrival as the
    natural gradient-paced timestamp at this distance and adds `duration`
    for departure.

    Attributes:
        distance_from_start: Distance in meters from the track start.
        duration: Length of the pause.
    """
    distance_from_start: float
    duration: timedelta


@dataclass
class RawPhotoAnchor:
    """A GPS + timestamp reading, read client-side from one photo's EXIF metadata.

    Attributes:
        lat: Latitude read from the photo's EXIF GPS tag, in degrees.
        lon: Longitude read from the photo's EXIF GPS tag, in degrees.
        timestamp: Capture time read from the photo's EXIF metadata.
    """
    lat: float
    lon: float
    timestamp: datetime


@dataclass
class ResolvedPhotoAnchor:
    """Outcome of resolving one RawPhotoAnchor against the track.

    Attributes:
        distance_from_start: Matched track point's distance from start, in meters.
        lat: Matched track point's latitude, in degrees.
        lon: Matched track point's longitude, in degrees.
        timestamp: The photo's own timestamp, carried through unchanged.
        status: "ok" if the match is usable as an anchor. Otherwise, in the
            order they're checked: "outside_activity_time" if the photo's
            timestamp isn't strictly inside the activity's time span,
            "too_far" if the match is beyond MAX_MATCH_DISTANCE_M, or
            "at_route_end" if it's the route's first or last point, where
            the start/end anchors already sit (see
            pacing.photo_anchors.resolve_photo_anchors).
        gap_m: Distance in meters between the photo's raw GPS and the matched point.
    """
    distance_from_start: float
    lat: float
    lon: float
    timestamp: datetime
    status: str
    gap_m: float


@dataclass
class ProfileSample:
    """One point of the post-conversion activity profile the GUI charts under the map.

    Built by activity_profile.build_activity_profile from a fully-paced
    track, downsampled so the chart gets a bounded number of points however
    dense the GPX is.

    Attributes:
        distance_from_start: Distance in meters from the track start.
        elapsed_seconds: Seconds since the activity's first timestamp (stops included).
        speed_mps: Average speed in m/s over the stretch ending at this
            sample, 0.0 for both samples of a stop.
        elevation: Elevation in meters, or None where the GPX had no elevation.
        lat: Latitude in degrees, so the GUI can mark the hovered spot on the map.
        lon: Longitude in degrees.
        is_stop: True for a stop's arrival and departure samples.
    """
    distance_from_start: float
    elapsed_seconds: float
    speed_mps: float
    elevation: float | None
    lat: float
    lon: float
    is_stop: bool = False
