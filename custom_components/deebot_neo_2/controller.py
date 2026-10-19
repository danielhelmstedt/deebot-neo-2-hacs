"""Controller module."""

from collections.abc import Mapping
import logging
from typing import Any

from deebot_client.api_client import ApiClient
from deebot_client.authentication import Authenticator, create_rest_config
from deebot_client.exceptions import (
    DeebotError,
    DeviceVerificationRequiredError,
    InvalidAuthenticationError,
)
from deebot_client.models import ApiDeviceInfo, DeviceInfo
from deebot_client.mqtt_client import MqttClient, create_mqtt_config
from deebot_client.util import md5

from homeassistant.const import (
    CONF_COUNTRY,
    CONF_DEVICE_ID,
    CONF_PASSWORD,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import aiohttp_client

from .const import CONF_DEVICE_DID
from .ngiot.device import NgiotDevice
from .ngiot.hardware import get_static_device_info

_LOGGER = logging.getLogger(__name__)

_NGIOT_COMPANY = "eco-ng"


def is_ngiot_candidate(device: ApiDeviceInfo) -> bool:
    """Return True for next-gen devices that the Ecovacs core integration skips."""
    return device.get("company") == _NGIOT_COMPANY


class NgiotController:
    """Controller of the devices of one Ecovacs account."""

    def __init__(self, hass: HomeAssistant, config: Mapping[str, Any]) -> None:
        """Initialize controller."""
        self._hass = hass
        self._devices: list[NgiotDevice] = []
        self._device_did: str | None = config.get(CONF_DEVICE_DID)
        self._device_id: str = config[CONF_DEVICE_ID]
        self._country: str = config[CONF_COUNTRY]

        self._authenticator = Authenticator(
            create_rest_config(
                aiohttp_client.async_get_clientsession(self._hass),
                device_id=self._device_id,
                alpha_2_country=self._country,
            ),
            config[CONF_USERNAME],
            md5(config[CONF_PASSWORD]),
        )
        self._api_client = ApiClient(self._authenticator)
        self._mqtt_client: MqttClient | None = None

    async def initialize(self) -> None:
        """Init controller."""
        try:
            devices = await self._api_client.get_devices()
            api_infos = [
                *(info.api for info in devices.mqtt),
                *devices.not_supported,
            ]
            candidates = [
                info
                for info in api_infos
                if is_ngiot_candidate(info)
                and (self._device_did is None or info["did"] == self._device_did)
            ]
            if not candidates:
                raise ConfigEntryNotReady("No matching device found")

            mqtt = await self._get_mqtt_client()
            for api_info in candidates:
                device = NgiotDevice(
                    DeviceInfo(api_info, get_static_device_info()),
                    self._authenticator,
                )
                await device.initialize(mqtt)
                self._devices.append(device)
        except DeviceVerificationRequiredError as ex:
            raise ConfigEntryAuthFailed("Device verification required") from ex
        except InvalidAuthenticationError as ex:
            raise ConfigEntryAuthFailed("Invalid credentials") from ex
        except DeebotError as ex:
            raise ConfigEntryNotReady("Error during setup") from ex

        _LOGGER.debug("Controller initialize complete")

    async def teardown(self) -> None:
        """Disconnect controller."""
        for device in self._devices:
            await device.teardown()
        if self._mqtt_client is not None:
            await self._mqtt_client.disconnect()
        await self._authenticator.teardown()

    async def _get_mqtt_client(self) -> MqttClient:
        """Return validated MQTT client."""
        if self._mqtt_client is None:
            config = await self._hass.async_add_executor_job(
                lambda: create_mqtt_config(
                    device_id=self._device_id, country=self._country
                )
            )
            mqtt = MqttClient(config, self._authenticator)
            await mqtt.verify_config()
            self._mqtt_client = mqtt

        return self._mqtt_client

    @property
    def devices(self) -> list[NgiotDevice]:
        """Return devices."""
        return self._devices
