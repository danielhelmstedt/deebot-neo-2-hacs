"""Capabilities of devices using the NGIOT endpoint."""

from dataclasses import dataclass

from deebot_client.capabilities import (
    Capabilities,
    CapabilityClean,
    CapabilityCleanAction,
    CapabilityEvent,
    CapabilityExecute,
    CapabilityLifeSpan,
    CapabilityMap,
    CapabilitySet,
    CapabilitySettings,
    CapabilitySetTypes,
    CapabilityStats,
    DeviceType,
)
from deebot_client.command import Command
from deebot_client.const import DataType
from deebot_client.events import (
    AvailabilityEvent,
    BatteryEvent,
    ErrorEvent,
    FanSpeedEvent,
    FanSpeedLevel,
    LifeSpan,
    LifeSpanEvent,
    NetworkInfoEvent,
    ReportStatsEvent,
    RoomsEvent,
    StateEvent,
    StatsEvent,
    TotalStatsEvent,
    VolumeEvent,
)
from deebot_client.events.map import (
    CachedMapInfoEvent,
    MapChangedEvent,
    MapTraceEvent,
    PositionsEvent,
)
from deebot_client.models import StaticDeviceInfo

from .commands import (
    Charge,
    Clean,
    CleanArea,
    GetCachedMapInfo,
    GetFanSpeed,
    GetMapInfo,
    GetStatus,
    PlaySound,
    ResetConsumable,
    SetFanSpeed,
    SetSwitch,
    SetVolume,
)
from .events import StatusEvent


@dataclass(frozen=True, kw_only=True)
class NgiotCapabilities(Capabilities):
    """Capabilities extended with the NGIOT status snapshot."""

    status: CapabilityEvent[StatusEvent]
    reset_consumable: CapabilityExecute[ResetConsumable]
    switches: dict[str, CapabilitySet[StatusEvent, [bool]]]


def status_capability(
    capabilities: Capabilities,
) -> CapabilityEvent[StatusEvent] | None:
    """Return the status capability if the device has one."""
    return getattr(capabilities, "status", None)


def _reset_life_span(life_span: LifeSpan) -> Command:
    raise NotImplementedError("Resetting life spans is not supported")


SWITCH_TOPICS = {
    "childLock": "50038",
    "disturbSwitch": "50019",
    "breakCleanSwitch": "50025",
    "autoBoost": "50036",
}


def _switch_capability(field: str, apn: str) -> CapabilitySet[StatusEvent, [bool]]:
    return CapabilitySet(
        StatusEvent,
        [GetStatus()],
        lambda enabled: SetSwitch(apn, field, enabled),
    )


def get_static_device_info() -> StaticDeviceInfo:
    """Return the static device info for NGIOT vacuums."""
    capabilities = NgiotCapabilities(
        DeviceType.VACUUM,
        availability=CapabilityEvent(AvailabilityEvent, []),
        battery=CapabilityEvent(BatteryEvent, [GetStatus()]),
        charge=CapabilityExecute(Charge),
        clean=CapabilityClean(
            action=CapabilityCleanAction(command=Clean, area=CleanArea),
        ),
        custom=None,  # type: ignore[arg-type]
        error=CapabilityEvent(ErrorEvent, []),
        fan_speed=CapabilitySetTypes(
            event=FanSpeedEvent,
            get=[GetFanSpeed()],
            set=SetFanSpeed,
            types=(
                FanSpeedLevel.QUIET,
                FanSpeedLevel.NORMAL,
                FanSpeedLevel.MAX,
                FanSpeedLevel.MAX_PLUS,
            ),
        ),
        life_span=CapabilityLifeSpan(
            event=LifeSpanEvent, get=[], types=(), reset=_reset_life_span
        ),
        map=CapabilityMap(
            cached_info=CapabilityEvent(CachedMapInfoEvent, [GetCachedMapInfo()]),
            changed=CapabilityEvent(MapChangedEvent, []),
            info=CapabilityExecute(GetMapInfo),
            position=CapabilityEvent(PositionsEvent, []),
            rooms=CapabilityEvent(RoomsEvent, [GetCachedMapInfo()]),
            set=CapabilityExecute(GetMapInfo),
            trace=CapabilityEvent(MapTraceEvent, []),
        ),
        network=CapabilityEvent(NetworkInfoEvent, []),
        play_sound=CapabilityExecute(PlaySound),
        settings=CapabilitySettings(
            volume=CapabilitySet(VolumeEvent, [GetStatus()], SetVolume)
        ),
        state=CapabilityEvent(StateEvent, [GetStatus()]),
        stats=CapabilityStats(
            clean=CapabilityEvent(StatsEvent, []),
            report=CapabilityEvent(ReportStatsEvent, []),
            total=CapabilityEvent(TotalStatsEvent, []),
        ),
        status=CapabilityEvent(StatusEvent, [GetStatus()]),
        reset_consumable=CapabilityExecute(ResetConsumable),
        switches={
            field: _switch_capability(field, apn)
            for field, apn in SWITCH_TOPICS.items()
        },
    )
    return StaticDeviceInfo(DataType.JSON, capabilities)
