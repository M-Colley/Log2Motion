#!/bin/sh
set -eu

# CLI flags run Log2Motion; an explicit command can run diagnostics or smoke tests.
case "${1:-}" in
    ""|-*) set -- python /app/log2motion_visualization.py "$@" ;;
esac

exec xvfb-run -a --server-args="-screen 0 1280x720x24 -nolisten tcp" "$@"
