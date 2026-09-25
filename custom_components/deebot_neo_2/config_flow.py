"""Config flow for the DEEBOT NEO 2 integration."""

from collections.abc import Mapping
import logging
from typing import Any

from aiohttp import ClientError
from deebot_client.api_client import ApiClient
from deebot_client.authentication import Authenticator, create_rest_config
from deebot_client.exceptions import (
    DeviceVerificationRequiredError,
    InvalidVerificationCodeError,
)
from deebot_client.util import md5
import probatio

from homeassistant.components.ecovacs.config_flow import (
    _validate_input as _ecovacs_validate_input,
)
from homeassistant.components.ecovacs.const import CONF_VERIFICATION_CODE
from homeassistant.components.ecovacs.util import get_client_device_id
from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult
from homeassistant.const import (
    CONF_COUNTRY,
    CONF_DEVICE_ID,
    CONF_PASSWORD,
    CONF_USERNAME,
)
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import aiohttp_client, selector

from . import _patch_deebot_client
from .const import (
    CONF_DEVICE_DID,
    CONF_DEVICE_NAME,
    CONF_DEVICE_RESOURCE,
    DOMAIN,
    SUPPORTED_DEVICE_CLASSES,
)

_LOGGER = logging.getLogger(__name__)

NEO_2_LOGIC_ID = "y30plus_ww_h_y30h5"


def _device_label(info: dict[str, Any]) -> str:
    return str(
        info.get("nick") or info.get("deviceName") or info.get("name") or info["did"]
    )


def _device_api_info(device: Any) -> dict[str, Any]:
    return device.api if hasattr(device, "api") else device


def _is_supported_neo_2(info: dict[str, Any]) -> bool:
    device_name = str(info.get("deviceName") or "")
    return (
        info.get("class") in SUPPORTED_DEVICE_CLASSES
        or "NEO 2.0" in device_name
        or info.get("UILogicId") == NEO_2_LOGIC_ID
    )


