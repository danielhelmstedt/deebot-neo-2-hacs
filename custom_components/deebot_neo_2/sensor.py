"""Sensors for DEEBOT NEO 2."""

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from deebot_client.device import Device
from deebot_client.events import BatteryEvent
from deebot_client.events.base import Event

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfArea, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Neo2Controller
from .const import DOMAIN
from .q287s6_app import Neo2StatusEvent


@dataclass(frozen=True)
class _StatusDescription:
    key: str
    name: str
    unit: str | None = None
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None


_STATUS_DESCRIPTIONS = (
    _StatusDescription(
        "cleanArea",
        "Clean area",
        UnitOfArea.SQUARE_METERS,
        SensorDeviceClass.AREA,
        SensorStateClass.MEASUREMENT,
    ),
    _StatusDescription(
        "cleanTime",
        "Clean time",
        UnitOfTime.MINUTES,
        SensorDeviceClass.DURATION,
        SensorStateClass.MEASUREMENT,
    ),
    _StatusDescription(
        "cleanCount", "Clean count", state_class=SensorStateClass.TOTAL_INCREASING
    ),
    _StatusDescription(
        "cleanAreaTotal",
        "Total clean area",
        UnitOfArea.SQUARE_METERS,
        SensorDeviceClass.AREA,
        SensorStateClass.TOTAL_INCREASING,
    ),
    _StatusDescription(
        "cleanCountTotal",
        "Total clean count",
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    _StatusDescription(
        "cleanTimeTotal",
        "Total clean time",
        UnitOfTime.MINUTES,
        SensorDeviceClass.DURATION,
        SensorStateClass.TOTAL_INCREASING,
    ),
    _StatusDescription("volume", "Volume", state_class=SensorStateClass.MEASUREMENT),
)

_TEXT_DESCRIPTIONS = (
    _StatusDescription("workMode", "Work mode"),
    _StatusDescription("waterMode", "Water mode"),
    _StatusDescription("mopState", "Mop state"),
    _StatusDescription("unitSet", "Unit setting"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up NEO 2 sensors."""
    controller: Neo2Controller = config_entry.runtime_data
    entities: list[SensorEntity] = []
    for device in controller.devices:
        entities.append(Neo2BatterySensor(device))
        entities.extend(
            Neo2StatusSensor(device, description)
            for description in _STATUS_DESCRIPTIONS
        )
        entities.extend(
            Neo2StatusSensor(device, description) for description in _TEXT_DESCRIPTIONS
        )
        entities.extend(
            Neo2ConsumableSensor(device, consumable)
            for consumable in ("sideBrush", "rollBrush", "filter", "unitCare")
        )
    async_add_entities(entities)


class _Neo2Entity(SensorEntity):
    """Shared NEO 2 sensor behavior."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_available = True

    def __init__(self, device: Device, key: str) -> None:
        self._device = device
        self._subscribed_events: set[type[Event]] = set()
        self._attr_unique_id = f"{device.device_info['did']}_{key}"

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

    def _subscribe(
        self,
        event_type: type[Event],
        callback: Callable[[Event], Coroutine[Any, Any, None]],
    ) -> None:
        self._subscribed_events.add(event_type)
        self.async_on_remove(self._device.events.subscribe(event_type, callback))

    async def async_update(self) -> None:
        """Request a status refresh."""
        for event_type in self._subscribed_events:
            self._device.events.request_refresh(event_type)


class Neo2BatterySensor(_Neo2Entity):
    """Battery level sensor."""

    _attr_name = "Battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, device: Device) -> None:
        """Initialize the battery sensor."""
        super().__init__(device, "battery")

    async def async_added_to_hass(self) -> None:
        """Subscribe to battery updates."""
        await super().async_added_to_hass()

        async def on_battery(event: BatteryEvent) -> None:
            if event.value is not None:
                self._attr_native_value = int(event.value)
                self.async_write_ha_state()

        self._subscribe(BatteryEvent, on_battery)
        self.async_schedule_update_ha_state(force_refresh=True)


class Neo2StatusSensor(_Neo2Entity):
    """Sensor backed by an enriched 10001 status field."""

    def __init__(self, device: Device, description: _StatusDescription) -> None:
        """Initialize a status sensor."""
        super().__init__(device, description.key)
        self._key = description.key
        self._attr_name = description.name
        self._attr_native_unit_of_measurement = description.unit
        self._attr_device_class = description.device_class
        self._attr_state_class = description.state_class

    async def async_added_to_hass(self) -> None:
        """Subscribe to enriched status updates."""
        await super().async_added_to_hass()

        async def on_status(event: Neo2StatusEvent) -> None:
            if (value := event.data.get(self._key)) is not None:
                self._attr_native_value = value
                self.async_write_ha_state()

        self._subscribe(Neo2StatusEvent, on_status)
        self.async_schedule_update_ha_state(force_refresh=True)


class Neo2ConsumableSensor(_Neo2Entity):
    """Consumable remaining-life sensor."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, device: Device, consumable: str) -> None:
        """Initialize a consumable sensor."""
        super().__init__(device, f"consumable_{consumable}")
        self._consumable = consumable
        self._attr_name = consumable

    async def async_added_to_hass(self) -> None:
        """Subscribe to consumable updates."""
        await super().async_added_to_hass()

        async def on_status(event: Neo2StatusEvent) -> None:
            for item in event.data.get("consumables", []):
                if (
                    item.get("type") == self._consumable
                    and item.get("left") is not None
                ):
                    self._attr_native_value = item["left"]
                    self._attr_extra_state_attributes = {"total": item.get("total")}
                    self.async_write_ha_state()

        self._subscribe(Neo2StatusEvent, on_status)
        self.async_schedule_update_ha_state(force_refresh=True)
