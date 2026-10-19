"""Commands for devices using the NGIOT (api-ngiot) endpoint."""

import logging
import random
import string
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from deebot_client.command import Command
from deebot_client.const import REALM, DataType
from deebot_client.event_bus import EventBus
from deebot_client.events import (
    BatteryEvent,
    FanSpeedEvent,
    FanSpeedLevel,
    FirmwareEvent,
    RoomsEvent,
    StateEvent,
    VolumeEvent,
)
from deebot_client.events.map import CachedMapInfoEvent, Map as MapInfo
from deebot_client.events.network import NetworkInfoEvent
from deebot_client.message import HandlingResult, HandlingState
from deebot_client.models import CleanAction, CleanMode, Room, State
from deebot_client.rs.map import RotationAngle

from .events import StatusEvent
from .map import decode_map_raster, parse_robot_position

if TYPE_CHECKING:
    from deebot_client.authentication import Authenticator
    from deebot_client.models import ApiDeviceInfo

_LOGGER = logging.getLogger(__name__)

FAN_SPEED_TO_FAN_MODE = {
    FanSpeedLevel.QUIET: "quiet",
    FanSpeedLevel.NORMAL: "auto",
    FanSpeedLevel.MAX: "strong",
    FanSpeedLevel.MAX_PLUS: "max",
}
FAN_MODE_TO_FAN_SPEED = {value: key for key, value in FAN_SPEED_TO_FAN_MODE.items()}
_FAN_SPEED_NAME_TO_FAN_MODE = {
    "quiet": "quiet",
    "quiet mode": "quiet",
    "normal": "auto",
    "standard": "auto",
    "auto": "auto",
    "strong": "strong",
    "max": "max",
    "max_plus": "max",
}


def _nonce(length: int = 16) -> str:
    return "".join(
        random.choice(string.ascii_letters + string.digits) for _ in range(length)
    )


def _ngiot_host(device_info: ApiDeviceInfo) -> str:
    service = device_info.get("service")
    if isinstance(service, dict) and service.get("mqs"):
        return str(service["mqs"])
    return f"api-ngiot.dc-{REALM}.ww.ecouser.net"


def state_from_status(data: dict[str, Any]) -> State | None:
    """Derive the vacuum state from a status snapshot."""
    if data.get("pauseSwitch") is True:
        return State.PAUSED

    status = data.get("status")
    work_mode = data.get("workMode")
    station_status = data.get("stationStatus")
    charge_state = data.get("chargeState")

    if status in {
        "clean",
        "cleaning",
        "smartClean",
        "areaClean",
        "spotClean",
        "singleClean",
    } or work_mode in {"auto", "clean", "cleaning", "select"}:
        return State.CLEANING
    if status in {"pause", "paused"} or work_mode in {
        "auto_pause",
        "pause",
        "paused",
    }:
        return State.PAUSED
    if (
        data.get("chargeStatus") is True
        or data.get("charging") is True
        or data.get("isCharging") is True
        or data.get("isDocked") is True
    ):
        return State.DOCKED
    if charge_state in {"charging", "docked", "charge", "charged"}:
        return State.DOCKED
    if status in {"goCharge", "goCharging", "returning"} or work_mode in {
        "return_dock",
        "goCharging",
        "returning",
    }:
        return State.RETURNING
    if status in {"charging", "docked"} or station_status in {"charging", "docked"}:
        return State.DOCKED
    if status in {"idle", "stop"} or work_mode in {"idle", "stop"}:
        return State.IDLE
    return None


