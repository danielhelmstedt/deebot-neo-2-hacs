"""Number module."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import override

from deebot_client.capabilities import CapabilitySet
from deebot_client.events import VolumeEvent
from deebot_client.events.base import Event

from homeassistant.components.number import NumberEntity, NumberEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NgiotConfigEntry
from .entity import NgiotCapabilityEntityDescription, NgiotDescriptionEntity
from .util import get_supported_entities


@dataclass(kw_only=True, frozen=True)
class NgiotNumberEntityDescription[EventT: Event](
    NumberEntityDescription,
    NgiotCapabilityEntityDescription,
):
    """Class describing number entity."""

    native_max_value_fn: Callable[[EventT], float | int | None] = lambda _: None
    value_fn: Callable[[EventT], float | None]


ENTITY_DESCRIPTIONS: tuple[NgiotNumberEntityDescription, ...] = (
    NgiotNumberEntityDescription[VolumeEvent](
        capability_fn=lambda caps: caps.settings.volume,
        value_fn=lambda e: e.volume,
        native_max_value_fn=lambda e: e.maximum,
        key="volume",
        translation_key="volume",
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=10,
        native_step=1.0,
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
            config_entry.runtime_data, NgiotNumber, ENTITY_DESCRIPTIONS
        )
    )


class NgiotNumber[EventT: Event](
    NgiotDescriptionEntity[CapabilitySet[EventT, [int]]],
    NumberEntity,
):
    """Number entity."""

    entity_description: NgiotNumberEntityDescription

    @override
    async def async_added_to_hass(self) -> None:
        """Set up the event listeners now that hass is ready."""
        await super().async_added_to_hass()

        async def on_event(event: EventT) -> None:
            self._attr_native_value = self.entity_description.value_fn(event)
            if maximum := self.entity_description.native_max_value_fn(event):
                self._attr_native_max_value = maximum
            self.async_write_ha_state()

        self._subscribe(self._capability.event, on_event)

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Set new value."""
        await self._device.execute_command(self._capability.set(int(value)))
