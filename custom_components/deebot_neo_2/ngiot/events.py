"""Events emitted by NGIOT commands and live messages."""

from dataclasses import dataclass
from typing import Any

from deebot_client.events.base import Event


@dataclass(frozen=True)
class StatusEvent(Event):
    """Full status snapshot as reported by the device."""

    data: dict[str, Any]


@dataclass(frozen=True)
class MapRasterEvent(Event):
    """Decoded map raster."""

    map_id: str
    width: int
    height: int
    resolution: int
    pixels: bytes
    x_min: float
    y_max: float


@dataclass(frozen=True)
class RobotPositionEvent(Event):
    """Robot position in map coordinates."""

    map_id: str
    x: float
    y: float
    angle: float
