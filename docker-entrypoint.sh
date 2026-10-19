#!/bin/sh
set -eu

export DISPLAY=:99
Xvfb "$DISPLAY" -screen 0 1280x720x24 -nolisten tcp &
for attempt in 1 2 3 4 5; do
    if [ -S /tmp/.X11-unix/X99 ]; then break; fi
    sleep 1
done
matchbox-window-manager &
x11vnc -display "$DISPLAY" -forever -shared -nopw -rfbport 5900 \
    -localhost -bg -o /tmp/x11vnc.log
websockify --web=/usr/share/novnc 6080 localhost:5900 &

exec python -m integration.server
