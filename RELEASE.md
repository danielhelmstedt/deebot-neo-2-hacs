# Release Instructions

Use this checklist when publishing a new GitHub release.

1. Update `version` in `custom_components/deebot_neo_2/manifest.json`.
2. Update `VERSION` in `custom_components/deebot_neo_2/const.py`.
3. Update README notes if supported features changed.
4. Run a syntax check:

   ```bash
   python3 -m compileall custom_components/deebot_neo_2
   ```

5. Install the integration in a test Home Assistant instance through HACS custom repository flow.
6. Verify login, device selection, start clean, return to dock, suction power, and battery updates.
7. Create a git tag matching the manifest version, for example:

   ```bash
   git tag v0.1.0
   git push origin main --tags
   ```

8. Create a GitHub release from the tag: <https://github.com/danielhelmstedt/deebot-neo-2-hacs/releases/new>
9. Include release notes with:

   - Added/changed features
   - Known limitations
   - Any required Home Assistant restart/reconfigure steps

Do not include logs, tokens, account identifiers, device IDs, local file paths, or Home Assistant `.storage` data in release notes.
