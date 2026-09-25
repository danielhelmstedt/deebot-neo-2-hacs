"""Constants for the DEEBOT NEO 2 integration."""

from homeassistant.const import Platform

DOMAIN = "deebot_neo_2"
NAME = "DEEBOT NEO 2"
VERSION = "0.1.3"

SUPPORTED_DEVICE_CLASS = "q287s6"
SUPPORTED_DEVICE_CLASSES = {SUPPORTED_DEVICE_CLASS, "eyfj07"}
SUPPORTED_MODELS = {"DEEBOT NEO 2.0", "DEEBOT NEO 2.0 PLUS"}

CONF_DEVICE_DID = "device_did"
CONF_DEVICE_NAME = "device_name"
CONF_DEVICE_RESOURCE = "device_resource"

PLATFORMS: tuple[Platform, ...] = (
    Platform.VACUUM,
    Platform.IMAGE,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
)

SUCTION_OPTIONS = ["quiet mode", "standard", "strong", "max"]
