"""Map image platform for DEEBOT NEO 2."""

from typing import cast

from deebot_client.capabilities import CapabilityMap
from deebot_client.device import Device
from deebot_client.map import Map

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Neo2Controller
from .const import DOMAIN
from .q287s6_app import Neo2MapRasterEvent, Neo2RobotPositionEvent

_BACKGROUND = 127
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


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the NEO 2 map image."""
    controller: Neo2Controller = config_entry.runtime_data
    async_add_entities(
        [
            Neo2Map(device, hass)
            for device in controller.devices
            if device.capabilities.map
        ]
    )


class Neo2Map(ImageEntity):
    """Render the map assembled by deebot-client."""

    _attr_content_type = "image/svg+xml"
    _attr_has_entity_name = True

    def __init__(self, device: Device, hass: HomeAssistant) -> None:
        """Initialize the map image."""
        super().__init__(hass)
        self._device = device
        self._capability = cast(CapabilityMap, device.capabilities.map)
        self._map = cast(Map, device.map)
        self._attr_unique_id = f"{device.device_info['did']}_map"
        self._attr_name = "Map"
        self._attr_extra_state_attributes = {}
        self._raster: Neo2MapRasterEvent | None = None
        self._position: Neo2RobotPositionEvent | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """Return the robot device information."""
        info = self._device.device_info
        return DeviceInfo(
            identifiers={(DOMAIN, info["did"])},
            manufacturer="Ecovacs",
            model=info.get("deviceName", "DEEBOT NEO 2"),
            model_id=info.get("class"),
            name=info.get("nick") or info.get("deviceName") or "DEEBOT NEO 2",
            serial_number=info.get("name") or info["did"],
            sw_version=self._device.fw_version,
        )

    def image(self) -> bytes | None:
        """Return the current NEO 2 raster as SVG."""
        if self._raster is None:
            return None
        pixel_size = self._raster.resolution
        rects: list[str] = []
        pixels = self._raster.pixels
        for y in range(self._raster.height):
            row_start = y * self._raster.width
            x = 0
            while x < self._raster.width:
                value = pixels[row_start + x]
                end = x + 1
                while end < self._raster.width and pixels[row_start + end] == value:
                    end += 1
                if value != _BACKGROUND:
                    rects.append(
                        f'<rect x="{x * pixel_size}" y="{y * pixel_size}" '
                        f'width="{(end - x) * pixel_size}" height="{pixel_size}" '
                        f'fill="{_COLORS.get(value, "#73808a")}"/>'
                    )
                x = end
        width = self._raster.width * pixel_size
        height = self._raster.height * pixel_size
        marker = ""
        if self._position and self._position.map_id == self._raster.map_id:
            marker_x = (self._position.x - self._raster.x_min) / self._raster.resolution
            marker_y = (self._raster.y_max - self._position.y) / self._raster.resolution
            marker_x *= pixel_size
            marker_y *= pixel_size
            marker = (
                f'<g transform="translate({marker_x} {marker_y}) rotate({self._position.angle})" '
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

    async def async_added_to_hass(self) -> None:
        """Subscribe to map updates."""
        await super().async_added_to_hass()

        async def on_info(event) -> None:
            for map_info in event.maps:
                if map_info.using:
                    self._attr_extra_state_attributes["map_name"] = map_info.name

        async def on_changed(event) -> None:
            self._attr_image_last_updated = event.when
            self.async_write_ha_state()

        async def on_raster(event: Neo2MapRasterEvent) -> None:
            self._raster = event
            self.async_write_ha_state()

        async def on_position(event: Neo2RobotPositionEvent) -> None:
            self._position = event
            self.async_write_ha_state()

        self.async_on_remove(
            self._device.events.subscribe(self._capability.cached_info.event, on_info)
        )
        self.async_on_remove(
            self._device.events.subscribe(self._capability.changed.event, on_changed)
        )
        self.async_on_remove(
            self._device.events.subscribe(Neo2MapRasterEvent, on_raster)
        )
        self.async_on_remove(
            self._device.events.subscribe(Neo2RobotPositionEvent, on_position)
        )
        self.async_schedule_update_ha_state(force_refresh=True)

    async def async_update(self) -> None:
        """Refresh the map data."""
        self._map.refresh()
