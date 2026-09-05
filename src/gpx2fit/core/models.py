from dataclasses import dataclass
from datetime import datetime
from enum import Enum



class SportType(Enum):
    """Allowed sport types of the activity used for pacing and FIT file generation."""
    RUNNING = "running"
    HIKING = "hiking"
    # CYCLING = "cycling" will be added later

@dataclass
class TrackPoint:
    """Represents a single point in a track with geographic and temporal information."""
    lat: float
    lon: float
    elevation: float  # meters
    distance_from_start: float # meters, cumulative — computed, not from GPX
    timestamp: datetime | None = None  # None until pacing assigns one


@dataclass
class Track:
    """Represents a sequence of TrackPoints forming a route or activity."""
    points: list[TrackPoint]
    sport: SportType | None = None  # sport type, optional for now
    device: str | None = None  # device name to be embedded if available
    activity_name: str | None = None  # cosmetic, shown in Strava's feed

    @property
    def start_time(self) -> datetime | None: # todo: make sure the @property is correct, PyCharm is complaining about it
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
        """Return the total elevation gain of the track."""
        gain = 0.0
        for prev, curr in zip(self.points, self.points[1:]):
            delta = curr.elevation - prev.elevation
            if delta > 0:
                gain += delta
        return gain


@dataclass
class RawAnchor:
    """Frontend-provided anchor candidate before normalization."""
    timestamp: datetime
    distance_from_start: float | None = None
    lat: float | None = None
    lon: float | None = None
    source: str = "user"


@dataclass
class Anchor:
    """Represents a point in the track that is used as a reference for pacing."""
    distance_from_start: float  # position along the route
    timestamp: datetime
    source: str  # "user" | "photo"


# TODO: add better documentation to the codebase, photo_anchors, and surface

# push to github and perhaps make the repo public
# solve the out and back anchor point problem.
# add a bigger hit box around the track so anchor points are easier to add.

# the over map upload gpx first text doesn't go away.
# try to get some more colors and icons for the map, and perhaps a better map style. basically try to turn it to mapy.cz
# remove the box around the sun/moon icon and make them only black white not emojis if possible
# auto scroll down to the output box when the generation completes so the user sees it. rn its confusing a bit.
# openstreetmap trace track