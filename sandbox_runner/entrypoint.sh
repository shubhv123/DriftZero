#!/bin/sh
set -e

echo "🐳 [DriftZero Sandbox] Initializing verification container..."

# Check if pytest is available
if ! command -v pytest >/dev/null 2>&1; then
    echo "❌ [DriftZero Sandbox] Error: pytest executable not found in container!" >&2
    exit 1
fi

# If /out/golden.json exists on mounted host volume and not in /workspace, copy it into /workspace
if [ -d "/out" ] && [ -f "/out/golden.json" ] && [ ! -f "golden.json" ]; then
    cp /out/golden.json ./golden.json
fi

# Run test suite
RET=0
if [ $# -gt 0 ]; then
    pytest "$@" || RET=$?
else
    pytest test_migration_parity.py -v --tb=short || RET=$?
fi

# If /out volume is mounted and golden.json was generated, write it to /out
if [ -d "/out" ] && [ -f "golden.json" ]; then
    cp golden.json /out/golden.json
fi

exit $RET
