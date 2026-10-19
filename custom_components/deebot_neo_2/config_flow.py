"""Config flow."""

from collections.abc import Mapping
import logging
from typing import Any

from aiohttp import ClientError
from deebot_client.api_client import ApiClient
from deebot_client.authentication import Authenticator, create_rest_config
from deebot_client.exceptions import (
    DeebotError,
    DeviceVerificationRequiredError,
    InvalidAuthenticationError,
    InvalidVerificationCodeError,
    MqttError,
)
from deebot_client.mqtt_client import MqttClient, create_mqtt_config
from deebot_client.util import md5
import probatio

from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult
from homeassistant.const import (
    CONF_COUNTRY,
    CONF_DEVICE_ID,
    CONF_PASSWORD,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import aiohttp_client, selector

from .const import CONF_DEVICE_DID, CONF_VERIFICATION_CODE, DOMAIN
from .controller import is_ngiot_candidate
from .util import get_client_device_id

_LOGGER = logging.getLogger(__name__)


def _device_label(info: Mapping[str, Any]) -> str:
    return str(
        info.get("nick") or info.get("deviceName") or info.get("name") or info["did"]
    )


async def _validate_input(
    hass: HomeAssistant,
    user_input: Mapping[str, Any],
    device_id: str,
    authenticator: Authenticator,
) -> dict[str, str]:
    """Validate the login and the MQTT connection."""
    errors: dict[str, str] = {}

    try:
        await authenticator.authenticate()
    except DeviceVerificationRequiredError:
        raise
    except ClientError:
        _LOGGER.debug("Cannot connect", exc_info=True)
        return {"base": "cannot_connect"}
    except InvalidAuthenticationError:
        return {"base": "invalid_auth"}
    except Exception:
        _LOGGER.exception("Unexpected exception during login")
        return {"base": "unknown"}

    mqtt_config = await hass.async_add_executor_job(
        lambda: create_mqtt_config(
            device_id=device_id, country=user_input[CONF_COUNTRY]
        )
    )
    try:
        await MqttClient(mqtt_config, authenticator).verify_config()
    except MqttError:
        _LOGGER.debug("Cannot connect", exc_info=True)
        errors["base"] = "cannot_connect"
    except InvalidAuthenticationError:
        errors["base"] = "invalid_auth"
    except Exception:
        _LOGGER.exception("Unexpected exception during mqtt connection verification")
        errors["base"] = "unknown"

    return errors


async def _find_devices(
    authenticator: Authenticator,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Return the account's next-gen devices not handled by the Ecovacs core integration."""
    try:
        devices = await ApiClient(authenticator).get_devices()
    except InvalidAuthenticationError:
        return [], {"base": "invalid_auth"}
    except DeebotError:
        _LOGGER.debug("Cannot get devices", exc_info=True)
        return [], {"base": "cannot_connect"}
    except Exception:
        _LOGGER.exception("Unexpected exception during device discovery")
        return [], {"base": "unknown"}

    found = [
        dict(info)
        for info in (*(d.api for d in devices.mqtt), *devices.not_supported)
        if is_ngiot_candidate(info)
    ]
    return found, {} if found else {"base": "no_supported_vacuums"}


class NgiotConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._auth_input: dict[str, Any] = {}
        self._devices: list[dict[str, Any]] = []
        self._authenticator: Authenticator | None = None
        self._device_id = ""

    async def _async_set_authenticator(self, user_input: Mapping[str, Any]) -> None:
        await self._async_teardown_authenticator()
        self._device_id = get_client_device_id(user_input)
        self._auth_input = {**user_input, CONF_DEVICE_ID: self._device_id}
        self._authenticator = Authenticator(
            create_rest_config(
                aiohttp_client.async_get_clientsession(self.hass),
                device_id=self._device_id,
                alpha_2_country=user_input[CONF_COUNTRY],
            ),
            user_input[CONF_USERNAME],
            md5(user_input[CONF_PASSWORD]),
        )

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
                name=f"{DOMAIN}_config_flow_authenticator_teardown",
            )

    async def _async_validate_and_find_devices(
        self,
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        assert self._authenticator is not None
        try:
            errors = await _validate_input(
                self.hass, self._auth_input, self._device_id, self._authenticator
            )
        except DeviceVerificationRequiredError:
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
        return await _find_devices(self._authenticator)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the account details."""
        errors: dict[str, str] = {}

        if user_input is not None:
            await self._async_set_authenticator(user_input)
            devices, errors = await self._async_validate_and_find_devices()
            if errors.get("base") == "device_verification_required":
                return await self.async_step_device_verification()
            if not errors:
                return await self._async_select_or_create(devices)

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

    async def _async_select_or_create(
        self, devices: list[dict[str, Any]]
    ) -> ConfigFlowResult:
        self._devices = devices
        if len(devices) == 1:
            return await self._create_entry(devices[0])
        return await self.async_step_select_device()

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
            await self._async_set_authenticator(
                {**self._auth_input, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            )
            _, errors = await self._async_validate_and_find_devices()
            if errors.get("base") == "device_verification_required":
                return await self.async_step_device_verification()
            if not errors:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates=self._auth_input
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
        """Let the user choose a vacuum."""
        if user_input is not None:
            selected = next(
                device
                for device in self._devices
                if device["did"] == user_input[CONF_DEVICE_DID]
            )
            return await self._create_entry(selected)

        return self.async_show_form(
            step_id="select_device",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_DEVICE_DID): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(
                                    value=device["did"], label=_device_label(device)
                                )
                                for device in self._devices
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
                _LOGGER.exception("Unexpected exception verifying device")
                errors["base"] = "unknown"
            else:
                devices, errors = await self._async_validate_and_find_devices()
                if not errors:
                    if self.source == SOURCE_REAUTH:
                        return self.async_update_reload_and_abort(
                            self._get_reauth_entry(), data_updates=self._auth_input
                        )
                    return await self._async_select_or_create(devices)

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
        return self.async_create_entry(
            title=_device_label(device),
            data={**self._auth_input, CONF_DEVICE_DID: device["did"]},
        )
