from dataclasses import dataclass
from datetime import datetime
from enum import Enum



class SportType(Enum):
    RUNNING = "running"
    HIKING = "hiking"
    # CYCLING = "cycling" will be added later

@dataclass
class TrackPoint:
    lat: float
    lon: float
    elevation: float  # meters
    distance_from_start: float # meters, cumulative — computed, not from GPX
    timestamp: datetime | None = None  # None until pacing assigns one


@dataclass
class Track:
    points: list[TrackPoint]
    sport: SportType | None = None  # sport type, optional for now
    device: str | None = None  # device name to be embedded if available
    activity_name: str | None = None  # cosmetic, shown in Strava's feed

    @property
    def start_time(self) -> datetime | None: # todo: make sure the @property is correct, PyCharm is complaining about it
        if not self.points or self.points[0].timestamp is None:
            return None
        return self.points[0].timestamp

    @property
    def total_distance(self) -> float:
        if not self.points:
            return 0.0
        return self.points[-1].distance_from_start

    @property
    def total_elevation_gain(self) -> float:
        gain = 0.0
        for prev, curr in zip(self.points, self.points[1:]):
            delta = curr.elevation - prev.elevation
            if delta > 0:
                gain += delta
        return gain


@dataclass
class Anchor:
    distance_from_start: float  # position along the route
    timestamp: datetime
    source: str  # "user" | "photo"