class NgiotCommand(Command):
    """Command sent to the NGIOT control endpoint."""

    DATA_TYPE = DataType.JSON
    NAME = "NgiotCommand"
    APN: str | None = None

    def __init__(
        self, data: dict[str, Any] | list[Any] | None = None, apn: str | None = None
    ) -> None:
        """Initialize the command."""
        super().__init__(data or {})
        self._apn = apn or self.APN

    def _get_payload(self) -> dict[str, Any]:
        return {}

    async def _execute_api_request(
        self, authenticator: Authenticator, device_info: ApiDeviceInfo
    ) -> dict[str, Any]:
        if self._apn is None:
            return {"body": {"code": 1, "msg": "missing apn"}}
        return await self._post_endpoint(
            authenticator, device_info, self._apn, self._args
        )

    async def _post_endpoint(
        self,
        authenticator: Authenticator,
        device_info: ApiDeviceInfo,
        apn: str,
        data: dict[str, Any] | list[Any],
    ) -> dict[str, Any]:
        credentials = await authenticator.authenticate()
        config = authenticator._auth_client._config  # noqa: SLF001
        url = urljoin(f"https://{_ngiot_host(device_info)}", "api/iot/endpoint/control")
        request_id = _nonce()
        query_params = {
            "si": request_id,
            "ct": "q",
            "eid": device_info["did"],
            "et": device_info["class"],
            "er": device_info["resource"],
            "apn": apn,
            "fmt": self.DATA_TYPE.value,
        }
        payload = {
            "header": {
                "channel": "iOS",
                "m": "request",
                "pri": 1,
                "reqid": _nonce(6),
                "ts": str(int(time.time() * 1000)),
                "tzc": "UTC",
                "tzm": 0,
                "ver": "0.0.50",
            },
            "body": {"data": data},
        }
        headers = {
            "accept": "*/*",
            "authorization": f"Bearer {credentials.token}",
            "content-type": "application/octet-stream",
            "user-agent": "EcovacsHome/287541 CFNetwork Darwin",
            "x-eco-request-id": request_id,
        }
        _LOGGER.debug("NGIOT request apn=%s data=%s", apn, data)
        try:
            async with config.session.post(
                url,
                json=payload,
                params=query_params,
                headers=headers,
            ) as response:
                response.raise_for_status()
                result = await response.json(content_type=None)
        except Exception as err:
            _LOGGER.exception("NGIOT request failed apn=%s", apn)
            return {"body": {"code": 1, "msg": str(err)}}
        if result is None:
            _LOGGER.debug("NGIOT empty acknowledgement apn=%s", apn)
            return {"body": {"code": 0, "msg": "empty acknowledgement"}}
        if not isinstance(result, dict):
            _LOGGER.error(
                "NGIOT unexpected response type apn=%s type=%s",
                apn,
                type(result).__name__,
            )
            return {"body": {"code": 1, "msg": "unexpected response"}}
        _LOGGER.debug(
            "NGIOT response apn=%s code=%s", apn, result.get("body", {}).get("code")
        )
        return result

    def _handle_response(
        self, event_bus: EventBus, response: dict[str, Any]
    ) -> HandlingResult:
        if response.get("body", {}).get("code") == 0:
            return HandlingResult.success()
        return HandlingResult(HandlingState.ANALYSE)


class GetStatus(NgiotCommand):
    """Read the full status snapshot."""

    NAME = "ngiotGetStatus"
    APN = "10001"

    def __init__(self) -> None:
        """Initialize the command."""
        super().__init__(
            {
                "fields": [
                    "battery",
                    "chargeStatus",
                    "chargeState",
                    "charging",
                    "isCharging",
                    "isDocked",
                    "pauseSwitch",
                    "status",
                    "workMode",
                    "stationStatus",
                    "stationType",
                    "fanMode",
                    "waterMode",
                    "mopState",
                    "cleanTime",
                    "cleanArea",
                    "cleanCount",
                    "cleanAreaTotal",
                    "cleanCountTotal",
                    "cleanTimeTotal",
                    "volume",
                    "childLock",
                    "disturbSwitch",
                    "disturbTimeSet",
                    "breakCleanStatus",
                    "breakCleanSwitch",
                    "autoBoost",
                    "carpetCleanPrefer",
                    "safeModeSwitch",
                    "relocateSwitch",
                    "dormant",
                    "unitSet",
                    "timeZone",
                    "consumables",
                    "deviceInfo",
                    "otaData",
                    "voiceData",
                    "error",
                ]
            }
        )

    def _handle_response(
        self, event_bus: EventBus, response: dict[str, Any]
    ) -> HandlingResult:
        body = response.get("body", {})
        if body.get("code") != 0:
            return HandlingResult(HandlingState.ANALYSE)

        notify_status(event_bus, body.get("data", {}))
        return HandlingResult.success()


def notify_status(event_bus: EventBus, data: dict[str, Any]) -> None:
    """Publish the events derivable from a status snapshot."""
    event_bus.notify(StatusEvent(data))
    if (battery := data.get("battery")) is not None:
        event_bus.notify(BatteryEvent(int(battery)))
    if (speed := FAN_MODE_TO_FAN_SPEED.get(data.get("fanMode"))) is not None:
        event_bus.notify(FanSpeedEvent(speed))
    if state := state_from_status(data):
        event_bus.notify(StateEvent(state))
    if (volume := data.get("volume")) is not None:
        event_bus.notify(VolumeEvent(int(volume), None))
    if isinstance(info := data.get("deviceInfo"), dict):
        if version := info.get("fwVersion"):
            event_bus.notify(FirmwareEvent(str(version)))
        if mac := info.get("mac"):
            rssi = info.get("rssi")
            event_bus.notify(
                NetworkInfoEvent(
                    ip=str(info.get("ip", "")),
                    ssid=str(info.get("ssid", "")),
                    rssi=int(rssi) if str(rssi).lstrip("-").isdigit() else 0,
                    mac=str(mac),
                )
            )


