"""Device handling for the NGIOT endpoint."""

import asyncio
import json
import logging

from deebot_client.device import Device
from deebot_client.events import StateEvent
from deebot_client.message import MessagePayloadType
from deebot_client.mqtt_client import MqttClient

from .commands import GetStatus, notify_status, state_from_status
from .map import decode_map_raster, parse_robot_position

_LOGGER = logging.getLogger(__name__)

STATUS_TOPIC_FAMILY = "10000"
MAP_TOPIC_FAMILY = "30000"
_STATUS_PRIME_ATTEMPTS = 5


class NgiotDevice(Device):
    """Device that handles the numeric NGIOT live message families."""

    async def initialize(self, client: MqttClient) -> None:
        """Initialize the device and prime it with a status snapshot."""
        await super().initialize(client)

        # The first request often races the MQTT subscription and returns nothing
        for _ in range(_STATUS_PRIME_ATTEMPTS):
            response = await self.execute_command(GetStatus())
            if response.get("body", {}).get("data"):
                return
            await asyncio.sleep(1)
        _LOGGER.warning("Could not fetch an initial status snapshot")

    def _handle_message(
        self, message_name: str, message_data: MessagePayloadType
    ) -> None:
        super()._handle_message(message_name, message_data)
        if message_name in (STATUS_TOPIC_FAMILY, MAP_TOPIC_FAMILY):
            self._handle_live_payload(message_name, message_data)

    def _handle_live_payload(
        self, topic_family: str, payload: MessagePayloadType
    ) -> None:
        try:
            data = json.loads(payload)
        except TypeError, json.JSONDecodeError:
            _LOGGER.debug("Ignoring invalid live payload for %s", topic_family)
            return
        body_data = data.get("body", {}).get("data", {})
        if not isinstance(body_data, dict):
            return

        if topic_family == STATUS_TOPIC_FAMILY:
            notify_status(self.events, body_data)
            if state_from_status(body_data) is None:
                # Partial pushes (e.g. scheduled cleans) can't tell the state
                self.events.request_refresh(StateEvent)
            return

        if isinstance(map_data := body_data.get("mapData"), dict) and (
            raster := decode_map_raster(map_data)
        ):
            self.events.notify(raster)
        map_id = str(body_data.get("mapId", ""))
        if position := parse_robot_position(body_data.get("pos"), map_id):
            self.events.notify(position)
