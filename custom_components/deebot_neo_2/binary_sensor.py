"""Binary sensor module."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import override

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NgiotConfigEntry
from .entity import NgiotCapabilityEntityDescription, NgiotDescriptionEntity
from .ngiot.events import StatusEvent
from .ngiot.hardware import status_capability
from .util import get_supported_entities


@dataclass(kw_only=True, frozen=True)
class NgiotBinarySensorEntityDescription(
    BinarySensorEntityDescription,
    NgiotCapabilityEntityDescription,
):
    """Class describing binary sensor entity."""

    value_fn: Callable[[StatusEvent], bool | None]


def _flag(key: str) -> Callable[[StatusEvent], bool | None]:
    def value(event: StatusEvent) -> bool | None:
        return None if (raw := event.data.get(key)) is None else bool(raw)

    return value


ENTITY_DESCRIPTIONS: tuple[NgiotBinarySensorEntityDescription, ...] = (
    NgiotBinarySensorEntityDescription(
        capability_fn=status_capability,
        value_fn=_flag("dormant"),
        key="dormant",
        translation_key="dormant",
    ),
    NgiotBinarySensorEntityDescription(
        capability_fn=status_capability,
        value_fn=_flag("relocateSwitch"),
        key="relocateSwitch",
        translation_key="relocation_mode",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: NgiotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add entities for passed config_entry in HA."""
    async_add_entities(
        get_supported_entities(
            config_entry.runtime_data, NgiotBinarySensor, ENTITY_DESCRIPTIONS
        )
    )


class NgiotBinarySensor(
    NgiotDescriptionEntity,
    BinarySensorEntity,
):
    """Binary sensor backed by the status snapshot."""

    entity_description: NgiotBinarySensorEntityDescription

    @override
    async def async_added_to_hass(self) -> None:
        """Set up the event listeners now that hass is ready."""
        await super().async_added_to_hass()

        async def on_event(event: StatusEvent) -> None:
            if (value := self.entity_description.value_fn(event)) is not None:
                self._attr_is_on = value
                self.async_write_ha_state()

        self._subscribe(self._capability.event, on_event)
