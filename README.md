# Ecovacs NGIOT

Unofficial Home Assistant custom integration for Ecovacs vacuums using the NGIOT endpoint, including DEEBOT NEO 2.0 and DEEBOT NEO 2.0 PLUS.

This is a community integration. It is not affiliated with, endorsed by, or supported by Ecovacs or Home Assistant.

## What Works

- Add supported vacuums from the Home Assistant UI using an Ecovacs account.
- Start, pause, resume, stop, and locate the vacuum.
- Send the vacuum back to its dock.
- See the vacuum state, including cleaning, paused, returning, docked, idle, and error states.
- Set the fan speed.
- View the map and robot position, and clean selected rooms on the currently active map when map data is available.
- View battery, cleaning statistics, consumable life, and other status sensors when the vacuum reports those values.
- Adjust supported settings: child lock, do-not-disturb, auto resume, carpet boost, and speaker volume.
- Reset the filter, main brush, side brush, and unit-care counters.
- View dormant and relocation-mode status.

## Limitations

- Room cleaning is limited to rooms on the currently active map.
- Some sensors may show as unknown until the vacuum reports their values; available status fields can vary by device or firmware.

These are implementation limits, not a list of known broken features. The integration only supports the device classes listed below.

## Installation With HACS

1. Install HACS
2. Go to HACS -> three dots -> Custom repositories
3. Add this GitHub repo URL: `https://github.com/danielhelmstedt/deebot-neo-2-hacs`
4. Select category: Integration
5. Install "Ecovacs NGIOT"
6. Restart Home Assistant
7. Go to Settings -> Devices & services -> Add integration
8. Search for "Ecovacs NGIOT"
9. Log in with Ecovacs account details
10. Select the vacuum

## Supported Devices

This integration is for Ecovacs devices with class `eyfj07` and `q287s6`, including:

- DEEBOT NEO 2.0
- DEEBOT NEO 2.0 PLUS

Other Ecovacs models are not supported by this integration. Use Home Assistant's built-in Ecovacs integration for other models.

## Debug Logging

Add this to `configuration.yaml`, then restart Home Assistant:

```yaml
logger:
  default: info
  logs:
    custom_components.deebot_neo_2: debug
```

When opening an issue, include the relevant `custom_components.deebot_neo_2` log lines. Remove account IDs, tokens, email addresses, and home/network details before posting logs.

Issues: <https://github.com/danielhelmstedt/deebot-neo-2-hacs/issues>

## Troubleshooting

### Vacuum Shows Unavailable

This integration avoids marking the vacuum unavailable just because unsupported NGIOT endpoints return `None`. If it still shows unavailable:

- Restart Home Assistant after installing or updating the integration.
- Confirm the vacuum is online in the Ecovacs app.
- Confirm your Ecovacs region/country matches the account.
- Enable debug logs and check for login, MQTT, or endpoint errors.

### Start, Pause, Or Return-to-Dock Does Not Work

Start, pause, and return-to-dock use `NGIOT` official-app endpoints. If a command does not reach the vacuum:

- Confirm the robot is awake and reachable in the Ecovacs app.
- Enable debug logs.
- Open an issue with the sanitized debug log around the command.

### Region Or Login Issues

Ecovacs accounts are region-sensitive.

- Choose the same country/region you use in the Ecovacs app.
- Your Ecovacs email may be case sensitive. Enter the same email address you use in the Ecovacs Home app.
- Verify your email and password by logging into the Ecovacs Home app.
- If your Ecovacs app account uses a social login, create or set an Ecovacs password first if the app allows it.

### How To Enable Debug Logs

Use the logger configuration shown in the Debug Logging section. After reproducing the issue, download Home Assistant logs and include only the relevant sanitized lines in your GitHub issue.

## Privacy

This integration stores your Ecovacs account details in Home Assistant's config entry storage, similar to other cloud integrations. Do not post `.storage` files, tokens, passwords, or full logs in GitHub issues.


## License

MIT. See [LICENSE](LICENSE).
