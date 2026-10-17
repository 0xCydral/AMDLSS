#!/usr/bin/env bash
# Steady-state screenshots of the title screen with the add-on held in one mode (enabled=1 DLSS, enabled=0 TSR) for the
# whole launch, so no switch between upscalers is on screen. No keyboard or mouse input is sent to the desktop.
# Usage: shots.sh <1|0> <label> [count] [seconds between shots]
set -u
EN=${1:?enabled}
LABEL=${2:?label}
COUNT=${3:-6}
GAP=${4:-4}
OUT=${OUT:-/tmp/aincrad-shots/steady}
GAME="/media/cydral/Data/SteamLibrary/steamapps/common/Echoes of Aincrad/EchoesofAincrad/Binaries/Win64"
INI="$GAME/amdlss_dx12_dlss.ini"
LOG="$GAME/amdlss_dx12_dlss.log"
PAT="[E]choesofAincrad-Win64-Shipping"
mkdir -p "$OUT"
running() { pgrep -f "$PAT" > /dev/null; }
pkill -f "$PAT"
for _ in $(seq 1 15); do running || break; sleep 2; done
sd '^enabled=.*$' "enabled=$EN" "$INI"
start=$(wc -l < "$LOG")
(steam -applaunch 2244210 > /dev/null 2>&1 &)
for _ in $(seq 1 60); do running && break; sleep 2; done
for _ in $(seq 1 60); do tail -n +"$start" "$LOG" | rg -q 'perf:.*upscaler=(DLSS|TSR)' && break; sleep 3; done
sleep 20
for i in $(seq 1 "$COUNT"); do
	tmp=$(mktemp -d)
	timeout 20 cosmic-screenshot --interactive=false --modal=false --notify=false -s "$tmp" > /dev/null 2>&1
	python3 -I -c "import sys,glob; from PIL import Image; f=glob.glob(sys.argv[1]+'/*.png'); Image.open(f[0]).crop((1920,0,4480,1440)).save(sys.argv[2])" "$tmp" "$OUT/$LABEL-$i.png" 2> /dev/null
	rm -rf "$tmp"
	sleep "$GAP"
done
tail -n +"$start" "$LOG" | rg '^perf:|^accounting' | tail -4
pkill -f "$PAT"
