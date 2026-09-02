from dataclasses import dataclass
from datetime import datetime


@dataclass
class TrackPoint:
    lat: float
    lon: float
    elevation: float|None  # meters
    distance_from_start: float # meters, cumulative — computed, not from GPX
    timestamp: datetime | None = None  # None until pacing assigns one


@dataclass
class Track:
    points: list[TrackPoint]
    sport: str | None = None  # sport type, optional for now
    device: str | None = None  # device name to be embedded if available
    # not entirely sure about the following three
    total_distance: float | None = None  # total distance of the track
    total_elevation_gain: float | None = None  # total elevation gain of the track
    start_time: datetime | None = None


@dataclass
class Anchor:
    distance_from_start: float  # position along the route
    timestamp: datetime
    source: str  # "user" | "photo"


# write a proper plan file or paper.
# think this all through this file is important
# then do the gpx reader and fit writer, those are also important, but they should be easier to write.
