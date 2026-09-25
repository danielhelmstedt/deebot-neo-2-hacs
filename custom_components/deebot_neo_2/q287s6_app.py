"""q287s6 official-app command support."""

import base64
from dataclasses import dataclass
import json
import logging
import random
import string
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from deebot_client.command import Command
from deebot_client.const import PATH_API_APPSVR_APP, REALM, REQUEST_HEADERS, DataType
from deebot_client.event_bus import EventBus
from deebot_client.events import (
    BatteryEvent,
    FanSpeedEvent,
    FanSpeedLevel,
    RoomsEvent,
    StateEvent,
)
from deebot_client.events.base import Event
from deebot_client.events.map import CachedMapInfoEvent, Map as MapInfo
from deebot_client.message import HandlingResult, HandlingState
from deebot_client.models import CleanAction, CleanMode, Room, State
from deebot_client.rs.map import RotationAngle

if TYPE_CHECKING:
    from deebot_client.authentication import Authenticator
    from deebot_client.models import ApiDeviceInfo

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Neo2MapRasterEvent(Event):
    """Decoded NEO 2 map raster."""

    map_id: str
    width: int
    height: int
    resolution: int
    pixels: bytes
    x_min: float
    y_max: float


@dataclass(frozen=True)
class Neo2RobotPositionEvent(Event):
    """Robot position reported by the NEO 2 map endpoint."""

    map_id: str
    x: float
    y: float
    angle: float


@dataclass(frozen=True)
class Neo2StatusEvent(Event):
    """Enriched NEO 2 status snapshot."""

    data: dict[str, Any]


def handle_neo2_live_payload(
    event_bus: EventBus, topic_family: str, payload: str | bytes | bytearray
) -> None:
    """Route confirmed numeric NGIOT event-family payloads."""
    if topic_family not in {"10000", "30000"}:
        return
    try:
        data = json.loads(payload)
    except TypeError, json.JSONDecodeError:
        _LOGGER.debug("Ignoring invalid NEO 2 MQTT payload for %s", topic_family)
        return
    body_data = data.get("body", {}).get("data", {})
    if not isinstance(body_data, dict):
        return

    if topic_family == "10000":
        event_bus.notify(Neo2StatusEvent(body_data))
        if (battery := body_data.get("battery")) is not None:
            event_bus.notify(BatteryEvent(int(battery)))
        if speed := _FAN_MODE_TO_FAN_SPEED.get(body_data.get("fanMode")):
            event_bus.notify(FanSpeedEvent(speed))
        if state := Q287s6EndpointStatus._state_from_status(body_data):  # noqa: SLF001
            event_bus.notify(StateEvent(state))
        else:
            # Some live pushes (e.g. app-schedule-triggered cleans) only carry a
            # partial field set that isn't enough to derive the state directly.
            # Pull an authoritative full snapshot instead of leaving it stale.
            event_bus.request_refresh(StateEvent)
        return

    map_data = body_data.get("mapData")
    if isinstance(map_data, dict) and (raster := _decode_map_raster(map_data)):
        event_bus.notify(raster)
    map_id = str(body_data.get("mapId", ""))
    if position := _parse_robot_position(body_data.get("pos"), map_id):
        event_bus.notify(position)


def _decode_map_raster(map_data: dict[str, Any]) -> Neo2MapRasterEvent | None:
    """Decode the NEO 2 raw LZ4 map raster."""
    encoded = map_data.get("map")
    width = map_data.get("width")
    height = map_data.get("height")
    compressed_length = map_data.get("lz4Len")
    if not isinstance(encoded, str) or not all(
        isinstance(value, int) and value > 0
        for value in (width, height, compressed_length)
    ):
        return None
    try:
        pixels = _decompress_lz4_block(base64.b64decode(encoded), compressed_length)
    except Exception:
        _LOGGER.exception("Unable to decode NEO 2 map raster")
        return None
    if len(pixels) != width * height:
        _LOGGER.error(
            "NEO 2 map raster has %s pixels, expected %s", len(pixels), width * height
        )
        return None
    return Neo2MapRasterEvent(
        map_id=str(map_data.get("mapId", "")),
        width=width,
        height=height,
        resolution=int(map_data.get("resolution", 1)),
        pixels=pixels,
        x_min=float(map_data.get("xMin", 0)),
        y_max=float(map_data.get("yMax", height)),
    )


