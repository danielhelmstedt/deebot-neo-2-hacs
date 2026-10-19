"""Map image entity."""

from typing import cast, override

from deebot_client.capabilities import CapabilityMap
from deebot_client.device import Device
from deebot_client.events.map import CachedMapInfoEvent, MapChangedEvent
from deebot_client.map import Map

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import NgiotConfigEntry
from .entity import NgiotEntity
from .ngiot.events import MapRasterEvent, RobotPositionEvent
from .ngiot.map import render_svg


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: NgiotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add entities for passed config_entry in HA."""
    controller = config_entry.runtime_data
    entities = [
        NgiotMap(device, caps, hass)
        for device in controller.devices
        if (caps := device.capabilities.map)
    ]

    if entities:
        async_add_entities(entities)


class NgiotMap(
    NgiotEntity[CapabilityMap],
    ImageEntity,
):
    """Map."""

    _attr_content_type = "image/svg+xml"

    def __init__(
        self,
        device: Device,
        capability: CapabilityMap,
        hass: HomeAssistant,
    ) -> None:
        """Initialize entity."""
        super().__init__(device, capability, hass=hass)
        self._attr_extra_state_attributes = {}
        self._map = cast(Map, self._device.map)
        self._raster: MapRasterEvent | None = None
        self._position: RobotPositionEvent | None = None

    entity_description = EntityDescription(
        key="map",
        translation_key="map",
    )

    @override
    def image(self) -> bytes | None:
        """Return bytes of image or None."""
        if self._raster is None:
            return None
        return render_svg(self._raster, self._position)

    @override
    async def async_added_to_hass(self) -> None:
        """Set up the event listeners now that hass is ready."""
        await super().async_added_to_hass()

        async def on_info(event: CachedMapInfoEvent) -> None:
            for map_obj in event.maps:
                if map_obj.using:
                    self._attr_extra_state_attributes["map_name"] = map_obj.name

        async def on_changed(event: MapChangedEvent) -> None:
            self._attr_image_last_updated = event.when
            self.async_write_ha_state()

        async def on_raster(event: MapRasterEvent) -> None:
            self._raster = event
            self._attr_image_last_updated = dt_util.utcnow()
            self.async_write_ha_state()

        async def on_position(event: RobotPositionEvent) -> None:
            self._position = event
            self._attr_extra_state_attributes["robot_position"] = {
                "x": event.x,
                "y": event.y,
                "angle": event.angle,
            }
            self._attr_image_last_updated = dt_util.utcnow()
            self.async_write_ha_state()

        self._subscribe(self._capability.cached_info.event, on_info)
        self._subscribe(self._capability.changed.event, on_changed)
        self._subscribe(MapRasterEvent, on_raster)
        self._subscribe(RobotPositionEvent, on_position)

    @override
    async def async_update(self) -> None:
        """Update the entity.

        Only used by the generic entity update service.
        """
        await super().async_update()
        self._map.refresh()
