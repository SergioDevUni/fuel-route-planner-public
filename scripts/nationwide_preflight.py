"""Establish independent feasibility before sending any measured API request."""

from decimal import Decimal

from scripts.feasibility_evidence import first_leg_bound, station_chain, witness


def preflight_case(case, start, finish, stations, adapter, baseline_cache, pacing):
    from app.planning.budget import RequestBudget
    from app.routing.client import RoutingUnavailable

    initial = Decimal(case["initial_fuel_gallons"])
    reserve = Decimal(case["reserve_gallons"])
    bound = first_leg_bound(start, finish, stations, initial, reserve)
    if bound["status"] == "checked_infeasible":
        return bound
    pacing.before_request(4)
    budget = RequestBudget(deadline_seconds=45)
    key = (start, finish)
    try:
        if key not in baseline_cache:
            baseline_cache[key] = adapter.route(key, budget)
        baseline = baseline_cache[key]
        direct = witness(baseline, baseline, (), initial, reserve)
        if direct["status"] == "checked_feasible":
            return {
                **direct,
                "first_leg_bound": bound,
                "hosted_attempts": budget.attempts,
            }
        attempts = []
        seen = set()
        for fraction in (0.75, 0.55, 0.9):
            selected = station_chain(baseline, stations, initial, reserve, fraction)
            ids = tuple(station.station_id for station in selected)
            if not ids or ids in seen or budget.attempts >= 4:
                continue
            seen.add(ids)
            route = adapter.route(
                (start, *(station.coordinates for station in selected), finish), budget
            )
            result = witness(route, baseline, ids, initial, reserve)
            if result["status"] == "checked_feasible":
                return {
                    **result,
                    "first_leg_bound": bound,
                    "hosted_attempts": budget.attempts,
                }
            attempts.append(result)
        return {
            "status": "undetermined",
            "method": "independent witness attempts",
            "first_leg_bound": bound,
            "witness_attempts": attempts,
            "hosted_attempts": budget.attempts,
        }
    except RoutingUnavailable:
        # Stop on provider unavailability instead of classifying quota failures
        # as fuel infeasibility. The run can resume from its saved checkpoint.
        raise
    except Exception as error:
        return {
            "status": "undetermined",
            "method": "independent witness attempts",
            "error": type(error).__name__,
            "first_leg_bound": bound,
            "hosted_attempts": budget.attempts,
        }
    finally:
        pacing.record(budget.attempts)
