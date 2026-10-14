"""Pinned source locations shared by catalog tools without importing their CLIs."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "data/raw/fuel-prices-for-be-assessment.csv"
OVERTURE_RELEASE = "2026-09-23.1"
OVERTURE_SOURCE = (
    f"s3://overturemaps-us-west-2/release/{OVERTURE_RELEASE}/theme=places/type=place/*"
)
OVERTURE_ROOT = ROOT / "var/overture_states"
