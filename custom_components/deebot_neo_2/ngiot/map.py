"""Map raster decoding and rendering for NGIOT devices."""

import base64
import logging
from typing import Any

from .events import MapRasterEvent, RobotPositionEvent

_LOGGER = logging.getLogger(__name__)

_BACKGROUND = 127
_FALLBACK_COLOR = "#73808a"
_COLORS = {
    0: "#eef2f4",
    1: "#ffffff",
    2: "#5d6872",
    3: "#c5cdd2",
    4: "#8d9aa3",
    5: "#424c54",
    6: "#aeb9c0",
    7: "#dce4e8",
    8: "#303a42",
    9: "#97a5ae",
    10: "#e9ad52",
    255: "#d64d5d",
}


def decode_map_raster(map_data: dict[str, Any]) -> MapRasterEvent | None:
    """Decode the LZ4 compressed map raster."""
    encoded = map_data.get("map")
    width = map_data.get("width")
    height = map_data.get("height")
    compressed_length = map_data.get("lz4Len")
    if not isinstance(encoded, str) or not all(
        isinstance(value, int) and value > 0
        for value in (width, height, compressed_length)
    ):
        return None
    try:
        pixels = _decompress_lz4_block(base64.b64decode(encoded), compressed_length)
    except Exception:
        _LOGGER.exception("Unable to decode map raster")
        return None
    if len(pixels) != width * height:
        _LOGGER.error(
            "Map raster has %s pixels, expected %s", len(pixels), width * height
        )
        return None
    return MapRasterEvent(
        map_id=str(map_data.get("mapId", "")),
        width=width,
        height=height,
        resolution=int(map_data.get("resolution", 1)),
        pixels=pixels,
        x_min=float(map_data.get("xMin", 0)),
        y_max=float(map_data.get("yMax", height)),
    )


def _read_length(data: bytes, index: int, length: int) -> tuple[int, int]:
    while True:
        extra = data[index]
        index += 1
        length += extra
        if extra != 255:
            return length, index


def _decompress_lz4_block(data: bytes, expected_size: int) -> bytes:
    """Decompress a raw LZ4 block without a frame or size header."""
    output = bytearray()
    index = 0
    while index < len(data):
        token = data[index]
        index += 1
        literal_length = token >> 4
        if literal_length == 15:
            literal_length, index = _read_length(data, index, literal_length)
        output.extend(data[index : index + literal_length])
        index += literal_length
        if index >= len(data):
            break
        offset = int.from_bytes(data[index : index + 2], "little")
        index += 2
        if offset == 0 or offset > len(output):
            raise ValueError("Invalid LZ4 match offset")
        match_length = (token & 0x0F) + 4
        if (token & 0x0F) == 15:
            match_length, index = _read_length(data, index, match_length)
        for _ in range(match_length):
            output.append(output[-offset])
    if len(output) != expected_size:
        raise ValueError(
            f"Invalid LZ4 output size {len(output)}, expected {expected_size}"
        )
    return bytes(output)


def parse_robot_position(position: Any, map_id: str) -> RobotPositionEvent | None:
    """Parse the position object returned by the map endpoint."""
    if isinstance(position, dict):
        x = position.get("x")
        y = position.get("y")
        angle = position.get("a", position.get("angle", 0))
    elif isinstance(position, (list, tuple)) and len(position) >= 2:
        x, y = position[:2]
        angle = position[2] if len(position) > 2 else 0
    else:
        return None
    if not all(isinstance(value, (int, float)) for value in (x, y, angle)):
        return None
    return RobotPositionEvent(map_id, float(x), float(y), float(angle))


def render_svg(
    raster: MapRasterEvent, position: RobotPositionEvent | None = None
) -> bytes:
    """Render the raster and the robot position as SVG."""
    pixel_size = raster.resolution
    rects: list[str] = []
    for y in range(raster.height):
        row_start = y * raster.width
        x = 0
        while x < raster.width:
            value = raster.pixels[row_start + x]
            end = x + 1
            while end < raster.width and raster.pixels[row_start + end] == value:
                end += 1
            if value != _BACKGROUND:
                rects.append(
                    f'<rect x="{x * pixel_size}" y="{y * pixel_size}" '
                    f'width="{(end - x) * pixel_size}" height="{pixel_size}" '
                    f'fill="{_COLORS.get(value, _FALLBACK_COLOR)}"/>'
                )
            x = end
    width = raster.width * pixel_size
    height = raster.height * pixel_size
    marker = ""
    if position and position.map_id == raster.map_id:
        marker_x = (position.x - raster.x_min) / raster.resolution * pixel_size
        marker_y = (raster.y_max - position.y) / raster.resolution * pixel_size
        marker = (
            f'<g transform="translate({marker_x} {marker_y}) rotate({position.angle})" '
            'role="img" aria-label="Robot vacuum position">'
            "<title>Robot vacuum position</title>"
            '<circle r="12" fill="#1976d2" stroke="#ffffff" stroke-width="3"/>'
            '<path d="M 0,-18 L 7,-5 L -7,-5 Z" fill="#ffffff"/>'
            "</g>"
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}"><rect width="100%" height="100%" '
        f'fill="#7f7f7f"/>{"".join(rects)}{marker}</svg>'
    ).encode()
