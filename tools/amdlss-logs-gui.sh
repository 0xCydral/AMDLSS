#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Desktop front end for tools/amdlss-logs.py: downloads every new report (sync) and opens the index page.
# The read token is asked once and kept in ~/.config/amdlss-logs/token (mode 0600); it never appears on a command line.
set -u
HERE=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
URL=https://amdlss-reports.amdlss-reports.workers.dev
CONF="${XDG_CONFIG_HOME:-$HOME/.config}/amdlss-logs"
TOKEN_FILE="$CONF/token"
OUT="$HOME/amdlss-reports"
TITLE='AMDLSS logs'

if [ ! -s "$TOKEN_FILE" ]; then
    token=$(zenity --password --title="$TITLE: read token (asked once)") || exit 0
    [ -n "$token" ] || exit 0
    mkdir -p "$CONF" && chmod 700 "$CONF"
    (umask 077 && printf '%s' "$token" > "$TOKEN_FILE")
    unset token
fi

# sync reads the token from the file itself; show a pulsing progress dialog while it runs
work=$(mktemp -d) || exit 1
AMDLSS_LOGS_URL="$URL" python3 -I "$HERE/amdlss-logs.py" sync > "$work/out" 2>&1 &
job=$!
(while kill -0 "$job" 2>/dev/null; do echo '# Downloading new reports...'; sleep 1; done) \
    | zenity --progress --pulsate --no-cancel --title="$TITLE" --text='Downloading new reports...' >/dev/null 2>&1
wait "$job"
status=$?
output=$(cat "$work/out")
rm -rf "$work"

if [ "$status" -ne 0 ]; then
    if printf '%s' "$output" | grep -q 'read token was refused'; then
        zenity --question --title="$TITLE" --text="The server refused the saved read token.\n\nForget it and ask again next time?" \
            && rm -f "$TOKEN_FILE"
    else
        printf '%s\n' "$output" | zenity --text-info --title="$TITLE: sync failed" --width=800 --height=400
    fi
    exit 1
fi
xdg-open "$OUT/index.html" >/dev/null 2>&1 &
