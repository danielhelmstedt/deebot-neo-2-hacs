"""Switch module."""

from dataclasses import dataclass
from typing import Any, override

from deebot_client.capabilities import CapabilitySet

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NgiotConfigEntry
from .entity import NgiotCapabilityEntityDescription, NgiotDescriptionEntity
from .ngiot.events import StatusEvent
from .util import get_supported_entities


@dataclass(kw_only=True, frozen=True)
class NgiotSwitchEntityDescription(
    SwitchEntityDescription,
    NgiotCapabilityEntityDescription,
):
    """Class describing switch entity."""


def _switch_description(
    key: str,
    translation_key: str,
    entity_category: EntityCategory | None = EntityCategory.CONFIG,
) -> NgiotSwitchEntityDescription:
    def capability(caps: Any) -> CapabilitySet[StatusEvent, [bool]] | None:
        return getattr(caps, "switches", {}).get(key)

    return NgiotSwitchEntityDescription(
        capability_fn=capability,
        key=key,
        translation_key=translation_key,
        entity_category=entity_category,
    )


ENTITY_DESCRIPTIONS: tuple[NgiotSwitchEntityDescription, ...] = (
    _switch_description("childLock", "child_lock"),
    _switch_description("disturbSwitch", "do_not_disturb"),
    _switch_description("breakCleanSwitch", "auto_resume"),
    _switch_description("autoBoost", "carpet_boost"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: NgiotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add entities for passed config_entry in HA."""
    async_add_entities(
        get_supported_entities(
            config_entry.runtime_data, NgiotSwitch, ENTITY_DESCRIPTIONS
        )
    )


class NgiotSwitch(
    NgiotDescriptionEntity[CapabilitySet[StatusEvent, [bool]]],
    SwitchEntity,
):
    """Switch for an on/off setting."""

    entity_description: NgiotSwitchEntityDescription

    @override
    async def async_added_to_hass(self) -> None:
        """Set up the event listeners now that hass is ready."""
        await super().async_added_to_hass()

        async def on_event(event: StatusEvent) -> None:
            if (value := event.data.get(self.entity_description.key)) is None:
                return
            self._attr_is_on = bool(value)
            self.async_write_ha_state()

        self._subscribe(self._capability.event, on_event)

    async def _set(self, enabled: bool) -> None:
        await self._device.execute_command(self._capability.set(enabled))

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the setting on."""
        await self._set(True)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the setting off."""
        await self._set(False)
