#!/usr/bin/env bash
# Runs once when the codespace is created (onCreateCommand), so Codespaces prebuilds can cache it.
set -euo pipefail

uv sync --frozen

# Preinstall everything `pio run/test -e native` would otherwise download at run time,
# so the firmware stage works offline. Versions match engenia/agents/firmware/templates/platformio.ini.
uv run --frozen pio pkg install -g -p "native@1.2.1"
uv run --frozen pio pkg install -g -t "platformio/tool-scons@~4.40801.0"
uv run --frozen pio pkg install -g -l "throwtheswitch/Unity@^2.6.0"