def _decompress_lz4_block(data: bytes, expected_size: int) -> bytes:
    """Decompress a raw LZ4 block without a frame or size header."""
    output = bytearray()
    index = 0
    while index < len(data):
        token = data[index]
        index += 1
        literal_length = token >> 4
        if literal_length == 15:
            while True:
                extra = data[index]
                index += 1
                literal_length += extra
                if extra != 255:
                    break
        output.extend(data[index : index + literal_length])
        index += literal_length
        if index >= len(data):
            break
        offset = int.from_bytes(data[index : index + 2], "little")
        index += 2
        if offset == 0 or offset > len(output):
            raise ValueError("Invalid LZ4 match offset")
        match_length = (token & 0x0F) + 4
        if (token & 0x0F) == 15:
            while True:
                extra = data[index]
                index += 1
                match_length += extra
                if extra != 255:
                    break
        for _ in range(match_length):
            output.append(output[-offset])
    if len(output) != expected_size:
        raise ValueError(
            f"Invalid LZ4 output size {len(output)}, expected {expected_size}"
        )
    return bytes(output)


def _parse_robot_position(position: Any, map_id: str) -> Neo2RobotPositionEvent | None:
    """Parse the NEO 2 position object returned by the map endpoint."""
    if isinstance(position, dict):
        x = position.get("x")
        y = position.get("y")
        angle = position.get("a", position.get("angle", 0))
    elif isinstance(position, (list, tuple)) and len(position) >= 2:
        x, y = position[:2]
        angle = position[2] if len(position) > 2 else 0
    else:
        return None
    if not all(isinstance(value, (int, float)) for value in (x, y, angle)):
        return None
    return Neo2RobotPositionEvent(map_id, float(x), float(y), float(angle))


def _nonce(length: int = 16) -> str:
    return "".join(
        random.choice(string.ascii_letters + string.digits) for _ in range(length)
    )


def _ngiot_host(device_info: ApiDeviceInfo) -> str:
    service = device_info.get("service")
    if isinstance(service, dict) and service.get("mqs"):
        return str(service["mqs"])
    return f"api-ngiot.dc-{REALM}.ww.ecouser.net"