async def _find_supported_devices(
    api_client: ApiClient,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Return q287s6 devices after official auth validation succeeds."""
    errors: dict[str, str] = {}
    try:
        _patch_deebot_client()
        devices = await api_client.get_devices()
    except ConfigEntryNotReady:
        _LOGGER.debug(
            "Cannot connect to Ecovacs during device discovery", exc_info=True
        )
        errors["base"] = "cannot_connect"
        return [], errors
    except ConfigEntryError:
        _LOGGER.debug(
            "Invalid Ecovacs authentication details during discovery", exc_info=True
        )
        errors["base"] = "invalid_auth"
        return [], errors
    except Exception:
        _LOGGER.exception("Unexpected exception during DEEBOT NEO 2 setup")
        errors["base"] = "unknown"
        return [], errors
    discovered = [
        _device_api_info(device) for device in devices.mqtt
    ] + devices.not_supported
    for info in discovered:
        _LOGGER.debug(
            "Ecovacs discovery saw device class=%s deviceName=%s",
            info.get("class"),
            info.get("deviceName"),
        )

    supported = [info for info in discovered if _is_supported_neo_2(info)]
    if not supported:
        errors["base"] = "no_supported_vacuums"
    return supported, errors


class DeebotNeo2ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for DEEBOT NEO 2."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._auth_input: dict[str, Any] = {}
        self._devices: list[dict[str, Any]] = []
        self._authenticator: Authenticator | None = None
        self._device_id = ""

    async def _async_set_authenticator(
        self, user_input: dict[str, Any]
    ) -> Authenticator:
        await self._async_teardown_authenticator()
        self._auth_input = dict(user_input)
        self._device_id = get_client_device_id(self.hass, False, user_input)
        self._auth_input[CONF_DEVICE_ID] = self._device_id
        self._authenticator = Authenticator(
            create_rest_config(
                aiohttp_client.async_get_clientsession(self.hass),
                device_id=self._device_id,
                alpha_2_country=user_input[CONF_COUNTRY],
            ),
            user_input[CONF_USERNAME],
            md5(user_input[CONF_PASSWORD]),
        )
        return self._authenticator

    async def _async_teardown_authenticator(self) -> None:
        if self._authenticator is not None:
            await self._authenticator.teardown()
            self._authenticator = None

    @callback
    def async_remove(self) -> None:
        """Handle flow removal."""
        super().async_remove()
        if self._authenticator is not None:
            self.hass.async_create_background_task(
                self._async_teardown_authenticator(),
                name="deebot_neo_2_config_flow_authenticator_teardown",
            )

    async def _async_validate_and_find_devices(
        self,
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        try:
            errors = await _ecovacs_validate_input(
                self.hass,
                self._auth_input,
                self._device_id,
                self._authenticator,
            )
        except DeviceVerificationRequiredError:
            if self._authenticator is None:
                return [], {"base": "unknown"}
            try:
                await self._authenticator.request_device_verification_code()
            except ClientError:
                return [], {"base": "cannot_connect"}
            except Exception:
                _LOGGER.exception("Unexpected exception requesting verification code")
                return [], {"base": "unknown"}
            return [], {"base": "device_verification_required"}
        if errors:
            return [], errors
        if self._authenticator is None:
            return [], {"base": "unknown"}
        return await _find_supported_devices(ApiClient(self._authenticator))

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle Ecovacs account details."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._devices = []
            await self._async_set_authenticator(user_input)
            devices, errors = await self._async_validate_and_find_devices()
            if errors.get("base") == "device_verification_required":
                return await self.async_step_device_verification()
            if not errors:
                self._devices = devices
                if len(devices) == 1:
                    return await self._create_entry(devices[0])
                return await self.async_step_select_device()

        defaults = dict(user_input or {CONF_COUNTRY: self.hass.config.country})
        if errors:
            defaults.pop(CONF_PASSWORD, None)
        schema = probatio.Schema(
            {
                probatio.Required(CONF_USERNAME): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
                probatio.Required(CONF_PASSWORD): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                probatio.Required(CONF_COUNTRY): selector.CountrySelector(),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, defaults),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauthentication after Ecovacs revokes the trusted device."""
        self._auth_input = dict(entry_data)
        self._auth_input.pop(CONF_PASSWORD, None)
        self._auth_input.pop(CONF_DEVICE_ID, None)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm credentials and repeat device verification if required."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._auth_input[CONF_PASSWORD] = user_input[CONF_PASSWORD]
            await self._async_set_authenticator(self._auth_input)
            devices, errors = await self._async_validate_and_find_devices()
            if errors.get("base") == "device_verification_required":
                return await self.async_step_device_verification()
            if not errors:
                self._devices = devices
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates=self._auth_input,
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_PASSWORD): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    )
                }
            ),
            description_placeholders={CONF_USERNAME: self._auth_input[CONF_USERNAME]},
            errors=errors,
        )

    async def async_step_select_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user choose a q287s6 vacuum."""
        if user_input is not None:
            selected = next(
                device
                for device in self._devices
                if device["did"] == user_input[CONF_DEVICE_DID]
            )
            return await self._create_entry(selected)

        options = {device["did"]: _device_label(device) for device in self._devices}
        return self.async_show_form(
            step_id="select_device",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_DEVICE_DID): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value=value, label=label)
                                for value, label in options.items()
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_device_verification(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Verify the device with the code sent by Ecovacs."""
        errors: dict[str, str] = {}
        if user_input and self._authenticator is not None:
            try:
                await self._authenticator.verify_device(
                    user_input[CONF_VERIFICATION_CODE]
                )
            except InvalidVerificationCodeError:
                errors["base"] = "invalid_verification_code"
            except ClientError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception verifying Ecovacs device")
                errors["base"] = "unknown"
            else:
                devices, errors = await self._async_validate_and_find_devices()
                if not errors:
                    self._devices = devices
                    if self.source == SOURCE_REAUTH:
                        return self.async_update_reload_and_abort(
                            self._get_reauth_entry(),
                            data_updates=self._auth_input,
                        )
                    if len(devices) == 1:
                        return await self._create_entry(devices[0])
                    return await self.async_step_select_device()

        return self.async_show_form(
            step_id="device_verification",
            data_schema=self.add_suggested_values_to_schema(
                probatio.Schema(
                    {
                        probatio.Required(
                            CONF_VERIFICATION_CODE
                        ): selector.TextSelector(
                            selector.TextSelectorConfig(
                                type=selector.TextSelectorType.TEXT
                            )
                        )
                    }
                ),
                user_input,
            ),
            description_placeholders={CONF_USERNAME: self._auth_input[CONF_USERNAME]},
            errors=errors,
        )

    async def _create_entry(self, device: dict[str, Any]) -> ConfigFlowResult:
        await self.async_set_unique_id(device["did"])
        self._abort_if_unique_id_configured()
        data = dict(self._auth_input)
        data[CONF_DEVICE_DID] = device["did"]
        data[CONF_DEVICE_RESOURCE] = device.get("resource")
        data[CONF_DEVICE_NAME] = _device_label(device)
        return self.async_create_entry(title=_device_label(device), data=data)
