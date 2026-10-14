"""Audit all-offer scope and road gaps on captured trips without provider calls."""

import argparse
import json
import os
import sqlite3
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from scripts.local_common import ROOT, planner_hash, sha256


def audit(facts: Path, database: Path) -> dict:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    from app.planning.budget import RequestBudget
    from app.planning.national_graph import completed_road_points
    from app.planning.national_scope import screen_csv_offers
    from app.planning.quality_evidence import purchase_bound
    from scripts.benchmarking.offline_replay import calculate_replay, load_replays

    index, cases = load_replays(ROOT, facts)
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        metadata = dict(db.execute("SELECT key,value FROM metadata"))
        points = db.execute("SELECT COUNT(*) FROM point WHERE active=1").fetchone()[0]
        valid = db.execute(
            "SELECT COUNT(*) FROM point WHERE active=1 AND snap_meters IS NOT NULL"
        ).fetchone()[0]
        edges = db.execute("SELECT COUNT(*) FROM edge").fetchone()[0]
    complete = completed_road_points(
        database,
        index.version,
        metadata["road_graph_version"],
        active_point_hash=index.road_point_hash,
    )
    rows = []
    pending = set()
    for name, case in cases.items():
        budget = RequestBudget()
        try:
            payload = calculate_replay(case, index, budget)
        except Exception as error:
            rows.append(
                {
                    "case": name,
                    "status": "replay_request_failed",
                    "error_type": type(error).__name__,
                }
            )
            continue
        # Public money is rounded; add half a cent plus a guard before using it
        # as an upper bound. This audit must not prune on rounded-down cost.
        upper = Decimal(payload["costs"]["fuel_purchases_cost"]) + Decimal("0.00500001")
        scope = screen_csv_offers(
            index.stations,
            case.start.coordinates,
            case.finish.coordinates,
            case.trip.initial_fuel_gallons,
            case.trip.reserve_gallons,
            upper,
            price_access_bound=True,
        )
        missing = {
            item.station.coordinates
            for item in scope.possible_offers
            if item.station.coordinates not in complete
        }
        pending.update(missing)
        rows.append(
            {
                "case": name,
                "status": "cost_comparison",
                "loaded_csv_offers": len(index.stations),
                "safely_excluded_by_cost": scope.excluded_by_cost,
                "eligible_csv_offers": len(scope.possible_offers),
                "missing_source_points": len(missing),
                "bound": purchase_bound(
                    case.start.coordinates,
                    case.finish.coordinates,
                    index.stations,
                    case.trip.initial_fuel_gallons,
                    case.trip.reserve_gallons,
                    upper,
                ),
                "replay_attempts": budget.attempts,
                "road_verification_scope": "captured same-profile road directions",
            }
        )
    return {
        "schema": 1,
        "planner_sha256": planner_hash(ROOT),
        "candidate_sha256": index.version,
        "road_facts_sha256": sha256(facts),
        "hosted_attempts": 0,
        "road_metadata": metadata,
        "road_points": points,
        "road_located_points": valid,
        "completed_sources": len(complete),
        "directed_edges": edges,
        "remaining_located_sources": valid - len(complete),
        "priority_missing_points": len(pending),
        "storage_estimate": {
            "checkpoint_bytes": database.stat().st_size,
            "projected_additional_bytes": int(
                database.stat().st_size
                * (valid - len(complete))
                / max(1, len(complete))
            ),
            "method": "linear source density; excludes tiles, backups and WAL",
        },
        "model": "Fixed CSV prices/vehicle, 30-minute allowance and prescribed "
        "directed fastest-profile station legs. Alternative road paths are outside "
        "this model.",
        "checkpoint_screening": [
            "Completed sources describe the checkpoint's original target screen.",
            "Legacy target screening uses raw geodesic range plus 1m.",
            "Updated target screening adds 2000m of snap allowance.",
        ],
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--road-facts", type=Path, required=True)
    parser.add_argument(
        "--database",
        type=Path,
        default=ROOT / "var/road_nationwide/station_edges_v2_live.db",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a fresh output")
    report = audit(args.road_facts.resolve(), args.database.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "road_points",
                    "completed_sources",
                    "priority_missing_points",
                    "hosted_attempts",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