class GetFanSpeed(NgiotCommand):
    """Read the fan speed."""

    NAME = "ngiotGetFanSpeed"
    APN = "10001"

    def __init__(self) -> None:
        """Initialize the command."""
        super().__init__({"fields": ["fanMode"]})

    def _handle_response(
        self, event_bus: EventBus, response: dict[str, Any]
    ) -> HandlingResult:
        body = response.get("body", {})
        if body.get("code") != 0:
            return HandlingResult(HandlingState.ANALYSE)

        speed = FAN_MODE_TO_FAN_SPEED.get(body.get("data", {}).get("fanMode"))
        if speed is None:
            return HandlingResult(HandlingState.ANALYSE)
        event_bus.notify(FanSpeedEvent(speed))
        return HandlingResult.success()


class SetFanSpeed(NgiotCommand):
    """Set the fan speed."""

    NAME = "ngiotSetFanSpeed"
    APN = "50011"

    def __init__(self, speed: FanSpeedLevel | str) -> None:
        """Initialize the command."""
        if isinstance(speed, FanSpeedLevel):
            fan_mode = FAN_SPEED_TO_FAN_MODE[speed]
        else:
            fan_mode = _FAN_SPEED_NAME_TO_FAN_MODE[speed]
        super().__init__({"fanMode": fan_mode})
        self._speed = FAN_MODE_TO_FAN_SPEED[fan_mode]

    def _handle_response(
        self, event_bus: EventBus, response: dict[str, Any]
    ) -> HandlingResult:
        result = super()._handle_response(event_bus, response)
        if result.state == HandlingState.SUCCESS:
            event_bus.notify(FanSpeedEvent(self._speed))
        return result


class SetVolume(NgiotCommand):
    """Set the speaker volume."""

    NAME = "ngiotSetVolume"
    APN = "50023"

    def __init__(self, volume: int) -> None:
        """Initialize the command."""
        super().__init__({"volume": volume})
        self._volume = volume

    def _handle_response(
        self, event_bus: EventBus, response: dict[str, Any]
    ) -> HandlingResult:
        result = super()._handle_response(event_bus, response)
        if result.state == HandlingState.SUCCESS:
            event_bus.notify(VolumeEvent(self._volume, None))
        return result


class SetSwitch(NgiotCommand):
    """Set an on/off setting."""

    NAME = "ngiotSetSwitch"

    def __init__(self, apn: str, field: str, enabled: bool) -> None:
        """Initialize the command."""
        super().__init__({field: enabled}, apn)

    def _handle_response(
        self, event_bus: EventBus, response: dict[str, Any]
    ) -> HandlingResult:
        result = super()._handle_response(event_bus, response)
        if result.state == HandlingState.SUCCESS:
            event_bus.request_refresh(StatusEvent)
        return result


class ResetConsumable(NgiotCommand):
    """Reset the usage counter of a consumable."""

    NAME = "ngiotResetConsumable"
    APN = "50017"

    def __init__(self, kind: str) -> None:
        """Initialize the command."""
        super().__init__({"resetConsumable": kind})

    def _handle_response(
        self, event_bus: EventBus, response: dict[str, Any]
    ) -> HandlingResult:
        result = super()._handle_response(event_bus, response)
        if result.state == HandlingState.SUCCESS:
            event_bus.request_refresh(StatusEvent)
        return result


class Clean(NgiotCommand):
    """Start, pause, resume or stop cleaning."""

    NAME = "ngiotClean"

    def __init__(self, action: CleanAction) -> None:
        """Initialize the command."""
        super().__init__({"action": action})
        self._action = action

    async def _execute_api_request(
        self, authenticator: Authenticator, device_info: ApiDeviceInfo
    ) -> dict[str, Any]:
        match self._action:
            case CleanAction.START:
                status = await self._post_endpoint(
                    authenticator,
                    device_info,
                    "10001",
                    {"fields": ["pauseSwitch", "status", "workMode"]},
                )
                data = status.get("body", {}).get("data", {})
                if data.get("pauseSwitch") is True or data.get("status") in {
                    "pause",
                    "paused",
                }:
                    return await self._post_endpoint(
                        authenticator, device_info, "40011", {"pauseSwitch": False}
                    )
                return await self._post_endpoint(
                    authenticator,
                    device_info,
                    "40001",
                    {"cleanMode": "smart", "cleanSwitch": True},
                )
            case CleanAction.RESUME:
                return await self._post_endpoint(
                    authenticator, device_info, "40011", {"pauseSwitch": False}
                )
            case CleanAction.PAUSE:
                return await self._post_endpoint(
                    authenticator, device_info, "40009", {"pauseSwitch": True}
                )
            case CleanAction.STOP:
                return await self._post_endpoint(
                    authenticator, device_info, "40015", {"chargeSwitch": False}
                )
        return {"body": {"code": 1, "msg": f"unsupported action {self._action}"}}

    def _handle_response(
        self, event_bus: EventBus, response: dict[str, Any]
    ) -> HandlingResult:
        result = super()._handle_response(event_bus, response)
        if result.state == HandlingState.SUCCESS:
            if self._action == CleanAction.PAUSE:
                event_bus.notify(StateEvent(State.PAUSED))
            elif self._action == CleanAction.STOP:
                event_bus.notify(StateEvent(State.IDLE))
            else:
                event_bus.notify(StateEvent(State.CLEANING))
        return result


