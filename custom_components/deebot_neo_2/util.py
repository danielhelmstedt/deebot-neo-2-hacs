"""Util functions."""

from collections.abc import Mapping
from enum import Enum
import random
import string
from typing import TYPE_CHECKING, Any

from homeassistant.const import CONF_DEVICE_ID
from homeassistant.core import callback

from .entity import (
    NgiotCapabilityEntityDescription,
    NgiotDescriptionEntity,
    NgiotEntity,
)

if TYPE_CHECKING:
    from .controller import NgiotController


def get_client_device_id(config: Mapping[str, Any]) -> str:
    """Get the stored client device id or generate a new one."""
    if device_id := config.get(CONF_DEVICE_ID):
        return str(device_id)
    return "".join(
        random.choice(string.ascii_uppercase + string.digits) for _ in range(8)
    )


def get_supported_entities(
    controller: NgiotController,
    entity_class: type[NgiotDescriptionEntity],
    descriptions: tuple[NgiotCapabilityEntityDescription, ...],
) -> list[NgiotEntity]:
    """Return all supported entities for all devices."""
    return [
        entity_class(device, capability, description)
        for device in controller.devices
        for description in descriptions
        if (capability := description.capability_fn(device.capabilities))
    ]


@callback
def get_name_key(enum: Enum) -> str:
    """Return the lower case name of the enum."""
    return enum.name.lower()
