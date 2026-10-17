#!/usr/bin/env bash
# Starts Echoes of Aincrad through Proton directly (not Steam), so environment variables such as
# VKD3D_FILTER_DEVICE_NAME reach the game. Extra environment comes from the caller.
# Usage: [VAR=value ...] run_proton.sh <seconds to wait before reporting> <log file>
set -u
WAIT=${1:-40}
OUT=${2:-/tmp/aincrad-shots/proton.log}
GAME="/media/cydral/Data/SteamLibrary/steamapps/common/Echoes of Aincrad/EchoesofAincrad/Binaries/Win64"
PROTON="/media/cydral/Data/SteamLibrary/steamapps/common/Proton - Experimental/proton"
EXE=EchoesofAincrad-Win64-Shipping
# The pattern is split so this script's own command line never matches it.
is_running() { pgrep -f "[E]choesofAincrad-Win64-${EXE##*-}" > /dev/null; }
if is_running; then echo "game already running, not starting"; exit 1; fi
cd "$GAME" || exit 1
STEAM_COMPAT_DATA_PATH=/media/cydral/Data/SteamLibrary/steamapps/compatdata/2244210 \
STEAM_COMPAT_CLIENT_INSTALL_PATH="$HOME/.steam/steam" SteamAppId=2244210 SteamGameId=2244210 \
WINEDLLOVERRIDES="${WINEDLLOVERRIDES:-dxgi=n,b}" setsid "$PROTON" run "$GAME/$EXE.exe" > "$OUT" 2>&1 &
for _ in $(seq 1 20); do is_running && break; sleep 2; done
for _ in $(seq 1 "$((WAIT / 2))"); do is_running || break; sleep 2; done
if is_running; then echo "running after ${WAIT}s"; else echo "exited within ${WAIT}s"; fi
