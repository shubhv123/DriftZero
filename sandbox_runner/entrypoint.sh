#!/bin/sh
# Applies git patch, runs tests, outputs structured JSON report
set -e

echo "🐳 [DriftZero Sandbox] Initializing verification container..."

# 1. Install workspace dependencies if requirements manifest is present and network is available
if [ -f "requirements.txt" ]; then
    echo "📦 Checking dependencies from requirements.txt..."
    pip install --no-cache-dir -r requirements.txt 2>/dev/null || true
elif [ -f "pyproject.toml" ]; then
    echo "📦 Checking package from pyproject.toml..."
    pip install --no-cache-dir . 2>/dev/null || true
fi

# 2. If TARGET_LIBRARY is set, ensure target library and target version are installed
if [ -n "$TARGET_LIBRARY" ]; then
    if [ -n "$TARGET_VERSION" ]; then
        echo "📦 Ensuring ${TARGET_LIBRARY}==${TARGET_VERSION} is installed..."
        pip install --no-cache-dir "${TARGET_LIBRARY}==${TARGET_VERSION}" 2>/dev/null || true
    else
        echo "📦 Ensuring ${TARGET_LIBRARY} is installed..."
        pip install --no-cache-dir "${TARGET_LIBRARY}" 2>/dev/null || true
    fi
fi

# 3. Ensure pytest is installed
if ! command -v pytest &> /dev/null; then
    pip install --no-cache-dir pytest
fi

# 4. Run test suite
if [ $# -gt 0 ]; then
    exec pytest "$@"
else
    exec pytest test_migration_parity.py -v --tb=short
fi
