import json
import logging
import os
import re
from collections import deque
from decimal import Decimal
from time import monotonic, perf_counter, sleep
from unittest.mock import patch

import httpx
from django.test import Client

from app.api.cache import trip_cache, verified_incumbent_cache
from app.api.validation import TripInput
from app.planning.baseline_cache import baseline_route_cache, local_baseline_route_cache
from app.planning.budget import RequestBudget
from app.planning.candidates import CandidateIndex, _prepare_geometry
from app.planning.national_graph import completed_road_points
from app.planning.road_graph import graph_from_matrix
from scripts.benchmarking.capture import HOSTED_GRAPHS
from scripts.benchmarking.cases import BENCHMARK_CASES, Case
from scripts.benchmarking.contracts import validate_verified_trip
from scripts.benchmarking.policy import DATABASE, GRAPH_VERSION


class ProviderPacing:
    """Pace between measurements; never add a sleep to measured planning."""

    def __init__(self, ceiling: int = 28) -> None:
        self.calls: deque[float] = deque()
        self.ceiling = ceiling

    def before_request(self, reserve_attempts: int = 4) -> None:
        now = monotonic()
        while self.calls and self.calls[0] <= now - 61:
            self.calls.popleft()
        if len(self.calls) + reserve_attempts > self.ceiling:
            sleep(max(0, self.calls[0] + 61 - now))
            self.before_request(reserve_attempts)

    def record(self, attempts: int) -> None:
        self.calls.extend([monotonic()] * attempts)


class SearchEvidence(logging.Handler):
    """Capture only safe search counters from production logs."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if "search" in message or "selection" in message:
            allowed = re.findall(
                r"(?:complete|expansions|candidates|csv_offers|eligible|retained|unavailable|points)=[\w.]+",
                message,
            )
            if allowed:
                self.events.append(" ".join(allowed))


def trip_input(case: Case) -> TripInput:
    return TripInput(
        case.start,
        case.finish,
        Decimal(case.initial),
        Decimal("3.20"),
        Decimal(case.reserve),
        None,
        None,
        "balanced",
        Decimal(30),
    )


def measure(
    name: str,
    mode: str,
    state: str,
    trial: int,
    index: CandidateIndex,
    run_version: str,
) -> dict:
    case = BENCHMARK_CASES[name]
    os.environ["FUEL_PARTIAL_ROAD_REFINER"] = "1" if mode == "portfolio" else "0"
    os.environ["FUEL_PARTIAL_ROAD_PORTFOLIO"] = "1" if mode == "portfolio" else "0"
    if state != "result_cached":
        trip_cache._entries.clear()
    if state == "routing_cold":
        verified_incumbent_cache._entries.clear()
        _prepare_geometry.cache_clear()
        CandidateIndex.along_route.cache_clear()
        baseline_route_cache._entries.clear()
        local_baseline_route_cache._entries.clear()
        os.environ["FUEL_MATRIX_CACHE_VERSION"] = f"{run_version}-{name}-{mode}-{trial}"
    request = {
        "start": case.start,
        "finish": case.finish,
        "initial_fuel_gallons": case.initial,
        "reserve_gallons": case.reserve,
        "initial_fuel_price_per_gallon": "3.20",
        "optimization_mode": "balanced",
        "max_extra_duration_minutes": "30",
    }
    budgets: list[RequestBudget] = []

    def observed_budget() -> RequestBudget:
        budget = RequestBudget()
        budgets.append(budget)
        return budget

    evidence = SearchEvidence()
    loggers = [
        logging.getLogger(f"app.planning.{name}")
        for name in ("trip", "national_refiner")
    ]
    levels = [logger.level for logger in loggers]
    for logger in loggers:
        logger.addHandler(evidence)
        logger.setLevel(logging.INFO)
    try:
        completed_at_submission = len(
            completed_road_points(
                DATABASE,
                index.version,
                GRAPH_VERSION,
                active_point_hash=index.road_point_hash,
            )
        )
    except Exception:
        completed_at_submission = None
    started = perf_counter()
    provider_statuses: list[int] = []
    original_send = httpx.Client.send

    def observed_send(client: httpx.Client, request: httpx.Request, **kwargs):
        response = original_send(client, request, **kwargs)
        if request.url.host == "api.heigit.org":
            provider_statuses.append(response.status_code)
        return response

    def observed_graph(*args, **kwargs):
        graph = graph_from_matrix(*args, **kwargs)
        HOSTED_GRAPHS[name] = graph
        return graph

    try:
        with (
            patch("app.api.views.RequestBudget", observed_budget),
            patch("httpx.Client.send", observed_send),
            patch("app.planning.matrix_selection.graph_from_matrix", observed_graph),
        ):
            response = Client().post(
                "/api/v1/trips",
                data=json.dumps(request),
                content_type="application/json",
            )
        payload = response.json()
    finally:
        for logger, level in zip(loggers, levels, strict=True):
            logger.removeHandler(evidence)
            logger.setLevel(level)
    row = {
        "case": name,
        "held_out": case.held_out,
        "mode": mode,
        "cache_state": state,
        "trial": trial,
        "completed_source_points_at_submission": completed_at_submission,
        "http_status": response.status_code,
        "seconds": round(perf_counter() - started, 3),
        "hosted_attempts": budgets[0].attempts if budgets else None,
        "provider_http_statuses": provider_statuses,
        "search_evidence": evidence.events,
        "contract_ok": False,
        "error": payload.get("error", {}).get("code"),
        "stage_seconds": budgets[0].stage_seconds if budgets else {},
        "completed_stage_cpu_seconds": budgets[0].completed_stage_cpu_seconds
        if budgets
        else {},
        "work_metrics": budgets[0].metrics if budgets else {},
        "offer_audit": budgets[0].offer_audit if budgets else {},
        "first_verified_seconds": budgets[0].first_verified_seconds
        if budgets
        else None,
        "cost_trajectory": budgets[0].cost_trajectory if budgets else [],
        "first_verified_from_cache": budgets[0].first_verified_from_cache
        if budgets
        else None,
    }
    if response.status_code == 200:
        by_id = {station.station_id: station for station in index.stations}
        errors = validate_verified_trip(payload, set(by_id))
        for stop in payload["fuel_stops"]:
            if (
                stop["station_id"] in by_id
                and Decimal(stop["price_per_gallon"])
                != by_id[stop["station_id"]].price_per_gallon
            ):
                errors.append("selected price differs from CSV")
        selection = payload["assumptions"]["station_selection"]
        profile = budgets[0].verified_routing_profile if budgets else None
        row.update(
            {
                "contract_ok": not errors,
                "contract_errors": errors,
                "purchase_cost": payload["costs"]["fuel_purchases_cost"],
                "station_ids": [stop["station_id"] for stop in payload["fuel_stops"]],
                "routed_station_count": len(payload["route"]["legs"]) - 1,
                "selection": selection,
                "extra_minutes": str(
                    Decimal(payload["route"]["extra_duration_seconds"]) / 60
                ),
                "profile": {
                    "local": "valhalla",
                    "hosted": "ors_fastest_driving_car",
                }.get(profile),
            }
        )
    return row
