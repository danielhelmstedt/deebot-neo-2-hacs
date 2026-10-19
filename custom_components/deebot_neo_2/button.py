"""Button module."""

from dataclasses import dataclass
from typing import override

from deebot_client.capabilities import CapabilityExecute

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NgiotConfigEntry
from .entity import NgiotCapabilityEntityDescription, NgiotDescriptionEntity
from .ngiot.commands import ResetConsumable
from .util import get_supported_entities


@dataclass(kw_only=True, frozen=True)
class NgiotButtonEntityDescription(
    ButtonEntityDescription,
    NgiotCapabilityEntityDescription,
):
    """Class describing button entity."""

    consumable: str


def _reset_description(
    consumable: str, translation_key: str
) -> NgiotButtonEntityDescription:
    return NgiotButtonEntityDescription(
        capability_fn=lambda caps: getattr(caps, "reset_consumable", None),
        key=f"reset_{consumable}",
        translation_key=translation_key,
        entity_category=EntityCategory.DIAGNOSTIC,
        consumable=consumable,
    )


ENTITY_DESCRIPTIONS: tuple[NgiotButtonEntityDescription, ...] = (
    _reset_description("sideBrush", "reset_side_brush"),
    _reset_description("rollBrush", "reset_roll_brush"),
    _reset_description("filter", "reset_filter"),
    _reset_description("unitCare", "reset_unit_care"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: NgiotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add entities for passed config_entry in HA."""
    async_add_entities(
        get_supported_entities(
            config_entry.runtime_data, NgiotButton, ENTITY_DESCRIPTIONS
        )
    )


class NgiotButton(
    NgiotDescriptionEntity[CapabilityExecute[ResetConsumable]],
    ButtonEntity,
):
    """Button that resets a consumable counter."""

    entity_description: NgiotButtonEntityDescription

    @override
    async def async_press(self) -> None:
        """Press the button."""
        await self._device.execute_command(
            self._capability.execute(self.entity_description.consumable)
        )
