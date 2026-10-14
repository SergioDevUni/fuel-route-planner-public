"""Run HTTP load and contract validation outside the measured server process."""

import multiprocessing
from types import MethodType, SimpleNamespace

from scripts.benchmarking.capacity_metrics import PressureSample


def _clients(pipe, url, body, prices, users, seconds):
    from scripts.benchmarking.capacity import CapacityProbe
    from scripts.benchmarking.capacity_sustained import sustained_load

    stations = [
        SimpleNamespace(station_id=key, price_per_gallon=value)
        for key, value in prices.items()
    ]
    probe = SimpleNamespace(
        url=url,
        mode={"replay": True},
        csv_ids=set(prices),
        replays=(SimpleNamespace(stations=stations),),
        observed=[],
        calls=0,
        body=lambda index, same: body,
    )
    probe.request = MethodType(CapacityProbe.request, probe)
    try:
        pipe.send({"row": sustained_load(probe, users, seconds, same=True)})
    except Exception as error:
        pipe.send({"error": type(error).__name__ + ": " + str(error)})
    finally:
        pipe.close()


def sustained_load(probe, users, seconds, *, same=False):
    """Keep server observation/pressure in the parent and HTTP clients in a child."""
    if not same or not probe.replays:
        raise ValueError("External load requires the captured cached workload")
    from app.api.deadline import planning_activity

    before, calls = len(probe.observed), probe.calls
    prices = {v.station_id: v.price_per_gallon for v in probe.replays[0].stations}
    context = multiprocessing.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(
        target=_clients,
        args=(writer, probe.url, probe.body(0, True), prices, users, seconds),
    )
    try:
        with PressureSample() as pressure:
            process.start()
            writer.close()
            if not reader.poll(seconds + 40):
                raise TimeoutError("External clients did not finish and drain")
            result = reader.recv()
            process.join(timeout=5)
            if process.exitcode != 0:
                raise RuntimeError("External client process exited unsuccessfully")
        if "error" in result:
            raise RuntimeError(result["error"])
        row = result["row"]
        budgets = probe.observed[before:]
        row["load_generator_peak"] = row["peak"]
        row["peak"] = pressure.report()
        row["planner_calls"] = probe.calls - calls
        row["cache_hits"] = sum(
            b.metrics.get("completed_result_reuses", 0) for b in budgets
        )
        row["coalesced"] = sum(
            b.metrics.get("coalesced_result_reuses", 0) for b in budgets
        )
        row["replay_attempts"] = sum(b.attempts for b in budgets)
        row["drained"] = planning_activity()["active_planners"] == 0
        row["passed"] = (
            row["passed"] and row["drained"] and pressure.peak["active_planners"] <= 6
        )
        return row
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        reader.close()
        writer.close()
