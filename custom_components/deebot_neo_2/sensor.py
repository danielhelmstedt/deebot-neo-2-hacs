"""Sensor module."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, override

from deebot_client.capabilities import CapabilityEvent
from deebot_client.events import BatteryEvent
from deebot_client.events.base import Event

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfArea, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import NgiotConfigEntry
from .entity import NgiotCapabilityEntityDescription, NgiotDescriptionEntity
from .ngiot.events import StatusEvent
from .ngiot.hardware import status_capability
from .util import get_supported_entities


@dataclass(kw_only=True, frozen=True)
class NgiotSensorEntityDescription[EventT: Event](
    SensorEntityDescription,
    NgiotCapabilityEntityDescription,
):
    """Class describing sensor entity."""

    value_fn: Callable[[EventT], StateType]
    attributes_fn: Callable[[EventT], Mapping[str, Any] | None] | None = None


def _status_value(key: str) -> Callable[[StatusEvent], StateType]:
    def value(event: StatusEvent) -> StateType:
        return event.data.get(key)

    return value


def _consumable(
    kind: str,
) -> tuple[
    Callable[[StatusEvent], StateType],
    Callable[[StatusEvent], Mapping[str, Any] | None],
]:
    def find(event: StatusEvent) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in event.data.get("consumables", [])
                if item.get("type") == kind and item.get("left") is not None
            ),
            None,
        )

    def value(event: StatusEvent) -> StateType:
        return item["left"] if (item := find(event)) else None

    def attributes(event: StatusEvent) -> Mapping[str, Any] | None:
        return {"total": item.get("total")} if (item := find(event)) else None

    return value, attributes


def _status_description(
    key: str,
    *,
    translation_key: str,
    unit: str | None = None,
    device_class: SensorDeviceClass | None = None,
    state_class: SensorStateClass | None = None,
) -> NgiotSensorEntityDescription[StatusEvent]:
    return NgiotSensorEntityDescription[StatusEvent](
        capability_fn=status_capability,
        value_fn=_status_value(key),
        key=key,
        translation_key=translation_key,
        native_unit_of_measurement=unit,
        device_class=device_class,
        state_class=state_class,
    )


def _consumable_description(
    kind: str, translation_key: str
) -> NgiotSensorEntityDescription[StatusEvent]:
    value_fn, attributes_fn = _consumable(kind)
    return NgiotSensorEntityDescription[StatusEvent](
        capability_fn=status_capability,
        value_fn=value_fn,
        attributes_fn=attributes_fn,
        key=f"consumable_{kind}",
        translation_key=translation_key,
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
    )


ENTITY_DESCRIPTIONS: tuple[NgiotSensorEntityDescription, ...] = (
    NgiotSensorEntityDescription[BatteryEvent](
        capability_fn=lambda caps: caps.battery,
        value_fn=lambda e: e.value,
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    _status_description(
        "cleanArea",
        translation_key="stats_area",
        unit=UnitOfArea.SQUARE_METERS,
        device_class=SensorDeviceClass.AREA,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    _status_description(
        "cleanTime",
        translation_key="stats_time",
        unit=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    _status_description(
        "cleanCount",
        translation_key="stats_cleanings",
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    _status_description(
        "cleanAreaTotal",
        translation_key="total_stats_area",
        unit=UnitOfArea.SQUARE_METERS,
        device_class=SensorDeviceClass.AREA,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    _status_description(
        "cleanCountTotal",
        translation_key="total_stats_cleanings",
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    _status_description(
        "cleanTimeTotal",
        translation_key="total_stats_time",
        unit=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    _status_description("workMode", translation_key="work_mode"),
    _status_description("waterMode", translation_key="water_mode"),
    _status_description("mopState", translation_key="mop_state"),
    _status_description("unitSet", translation_key="unit_set"),
    _consumable_description("sideBrush", "lifespan_side_brush"),
    _consumable_description("rollBrush", "lifespan_roll_brush"),
    _consumable_description("filter", "lifespan_filter"),
    _consumable_description("unitCare", "lifespan_unit_care"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: NgiotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add entities for passed config_entry in HA."""
    async_add_entities(
        get_supported_entities(
            config_entry.runtime_data, NgiotSensor, ENTITY_DESCRIPTIONS
        )
    )


class NgiotSensor[EventT: Event](
    NgiotDescriptionEntity[CapabilityEvent[EventT]],
    SensorEntity,
):
    """Sensor."""

    entity_description: NgiotSensorEntityDescription

    @override
    async def async_added_to_hass(self) -> None:
        """Set up the event listeners now that hass is ready."""
        await super().async_added_to_hass()

        async def on_event(event: EventT) -> None:
            if (value := self.entity_description.value_fn(event)) is None:
                return
            self._attr_native_value = value
            if self.entity_description.attributes_fn:
                self._attr_extra_state_attributes = (
                    self.entity_description.attributes_fn(event)
                )
            self.async_write_ha_state()

        self._subscribe(self._capability.event, on_event)