class Q287s6RobotControlClean(Command):
    """q287s6 clean action using the official Ecovacs app RobotControl envelope."""

    DATA_TYPE = DataType.JSON
    NAME = "Q287s6RobotControlClean"

    def __init__(self, action: CleanAction) -> None:
        """Initialize a RobotControl clean command."""
        super().__init__({"action": action})
        self._action = action

    def _get_payload(self) -> dict[str, Any]:
        return {}

    async def _execute_api_request(
        self, authenticator: Authenticator, device_info: ApiDeviceInfo
    ) -> dict[str, Any]:
        if self._action in (
            CleanAction.START,
            CleanAction.STOP,
            CleanAction.PAUSE,
            CleanAction.RESUME,
        ):
            return await self._post_robot_control(authenticator, device_info)
        return {
            "ret": "fail",
            "error": f"Unsupported q287s6 clean action: {self._action}",
        }

    async def _post_robot_control(
        self, authenticator: Authenticator, device_info: ApiDeviceInfo
    ) -> dict[str, Any]:
        clean_data = {
            "act": self._action.xml_value,
            "type": CleanMode.AUTO.value,
            "tri": "app",
        }
        payload = {
            "todo": "RobotControl",
            "did": device_info["did"],
            "mid": device_info["class"],
            "res": device_info["resource"],
            "app": {"id": "ecovacs", "ts": int(time.time() * 1000)},
            "data": {
                "ctl": {
                    "Clean": {
                        "cmd": "Clean",
                        "type": "p2p",
                        "did": device_info["did"],
                        "mid": device_info["class"],
                        "res": device_info["resource"],
                        "all": False,
                        "data": clean_data,
                    }
                }
            },
        }
        _LOGGER.debug("q287s6 RobotControl Clean request: %s", clean_data)
        try:
            response = await authenticator.post_authenticated(
                PATH_API_APPSVR_APP,
                payload,
                headers=REQUEST_HEADERS,
            )
        except Exception as err:
            _LOGGER.exception("q287s6 RobotControl Clean request failed")
            return {"ret": "fail", "error": str(err)}
        _LOGGER.debug(
            "q287s6 RobotControl Clean response received ret=%s code=%s",
            response.get("ret"),
            response.get("code"),
        )
        return response

    def _handle_response(
        self, event_bus: EventBus, response: dict[str, Any]
    ) -> HandlingResult:
        if response.get("ret") == "ok" and response.get("code") == 0:
            clean = response.get("data", {}).get("Clean", {})
            if clean.get("ret") == "ok":
                if self._action in (CleanAction.STOP, CleanAction.PAUSE):
                    event_bus.notify(StateEvent(State.PAUSED))
                else:
                    event_bus.notify(StateEvent(State.CLEANING))
                return HandlingResult.success()
        if response.get("body", {}).get("code") == 0:
            return HandlingResult.success()
        return HandlingResult(HandlingState.ANALYSE)


class Q287s6EndpointCommand(Command):
    """q287s6 field-style api-ngiot endpoint command."""

    DATA_TYPE = DataType.JSON
    NAME = "Q287s6EndpointCommand"
    APN: str | None = None

    def __init__(
        self, data: dict[str, Any] | list[Any] | None = None, apn: str | None = None
    ) -> None:
        """Initialize an endpoint command."""
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
        auth_client = authenticator._auth_client  # noqa: SLF001
        config = auth_client._config  # noqa: SLF001
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
        _LOGGER.debug("q287s6 endpoint request apn=%s data=%s", apn, data)
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
            _LOGGER.exception("q287s6 endpoint request failed apn=%s", apn)
            return {"body": {"code": 1, "msg": str(err)}}
        if result is None:
            _LOGGER.debug(
                "q287s6 endpoint returned an empty acknowledgement apn=%s", apn
            )
            return {"body": {"code": 0, "msg": "empty acknowledgement"}}
        if not isinstance(result, dict):
            _LOGGER.error(
                "q287s6 endpoint returned an unexpected response type apn=%s type=%s",
                apn,
                type(result).__name__,
            )
            return {"body": {"code": 1, "msg": "unexpected response"}}
        body = result.get("body", {})
        _LOGGER.debug("q287s6 endpoint response apn=%s code=%s", apn, body.get("code"))
        return result

    def _handle_response(
        self, event_bus: EventBus, response: dict[str, Any]
    ) -> HandlingResult:
        body = response.get("body", {})
        if body.get("code") == 0:
            return HandlingResult.success()
        return HandlingResult(HandlingState.ANALYSE)


class Q287s6MapIndex(Q287s6EndpointCommand):
    """Read NEO 2 saved map metadata and request its active map data."""

    NAME = "Q287s6MapIndex"
    APN = "30001"

    def __init__(self) -> None:
        """Initialize a map index query."""
        super().__init__({"fields": ["mapInfos"]})

    def _handle_response(
        self, event_bus: EventBus, response: dict[str, Any]
    ) -> HandlingResult:
        body = response.get("body", {})
        if body.get("code") != 0:
            return HandlingResult(HandlingState.ANALYSE)

        maps = body.get("data", {}).get("mapInfos", [])
        map_infos = {
            MapInfo(
                id=str(map_info["mapId"]),
                name=str(map_info.get("name") or map_info["mapId"]),
                using=map_info.get("status") == 1,
                built=map_info.get("saved") == 1,
                angle=RotationAngle.from_int(map_info.get("angle", 0)),
            )
            for map_info in maps
        }
        event_bus.notify(CachedMapInfoEvent(map_infos))
        current_map = next(
            (map_info for map_info in maps if map_info.get("status") == 1), None
        )
        if current_map is None:
            return HandlingResult.success()
        return HandlingResult(
            HandlingState.SUCCESS,
            requested_commands=[Q287s6MapData(str(current_map["mapId"]))],
        )


