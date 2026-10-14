import argparse
from decimal import Decimal

from app.planning.candidates import CandidateIndex
from scripts.benchmarking.hosted_reference import hosted_reference
from scripts.benchmarking.local_reference import local_reference
from scripts.benchmarking.measurement import ProviderPacing


def offline_reference(
    name: str,
    rows: list[dict],
    index: CandidateIndex,
    args: argparse.Namespace,
    pacing: ProviderPacing,
) -> dict:
    result = hosted_reference(name, rows, index, args)
    pacing.record(result.get("offline_hosted_attempts", 0))
    if not args.skip_local_references:
        pacing.before_request()
        local = local_reference(name, rows, index, args)
        pacing.record(local.get("offline_hosted_attempts", 0))
        result["local_reference"] = local
        if "reference_cost" in local and Decimal(local["reference_cost"]) < Decimal(
            result.get("reference_cost", "Infinity")
        ):
            result["reference_cost"], result["reference_kind"] = (
                local["reference_cost"],
                local["reference_kind"],
            )
        result["independent_reference_available"] = result.get(
            "independent_reference_available", False
        ) or local.get("independent_reference_available", False)
    return result
