#!/usr/bin/env bash
# Title-screen benchmark for the DLSS add-on: one game launch per render scale, TSR and DLSS alternated live through
# the add-on's ini, frame times from its `perf:` lines. No keyboard or mouse input is sent to the desktop.
# Usage: bench.sh <r.ScreenPercentage, 0 = the game's own scale> [cycles] [seconds per half-cycle]
# Output: $OUT/rq<scale>.perf (tagged perf lines) and $OUT/rq<scale>-{TSR,DLSS}.png (game monitor screenshots).
set -u
RQ=${1:?scale}
CYCLES=${2:-4}
HALF=${3:-25}
OUT=${OUT:-/tmp/aincrad-bench}
GAME="/media/cydral/Data/SteamLibrary/steamapps/common/Echoes of Aincrad/EchoesofAincrad/Binaries/Win64"
CFG="/media/cydral/Data/SteamLibrary/steamapps/compatdata/2244210/pfx/drive_c/users/steamuser/AppData/Local/EchoesofAincrad/Saved/Config/WindowsClient/GameUserSettings.ini"
INI="$GAME/amdlss_dx12_dlss.ini"
LOG="$GAME/amdlss_dx12_dlss.log"
PAT="[E]choesofAincrad-Win64-Shipping"
mkdir -p "$OUT"

running() { pgrep -f "$PAT" > /dev/null; }
stop_game() {
	pkill -f "$PAT"
	for _ in $(seq 1 15); do running || return 0; sleep 2; done
	return 1
}
shot() {
	local tmp
	tmp=$(mktemp -d)
	timeout 20 cosmic-screenshot --interactive=false --modal=false --notify=false -s "$tmp" > /dev/null 2>&1
	# The game is on the middle 2560x1440 monitor of a 6400x1440 desktop.
	python3 -I -c "import sys,glob; from PIL import Image; f=glob.glob(sys.argv[1]+'/*.png'); Image.open(f[0]).crop((1920,0,4480,1440)).save(sys.argv[2])" "$tmp" "$1" 2> /dev/null
	rm -rf "$tmp"
}
set_enabled() { sd '^enabled=.*$' "enabled=$1" "$INI"; }

stop_game || { echo "game did not close"; exit 1; }
# sg.ResolutionQuality does not change this game's render size; r.ScreenPercentage under [SystemSettings] in the
# user Engine.ini does, but the game drops that section when it exits, so it is written again before every launch.
ENG="$(dirname "$CFG")/Engine.ini"
python3 -I -c "
import re, sys
path, pct = sys.argv[1], sys.argv[2]
text = open(path, encoding='utf-8').read()
text = re.sub(r'(?m)^\[SystemSettings\]\n(?:r\.ScreenPercentage=.*\n?)?', '', text).rstrip('\n') + '\n'
if pct != '0':
    text += '\n[SystemSettings]\nr.ScreenPercentage=' + pct + '\n'
open(path, 'w', encoding='utf-8').write(text)
" "$ENG" "$RQ"
sd '^ResolutionSizeX=.*$' 'ResolutionSizeX=2560' "$CFG"
sd '^ResolutionSizeY=.*$' 'ResolutionSizeY=1440' "$CFG"
set_enabled 1
start=$(wc -l < "$LOG")
(steam -applaunch 2244210 > /dev/null 2>&1 &)
for _ in $(seq 1 60); do running && break; sleep 2; done
for _ in $(seq 1 60); do tail -n +"$start" "$LOG" | rg -q 'perf:.*upscaler=DLSS' && break; sleep 3; done
sleep 20
: > "$OUT/rq$RQ.perf"
for c in $(seq 1 "$CYCLES"); do
	for mode in TSR DLSS; do
		[ "$mode" = TSR ] && set_enabled 0 || set_enabled 1
		mark=$(wc -l < "$LOG")
		sleep "$HALF"
		[ "$c" = 1 ] && shot "$OUT/rq$RQ-$mode.png"
		tail -n +"$((mark + 1))" "$LOG" | rg '^perf:' | sed "s/^/rq=$RQ cycle=$c mode=$mode /" >> "$OUT/rq$RQ.perf"
	done
done
set_enabled 1
stop_game
wc -l < "$OUT/rq$RQ.perf"