class PlaySound(NgiotCommand):
    """Make the robot play its locate sound."""

    NAME = "ngiotPlaySound"
    APN = "40019"

    def __init__(self, *_args: Any) -> None:
        """Initialize the command."""
        super().__init__({"seek": True})


class CleanArea(NgiotCommand):
    """Clean the selected rooms."""

    NAME = "ngiotCleanArea"
    APN = "40007"

    def __init__(
        self, mode: CleanMode, area: list[int | float], cleanings: int = 1
    ) -> None:
        """Initialize the command."""
        super().__init__(
            {
                "cleanSwitch": True,
                "cleanMode": "area",
                "cleanValues": [int(value) for value in area],
            }
        )

    def _handle_response(
        self, event_bus: EventBus, response: dict[str, Any]
    ) -> HandlingResult:
        result = super()._handle_response(event_bus, response)
        if result.state == HandlingState.SUCCESS:
            event_bus.notify(StateEvent(State.CLEANING))
        return result


class Charge(NgiotCommand):
    """Return to the dock."""

    NAME = "ngiotCharge"
    APN = "40013"

    def __init__(self) -> None:
        """Initialize the command."""
        super().__init__({"chargeSwitch": True})

    def _handle_response(
        self, event_bus: EventBus, response: dict[str, Any]
    ) -> HandlingResult:
        result = super()._handle_response(event_bus, response)
        if result.state == HandlingState.SUCCESS:
            event_bus.notify(StateEvent(State.RETURNING))
        return result


class GetCachedMapInfo(NgiotCommand):
    """Read the saved map metadata and request the active map data."""

    NAME = "ngiotGetCachedMapInfo"
    APN = "30001"

    def __init__(self) -> None:
        """Initialize the command."""
        super().__init__({"fields": ["mapInfos"]})

    def _handle_response(
        self, event_bus: EventBus, response: dict[str, Any]
    ) -> HandlingResult:
        body = response.get("body", {})
        if body.get("code") != 0:
            return HandlingResult(HandlingState.ANALYSE)

        maps = body.get("data", {}).get("mapInfos", [])
        event_bus.notify(
            CachedMapInfoEvent(
                {
                    MapInfo(
                        id=str(map_info["mapId"]),
                        name=str(map_info.get("name") or map_info["mapId"]),
                        using=map_info.get("status") == 1,
                        built=map_info.get("saved") == 1,
                        angle=RotationAngle.from_int(map_info.get("angle", 0)),
                    )
                    for map_info in maps
                }
            )
        )
        current_map = next(
            (map_info for map_info in maps if map_info.get("status") == 1), None
        )
        if current_map is None:
            return HandlingResult.success()
        return HandlingResult(
            HandlingState.SUCCESS,
            requested_commands=[GetMapInfo(str(current_map["mapId"]))],
        )


class GetMapInfo(NgiotCommand):
    """Read the map image, position and room list."""

    NAME = "ngiotGetMapInfo"
    APN = "30001"

    def __init__(self, map_id: str, *_args: Any) -> None:
        """Initialize the command."""
        super().__init__({"mapId": map_id, "fields": ["mapData", "areas", "pos"]})
        self._map_id = map_id

    def _handle_response(
        self, event_bus: EventBus, response: dict[str, Any]
    ) -> HandlingResult:
        body = response.get("body", {})
        if body.get("code") != 0:
            return HandlingResult(HandlingState.ANALYSE)

        data = body.get("data", {})
        if raster := decode_map_raster(data.get("mapData", {})):
            event_bus.notify(raster)
        if position := parse_robot_position(data.get("pos"), self._map_id):
            event_bus.notify(position)
        rooms = [
            Room(
                name=str(area.get("name") or area["id"]),
                id=int(area["id"]),
                coordinates="",
            )
            for area in data.get("areas", [])
            if "id" in area
        ]
        if rooms:
            event_bus.notify(RoomsEvent(self._map_id, rooms))
        return HandlingResult.success()
