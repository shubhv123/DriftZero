# Env variables, Nebius model IDs, pricing constants for telemetry
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

BLAST_RADIUS_FILE = PROJECT_ROOT / "BlastRadiusReport.json"
MIGRATION_DELTA_FILE = PROJECT_ROOT / "MigrationDelta.json"
MIGRATION_RESULTS_FILE = PROJECT_ROOT / "MigrationResults.json"
MIGRATED_REPO_DIR = PROJECT_ROOT / "migrated_target_repo"

NEBIUS_API_KEY = os.getenv("NEBIUS_API_KEY", "")
NEBIUS_BASE_URL = os.getenv("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/")

NEMOTRON_SUPER_MODEL = os.getenv(
    "NEMOTRON_SUPER_MODEL",
    "nvidia/nemotron-3-super-120b-a12b"
)

NEMOTRON_ULTRA_MODEL = os.getenv(
    "NEMOTRON_ULTRA_MODEL",
    "nvidia/Nemotron-3-Ultra-550b-a55b"
)

PRICE_PER_1M_SUPER_INPUT = 0.13
PRICE_PER_1M_SUPER_OUTPUT = 0.40

PRICE_PER_1M_ULTRA_INPUT = 0.6
PRICE_PER_1M_ULTRA_OUTPUT = 1.8

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY","")