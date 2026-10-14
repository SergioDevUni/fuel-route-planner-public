import argparse
import json
from pathlib import Path

from scripts.local_common import ROOT
from scripts.road_precompute.builder import build


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidates", type=Path, default=ROOT / "var/station_candidates"
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=(
            ROOT / "var/road_nationwide/station_edges_v2_live.db"
            if (ROOT / "var/road_nationwide/station_edges_v2_live.db").is_file()
            else ROOT / "var/road_nationwide/station_edges.db"
        ),
    )
    parser.add_argument("--url", default="http://127.0.0.1:8004")
    parser.add_argument("--graph-version", required=True)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--max-groups", type=int)
    parser.add_argument("--priority-file", type=Path)
    parser.add_argument("--priority-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            build(
                args.candidates,
                args.database,
                args.url,
                args.graph_version,
                batch_size=args.batch_size,
                workers=args.workers,
                max_groups=args.max_groups,
                priority_file=args.priority_file,
                priority_only=args.priority_only,
            )
        )
    )


if __name__ == "__main__":
    main()
