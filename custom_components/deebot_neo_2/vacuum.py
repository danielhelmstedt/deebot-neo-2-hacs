"""Vacuum platform for DEEBOT NEO 2."""

import asyncio
from collections.abc import Callable, Coroutine, Mapping
import logging
from time import monotonic
from typing import Any

from deebot_client.device import Device
from deebot_client.events import (
    BatteryEvent,
    FanSpeedEvent,
    FanSpeedLevel,
    RoomsEvent,
    StateEvent,
)
from deebot_client.events.base import Event
from deebot_client.map import Map
from deebot_client.models import CleanAction, CleanMode, State

from homeassistant.components.vacuum import (
    Segment,
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import slugify

from . import Neo2Controller
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_STATE_TO_ACTIVITY = {
    State.IDLE: VacuumActivity.IDLE,
    State.CLEANING: VacuumActivity.CLEANING,
    State.RETURNING: VacuumActivity.RETURNING,
    State.DOCKED: VacuumActivity.DOCKED,
    State.ERROR: VacuumActivity.ERROR,
    State.PAUSED: VacuumActivity.PAUSED,
}

_SUCTION_LABELS = {
    FanSpeedLevel.QUIET: "quiet mode",
    FanSpeedLevel.NORMAL: "standard",
    FanSpeedLevel.MAX: "strong",
    FanSpeedLevel.MAX_PLUS: "max",
}

_RETURN_TO_DOCK_REFRESH_INTERVAL = 30
_RETURN_TO_DOCK_FALLBACK_TIMEOUT = 20 * 60


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up DEEBOT NEO 2 vacuums."""
    controller: Neo2Controller = config_entry.runtime_data
    async_add_entities([Neo2VacuumEntity(device) for device in controller.devices])


class Neo2VacuumEntity(StateVacuumEntity):
    """DEEBOT NEO 2 vacuum entity."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_supported_features = (
        VacuumEntityFeature.START
        | VacuumEntityFeature.PAUSE
        | VacuumEntityFeature.RETURN_HOME
        | VacuumEntityFeature.STATE
        | VacuumEntityFeature.FAN_SPEED
        | VacuumEntityFeature.CLEAN_AREA
    )
    _unrecorded_attributes = frozenset({"rooms"})

    def __init__(self, device: Device) -> None:
        """Initialize the NEO 2 vacuum."""
        self._device = device
        self._subscribed_events: set[type[Event]] = set()
        self._attr_unique_id = f"{device.device_info['did']}_vacuum"
        self._attr_activity = VacuumActivity.IDLE
        self._attr_available = True
        self._attr_fan_speed_list = list(_SUCTION_LABELS.values())
        self._return_to_dock_task: asyncio.Task[None] | None = None
        self._room_event: RoomsEvent | None = None
        self._maps: dict[str, Map] = {}

    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry information."""
        info = self._device.device_info
        device_info = DeviceInfo(
            identifiers={(DOMAIN, info["did"])},
            manufacturer="Ecovacs",
            model=info.get("deviceName", "DEEBOT NEO 2"),
            model_id=info.get("class"),
            name=info.get("nick") or info.get("deviceName") or "DEEBOT NEO 2",
            serial_number=info.get("name") or info["did"],
            sw_version=self._device.fw_version,
        )
        if mac := self._device.mac:
            device_info["connections"] = {(dr.CONNECTION_NETWORK_MAC, mac)}
        return device_info

    async def async_added_to_hass(self) -> None:
        """Subscribe to device events."""
        await super().async_added_to_hass()

        async def on_state(event: StateEvent) -> None:
            self._attr_available = True
            self._attr_activity = _STATE_TO_ACTIVITY.get(
                event.state, VacuumActivity.IDLE
            )
            if self._attr_activity != VacuumActivity.RETURNING:
                self._cancel_return_to_dock_monitor()
            self.async_write_ha_state()

        async def on_fan_speed(event: FanSpeedEvent) -> None:
            self._attr_available = True
            self._attr_fan_speed = _SUCTION_LABELS.get(event.speed)
            self.async_write_ha_state()

        async def on_battery(event: BatteryEvent) -> None:
            if event.value is not None:
                self._attr_battery_level = int(event.value)
                self.async_write_ha_state()

        self._subscribe(StateEvent, on_state)
        self._subscribe(FanSpeedEvent, on_fan_speed)
        self._subscribe(BatteryEvent, on_battery)
        if map_capability := self._device.capabilities.map:

            async def on_rooms(event: RoomsEvent) -> None:
                self._room_event = event
                self.async_write_ha_state()

            async def on_map_info(event) -> None:
                self._maps = {map_info.id: map_info for map_info in event.maps}

            self._subscribe(map_capability.rooms.event, on_rooms)
            self._subscribe(map_capability.cached_info.event, on_map_info)
        self.async_schedule_update_ha_state(force_refresh=True)

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        """Return discovered room IDs."""
        rooms: dict[str, Any] = {}
        if self._room_event is None:
            return {"rooms": rooms}
        for room in self._room_event.rooms:
            name = slugify(room.name)
            if name in rooms:
                rooms[name] = (
                    [rooms[name], room.id]
                    if not isinstance(rooms[name], list)
                    else [*rooms[name], room.id]
                )
            else:
                rooms[name] = room.id
        return {"rooms": rooms}

    async def async_will_remove_from_hass(self) -> None:
        """Cancel pending q287s6 fallback work before removing the entity."""
        self._cancel_return_to_dock_monitor()
        await super().async_will_remove_from_hass()

    def _subscribe[EventT: Event](
        self,
        event_type: type[EventT],
        callback: Callable[[EventT], Coroutine[Any, Any, None]],
    ) -> None:
        self._subscribed_events.add(event_type)
        self.async_on_remove(self._device.events.subscribe(event_type, callback))

    async def async_update(self) -> None:
        """Request a best-effort refresh without marking the entity unavailable."""
        for event_type in self._subscribed_events:
            try:
                self._device.events.request_refresh(event_type)
            except Exception:
                _LOGGER.debug("q287s6 refresh failed for %s", event_type, exc_info=True)

    async def async_start(self) -> None:
        """Start an auto clean."""
        await self._execute(
            "start", self._device.capabilities.clean.action.command(CleanAction.START)
        )

    async def async_pause(self) -> None:
        """Pause the current clean."""
        await self._execute(
            "pause", self._device.capabilities.clean.action.command(CleanAction.PAUSE)
        )

    async def async_return_to_base(self, **kwargs: Any) -> None:
        """Return the vacuum to the dock."""
        await self._execute(
            "return_to_base", self._device.capabilities.charge.execute()
        )
        self._attr_activity = VacuumActivity.RETURNING
        self.async_write_ha_state()
        self._start_return_to_dock_monitor()

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        """Set suction power."""
        if fan_speed not in self._attr_fan_speed_list:
            raise HomeAssistantError(f"Unsupported suction power: {fan_speed}")
        await self._execute(
            "set_suction_power", self._device.capabilities.fan_speed.set(fan_speed)
        )

    async def async_get_segments(self) -> list[Any]:
        """Return discovered rooms as cleanable segments."""
        if self._room_event is None:
            return []
        map_info = next(
            (
                map_info
                for map_info in self._maps.values()
                if map_info.id == self._room_event.map_id
            ),
            None,
        )
        if map_info is None:
            return []
        return [
            Segment(id=f"{map_info.id}_{room.id}", name=room.name, group=map_info.name)
            for room in self._room_event.rooms
        ]

    async def async_clean_segments(self, segment_ids: list[str], **kwargs: Any) -> None:
        """Clean the selected rooms."""
        if self._room_event is None:
            return
        map_id = self._room_event.map_id
        room_ids = [
            int(segment_id.removeprefix(f"{map_id}_"))
            for segment_id in segment_ids
            if segment_id.startswith(f"{map_id}_")
        ]
        if room_ids:
            await self._execute(
                "clean_segments",
                self._device.capabilities.clean.action.area(
                    CleanMode.SPOT_AREA, room_ids, 1
                ),
            )

    async def _execute(self, action: str, command: Any) -> None:
        try:
            _LOGGER.debug("q287s6 command requested: %s", action)
            await self._device.execute_command(command)
            _LOGGER.debug("q287s6 command succeeded: %s", action)
        except Exception as err:
            _LOGGER.exception("q287s6 command failed: %s", action)
            raise HomeAssistantError(f"DEEBOT NEO 2 command failed: {action}") from err

    def _start_return_to_dock_monitor(self) -> None:
        """Poll state after return-to-dock because q287s6 may not push dock events."""
        self._cancel_return_to_dock_monitor()
        self._return_to_dock_task = asyncio.create_task(self._monitor_return_to_dock())

    def _cancel_return_to_dock_monitor(self) -> None:
        if self._return_to_dock_task is not None:
            self._return_to_dock_task.cancel()
            self._return_to_dock_task = None

    async def _monitor_return_to_dock(self) -> None:
        """Refresh q287s6 state while returning and apply a conservative fallback."""
        started = monotonic()
        try:
            while self._attr_activity == VacuumActivity.RETURNING:
                await asyncio.sleep(_RETURN_TO_DOCK_REFRESH_INTERVAL)
                await self._refresh_return_to_dock_state()
                if self._attr_activity != VacuumActivity.RETURNING:
                    return

                if monotonic() - started >= _RETURN_TO_DOCK_FALLBACK_TIMEOUT:
                    # q287s6 status can omit a final docked state even after the
                    # app charge command succeeds. Avoid leaving Home Assistant
                    # stuck on returning forever; real Ecovacs state events above
                    # still win whenever the status endpoint provides them.
                    _LOGGER.debug("q287s6 return-to-dock fallback marked docked")
                    self._attr_activity = VacuumActivity.DOCKED
                    self.async_write_ha_state()
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.debug("q287s6 return-to-dock monitor failed", exc_info=True)
        finally:
            if self._return_to_dock_task is asyncio.current_task():
                self._return_to_dock_task = None

    async def _refresh_return_to_dock_state(self) -> None:
        """Request safe q287s6 refresh events without logging device identifiers."""
        for event_type in (StateEvent, BatteryEvent):
            if event_type not in self._subscribed_events:
                continue
            try:
                self._device.events.request_refresh(event_type)
            except Exception:
                _LOGGER.debug(
                    "q287s6 return-to-dock refresh failed for %s",
                    event_type,
                    exc_info=True,
                )
