"""Binary sensors for DEEBOT NEO 2."""

from deebot_client.device import Device

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Neo2Controller
from .const import DOMAIN
from .q287s6_app import Neo2StatusEvent


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up NEO 2 binary sensors."""
    controller: Neo2Controller = config_entry.runtime_data
    async_add_entities(
        [
            Neo2BooleanSensor(
                device, "childLock", "Child lock", BinarySensorDeviceClass.SAFETY
            )
            for device in controller.devices
        ]
        + [
            Neo2BooleanSensor(device, key, name)
            for device in controller.devices
            for key, name in (
                ("disturbSwitch", "Do not disturb"),
                ("dormant", "Dormant"),
                ("breakCleanStatus", "Break clean"),
                ("relocateSwitch", "Relocation mode"),
            )
        ]
    )


class Neo2BooleanSensor(BinarySensorEntity):
    """Boolean state reported by the robot."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_available = True

    def __init__(
        self,
        device: Device,
        key: str,
        name: str,
        device_class: BinarySensorDeviceClass | None = None,
    ) -> None:
        """Initialize a boolean status sensor."""
        self._device = device
        self._key = key
        self._attr_name = name
        self._attr_device_class = device_class
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

    async def async_added_to_hass(self) -> None:
        """Subscribe to child-lock updates."""
        await super().async_added_to_hass()

        async def on_status(event: Neo2StatusEvent) -> None:
            if (value := event.data.get(self._key)) is not None:
                self._attr_is_on = bool(value)
                self.async_write_ha_state()

        self.async_on_remove(self._device.events.subscribe(Neo2StatusEvent, on_status))
        self.async_schedule_update_ha_state(force_refresh=True)
