"""Shared data model: the point/track/anchor types every core module reads and writes."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


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
        device: Device name to embed in the FIT file, if available.
        activity_name: Cosmetic activity name, shown in Strava's feed.
    """
    points: list[TrackPoint]
    sport: SportType | None = None
    device: str | None = None
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
        source: "user" | "photo".
    """
    distance_from_start: float
    timestamp: datetime
    source: str


# TODO:
# 1. Check the limits. (fallback would be Enri maps). day_1 = 1803 requests(1%).
# 2. Read the test_files and verify they are all looking good.
# 3. Add the padding, device name and stops to ui and also backend (and possibly think of more useful data user could add) and add JS tests while doing so.
# 4. Add the photo anchors feature
# 5. Implement surface + max speed capping speed adjustments
# 6. Add the graph with activity data below the map once converted.
# 7. Make sure the app works also for phones.
# 8. Add cycling sport type. It will require separate speed computing logic and all.


# take a look at the PyCharm MCP for Claude