class Q287s6MapData(Q287s6EndpointCommand):
    """Read the NEO 2 map image and room list."""

    NAME = "Q287s6MapData"
    APN = "30001"

    def __init__(self, map_id: str, *_args: Any) -> None:
        """Initialize a map data query."""
        super().__init__({"mapId": map_id, "fields": ["mapData", "areas", "pos"]})
        self._map_id = map_id

    def _handle_response(
        self, event_bus: EventBus, response: dict[str, Any]
    ) -> HandlingResult:
        body = response.get("body", {})
        if body.get("code") != 0:
            return HandlingResult(HandlingState.ANALYSE)

        data = body.get("data", {})
        map_data = data.get("mapData", {})
        if raster := _decode_map_raster(map_data):
            event_bus.notify(raster)
        if position := _parse_robot_position(data.get("pos"), self._map_id):
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


class Q287s6EndpointAreaClean(Q287s6EndpointCommand):
    """Clean selected NEO 2 rooms using the captured app payload."""

    NAME = "Q287s6EndpointAreaClean"
    APN = "40007"

    def __init__(
        self, mode: CleanMode, area: list[int | float], cleanings: int = 1
    ) -> None:
        """Initialize an area-clean command."""
        super().__init__(
            {
                "cleanSwitch": True,
                "cleanMode": "area",
                "cleanValues": [int(value) for value in area],
            }
        )
        self._area_data = self._args

    async def _execute_api_request(
        self, authenticator: Authenticator, device_info: ApiDeviceInfo
    ) -> dict[str, Any]:
        """Select and start the area clean using the NEO 2 room command."""
        return await self._post_endpoint(
            authenticator, device_info, "40007", self._area_data
        )

    def _handle_response(
        self, event_bus: EventBus, response: dict[str, Any]
    ) -> HandlingResult:
        """Update the vacuum state when area cleaning starts."""
        result = super()._handle_response(event_bus, response)
        if result.state == HandlingState.SUCCESS:
            event_bus.notify(StateEvent(State.CLEANING))
        return result


_FAN_SPEED_TO_FAN_MODE = {
    FanSpeedLevel.QUIET: "quiet",
    FanSpeedLevel.NORMAL: "auto",
    FanSpeedLevel.MAX: "strong",
    FanSpeedLevel.MAX_PLUS: "max",
}
_FAN_MODE_TO_FAN_SPEED = {value: key for key, value in _FAN_SPEED_TO_FAN_MODE.items()}
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


class Q287s6EndpointFanSpeed(Q287s6EndpointCommand):
    """Set q287s6 suction using the official app fanMode endpoint."""

    NAME = "Q287s6EndpointFanSpeed"
    APN = "50011"

    def __init__(self, speed: FanSpeedLevel | str) -> None:
        """Initialize a fan-speed command."""
        if isinstance(speed, FanSpeedLevel):
            fan_mode = _FAN_SPEED_TO_FAN_MODE[speed]
        else:
            fan_mode = _FAN_SPEED_NAME_TO_FAN_MODE[speed]
        super().__init__({"fanMode": fan_mode})
        self._speed = _FAN_MODE_TO_FAN_SPEED[fan_mode]

    def _handle_response(
        self, event_bus: EventBus, response: dict[str, Any]
    ) -> HandlingResult:
        result = super()._handle_response(event_bus, response)
        if result.state == HandlingState.SUCCESS:
            event_bus.notify(FanSpeedEvent(self._speed))
        return result


