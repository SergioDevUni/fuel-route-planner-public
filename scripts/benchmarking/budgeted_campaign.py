"""Deterministic state coverage and performance sampling within 960 attempts."""

from copy import deepcopy

from scripts.benchmarking.campaign import (
    CACHE_STATES,
    CONCURRENT_CASES,
    HOSTED_CONCURRENCY,
    performance_inputs,
)

CALL_CAP = 960
PERFORMANCE_CASES = (
    *CONCURRENT_CASES[:6],
    "chicago_joliet_full",
    "clayton_lamar",
    "albuquerque_denver",
    "dallas_houston_low_tank",
    "portland_seattle_reserve",
    "oakland_fresno",
)


def select_sample(sample: dict) -> dict:
    states = sorted(sample["states"])
    if len(states) != 48 or len(set(states)) != 48:
        raise ValueError("48 distinct contiguous states are required")
    cases = {}
    for index, state in enumerate(states):
        stratum = ("short", "regional", "long")[index % 3]
        fuel = {"short": "5", "regional": "20", "long": "50"}[stratum]
        name = f"{state.lower()}_{stratum}_{fuel}"
        case = sample["cases"][name]
        if (case["start_state"], case["stratum"], case["initial_fuel_gallons"]) != (
            state,
            stratum,
            fuel,
        ):
            raise ValueError(f"sample input differs: {name}")
        cases[name] = deepcopy(case)
    for name in ("oakland_fresno_10", "oakland_fresno_50"):
        cases[name] = deepcopy(sample["cases"][name])
    reduced = deepcopy(sample)
    reduced.update(
        cases=cases,
        selection="Budget 960: sorted states rotate short/5, regional/20, long/50; "
        "16 inputs per distance stratum plus both Oakland-Fresno regressions. "
        "Selection is fixed before outcomes.",
    )
    return reduced


def specification(sample: dict) -> dict:
    inputs = performance_inputs()
    concurrent = [name for name in CONCURRENT_CASES if name in PERFORMANCE_CASES]
    allowances = {
        "nationwide_preflight": len(sample["cases"]) * 4,
        "nationwide_measurements": len(sample["cases"]) * 4,
        "performance_sequential": len(PERFORMANCE_CASES) * len(CACHE_STATES) * 4,
        "performance_concurrent": len(concurrent)
        * len(HOSTED_CONCURRENCY)
        * len(CACHE_STATES)
        * 4,
        "independent_references": len(PERFORMANCE_CASES) * (2 * 4 + 4),
    }
    total = sum(allowances.values())
    if total > CALL_CAP:
        raise ValueError("selected campaign exceeds the shared cap")
    return {
        "profile": "budget-960",
        "scope": "50 nationwide inputs and 12 performance routes",
        "performance_cases": {name: inputs[name] for name in PERFORMANCE_CASES},
        "concurrent_cases": concurrent,
        "protocol": {
            "repeats": 1,
            "cache_states": list(CACHE_STATES),
            "hosted_concurrency": list(HOSTED_CONCURRENCY),
        },
        "hosted_attempt_allowances": allowances,
        "conservative_total_allowance": total,
        "hard_call_cap": CALL_CAP,
        "reserve_attempts": CALL_CAP - total,
        "accounting": "Both phases share one persistent ledger. Every provider "
        "attempt, including failures and retries, counts toward the hard cap. "
        "Execution pauses at the cap and retains its checkpoint.",
        "assessment_scope": "Results describe this owner-selected development "
        "subset. The historical 290-input/54-route study remains available; "
        "neither profile is an assessment requirement or release obligation.",
    }
