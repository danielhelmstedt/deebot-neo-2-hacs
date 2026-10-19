"""Support for Ecovacs vacuums using the NGIOT endpoint."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .controller import NgiotController

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.IMAGE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.VACUUM,
]
type NgiotConfigEntry = ConfigEntry[NgiotController]


async def async_setup_entry(hass: HomeAssistant, entry: NgiotConfigEntry) -> bool:
    """Set up this integration using UI."""
    controller = NgiotController(hass, entry.data)

    entry.async_on_unload(controller.teardown)

    await controller.initialize()

    entry.runtime_data = controller

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NgiotConfigEntry) -> bool:
    """Unload config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
