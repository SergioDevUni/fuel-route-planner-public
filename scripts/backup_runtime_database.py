"""Create a consistent SQLite online backup without copying WAL sidecars."""

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path


def backup_database(source: Path, destination: Path) -> None:
    source, destination = source.resolve(), destination.resolve()
    if not source.is_file():
        raise ValueError("Source database does not exist")
    sidecars = {Path(str(source) + suffix) for suffix in ("-wal", "-shm", "-journal")}
    if source == destination or destination in sidecars or destination.exists():
        raise ValueError("Backup destination must be new and distinct")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Reserve the exact destination; never overwrite an existing backup.
    with destination.open("xb"):
        pass
    try:
        with (
            closing(
                sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
            ) as original,
            closing(sqlite3.connect(destination)) as backup,
        ):
            original.backup(backup, pages=256)
            if backup.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ValueError("Backup integrity check failed")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--source", type=Path, default=Path("var/app.sqlite3"))
    args = parser.parse_args()
    backup_database(args.source, args.destination)
    print("Database backup verified")


if __name__ == "__main__":
    main()
