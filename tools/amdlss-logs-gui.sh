#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Desktop front end for tools/amdlss-logs.py: asks for the tester's message, downloads the report, opens its folder.
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

message=$(zenity --entry --title="$TITLE" --width=420 \
    --text="Paste the tester's message (AMDLSS report CODE):") || exit 0
code=$(printf '%s' "$message" | tr '[:lower:]' '[:upper:]' | grep -oE '\b[0-9A-HJKMNP-TV-Z]{8}\b' | tail -n 1)
if [ -z "$code" ]; then
    zenity --error --title="$TITLE" --text="No report code in that message. A code is 8 letters and digits."
    exit 1
fi

if [ -d "$OUT/$code" ]; then   # already downloaded: the tool never overwrites, so just show it
    xdg-open "$OUT/$code" >/dev/null 2>&1 &
    exit 0
fi

output=$(AMDLSS_LOGS_URL="$URL" AMDLSS_LOGS_TOKEN="$(cat "$TOKEN_FILE")" \
    python3 -I "$HERE/amdlss-logs.py" fetch "$code" 2>&1)
status=$?
if [ "$status" -ne 0 ]; then
    if printf '%s' "$output" | grep -q 'read token was refused'; then
        zenity --question --title="$TITLE" --text="The server refused the saved read token.\n\nForget it and ask again next time?" \
            && rm -f "$TOKEN_FILE"
    else
        printf '%s\n' "$output" | zenity --text-info --title="$TITLE: $code failed" --width=800 --height=400
    fi
    exit 1
fi
xdg-open "$OUT/$code" >/dev/null 2>&1 &
printf '%s\n' "$output" | zenity --text-info --title="$TITLE: $code" --width=1000 --height=700