class Q287s6EndpointFanSpeedStatus(Q287s6EndpointCommand):
    """Read q287s6 suction from the official app status endpoint."""

    NAME = "Q287s6EndpointFanSpeedStatus"
    APN = "10001"

    def __init__(self) -> None:
        """Initialize a fan-speed status query."""
        super().__init__({"fields": ["fanMode"]})

    def _handle_response(
        self, event_bus: EventBus, response: dict[str, Any]
    ) -> HandlingResult:
        body = response.get("body", {})
        if body.get("code") != 0:
            return HandlingResult(HandlingState.ANALYSE)

        fan_mode = body.get("data", {}).get("fanMode")
        speed = _FAN_MODE_TO_FAN_SPEED.get(fan_mode)
        if speed is None:
            return HandlingResult(HandlingState.ANALYSE)
        event_bus.notify(FanSpeedEvent(speed))
        return HandlingResult.success()


class Q287s6EndpointClean(Q287s6EndpointCommand):
    """q287s6 clean actions using official app endpoint commands."""

    NAME = "Q287s6EndpointClean"

    def __init__(self, action: CleanAction) -> None:
        """Initialize a clean command."""
        super().__init__({"action": action})
        self._action = action

    async def _execute_api_request(
        self, authenticator: Authenticator, device_info: ApiDeviceInfo
    ) -> dict[str, Any]:
        if self._action == CleanAction.START:
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

        if self._action == CleanAction.RESUME:
            return await self._post_endpoint(
                authenticator, device_info, "40011", {"pauseSwitch": False}
            )

        if self._action == CleanAction.PAUSE:
            return await self._post_endpoint(
                authenticator, device_info, "40009", {"pauseSwitch": True}
            )

        return await Q287s6RobotControlClean(self._action)._execute_api_request(  # noqa: SLF001
            authenticator, device_info
        )

    def _handle_response(
        self, event_bus: EventBus, response: dict[str, Any]
    ) -> HandlingResult:
        result = super()._handle_response(event_bus, response)
        if result.state == HandlingState.SUCCESS:
            if self._action == CleanAction.PAUSE:
                event_bus.notify(StateEvent(State.PAUSED))
            elif self._action in (CleanAction.START, CleanAction.RESUME):
                event_bus.notify(StateEvent(State.CLEANING))
        return result


class Q287s6EndpointStatus(Q287s6EndpointCommand):
    """q287s6 app-style status reader."""

    NAME = "Q287s6EndpointStatus"
    APN = "10001"

    def __init__(self) -> None:
        """Initialize a status query."""
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

        data = body.get("data", {})
        event_bus.notify(Neo2StatusEvent(data))
        battery = data.get("battery")
        if battery is not None:
            event_bus.notify(BatteryEvent(int(battery)))

        speed = _FAN_MODE_TO_FAN_SPEED.get(data.get("fanMode"))
        if speed is not None:
            event_bus.notify(FanSpeedEvent(speed))

        state = self._state_from_status(data)
        if state is not None:
            event_bus.notify(StateEvent(state))
            return HandlingResult.success()
        return HandlingResult.success()

    @staticmethod
    def _state_from_status(data: dict[str, Any]) -> State | None:
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
        } or work_mode in {
            "auto",
            "clean",
            "cleaning",
            "select",
        }:
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


class Q287s6EndpointCharge(Q287s6EndpointCommand):
    """Return q287s6 to dock using official app chargeSwitch endpoint."""

    NAME = "Q287s6EndpointCharge"
    APN = "40013"

    def __init__(self) -> None:
        """Initialize a charge command."""
        super().__init__({"chargeSwitch": True})

    def _handle_response(
        self, event_bus: EventBus, response: dict[str, Any]
    ) -> HandlingResult:
        result = super()._handle_response(event_bus, response)
        if result.state == HandlingState.SUCCESS:
            event_bus.notify(StateEvent(State.RETURNING))
        return result
