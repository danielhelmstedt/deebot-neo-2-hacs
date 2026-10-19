"""Diagnostics."""

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_NAME, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import NgiotConfigEntry
from .const import CONF_DEVICE_DID

REDACT_CONFIG = {CONF_USERNAME, CONF_PASSWORD, CONF_DEVICE_DID, "title"}
REDACT_DEVICE = {"did", CONF_NAME, "homeId"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: NgiotConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    controller = config_entry.runtime_data
    return {
        "config": async_redact_data(config_entry.as_dict(), REDACT_CONFIG),
        "devices": [
            async_redact_data(device.device_info, REDACT_DEVICE)
            for device in controller.devices
        ],
    }
