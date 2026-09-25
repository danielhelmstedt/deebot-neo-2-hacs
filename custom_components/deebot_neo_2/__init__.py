"""DEEBOT NEO 2 custom integration."""

import asyncio
from collections.abc import Mapping
import logging
import sys
from typing import Any

from deebot_client.device import Device
import deebot_client.hardware as deebot_hardware
from deebot_client.mqtt_client import SubscriberInfo

from homeassistant.components.ecovacs.controller import EcovacsController
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from . import q287s6_app, q287s6_profile
from .const import CONF_DEVICE_DID, PLATFORMS, SUPPORTED_DEVICE_CLASSES

_LOGGER = logging.getLogger(__name__)


def _patch_deebot_client() -> None:
    """Expose NEO 2 hardware classes to deebot_client before devices initialize."""
    sys.modules.setdefault("deebot_client.commands.json.q287s6_app", q287s6_app)
    sys.modules.setdefault("deebot_client.hardware.q287s6", q287s6_profile)
    sys.modules.setdefault("deebot_client.hardware.eyfj07", q287s6_profile)
    if not_found := getattr(deebot_hardware, "_NOT_FOUND", None):
        not_found.difference_update(SUPPORTED_DEVICE_CLASSES)


class Neo2Controller(EcovacsController):
    """Official Ecovacs controller with q287s6 registration and filtering."""

    def __init__(self, hass: HomeAssistant, config: Mapping[str, Any]) -> None:
        """Initialize the NEO 2 controller."""
        super().__init__(hass, config)
        self._config = config

    async def initialize(self) -> None:
        """Register q287s6, then run the official Ecovacs controller setup."""
        _patch_deebot_client()
        await super().initialize()

        selected_did = self._config.get(CONF_DEVICE_DID)
        selected_devices: list[Device] = []
        for device in self._devices:
            if device.device_info.get("class") in SUPPORTED_DEVICE_CLASSES and (
                selected_did is None or device.device_info.get("did") == selected_did
            ):
                selected_devices.append(device)
            else:
                await device.teardown()

        self._devices = selected_devices
        if not self._devices:
            raise ConfigEntryNotReady("No selected q287s6 device found")

        mqtt = await self._get_mqtt_client()
        for device in self._devices:

            def on_message(
                topic_family: str,
                payload: str | bytes | bytearray,
                device: Device = device,
            ) -> None:
                if topic_family not in {"10000", "30000"}:
                    device._handle_message(topic_family, payload)  # noqa: SLF001
                q287s6_app.handle_neo2_live_payload(
                    device.events, topic_family, payload
                )

            await mqtt.subscribe(
                SubscriberInfo(device._device_info, device.events, on_message)  # noqa: SLF001
            )

        for device in self._devices:
            # Prime the event bus with a full status snapshot so entities have a
            # value as soon as they subscribe, instead of showing unavailable
            # until the next live MQTT push happens to arrive. The first attempt
            # often races ahead of the MQTT topic subscriptions reaching the
            # broker and comes back empty, so retry until real data arrives.
            for _attempt in range(5):
                response = await device.execute_command(
                    q287s6_app.Q287s6EndpointStatus()
                )
                if response.get("body", {}).get("data"):
                    break
                await asyncio.sleep(1)
            else:
                _LOGGER.warning(
                    "Could not fetch an initial status snapshot for %s",
                    device.device_info.get("did"),
                )

        _LOGGER.debug("Initialized %s q287s6 device(s)", len(self._devices))


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up DEEBOT NEO 2 from a config entry."""
    controller = Neo2Controller(hass, entry.data)
    await controller.initialize()
    entry.runtime_data = controller
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    controller: Neo2Controller = entry.runtime_data
    await controller.teardown()
    return unload_ok
