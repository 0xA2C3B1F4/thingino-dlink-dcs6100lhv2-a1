#!/bin/sh
# Retired compatibility entry point. Full Raptor composition is required.
set -eu
printf '%s\n' 'volatile runtime candidates are retired; use the composed full Raptor image' >&2
exit 2
