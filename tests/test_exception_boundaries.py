"""Rich domain errors must retain their identity through timed work scopes."""

from contextlib import contextmanager
from decimal import Decimal

import pytest

from app.domain.location import LocationAmbiguous
from app.planning.budget import RequestBudget
from app.planning.fuel import FuelShortfall
from app.planning.portfolio import primary_request_work


@contextmanager
def scope():
    yield


@pytest.mark.parametrize(
    "error", [LocationAmbiguous((), False), FuelShortfall(1, Decimal("0.01"))]
)
@pytest.mark.parametrize("boundary", ["scope", "primary", "stage"])
def test_domain_errors_survive_context_manager_traceback_assignment(error, boundary):
    budget = RequestBudget()
    context = {
        "scope": scope(),
        "primary": primary_request_work(budget),
        "stage": budget.stage("regression"),
    }[boundary]
    with pytest.raises(type(error)) as caught, context:
        raise error
    assert caught.value is error
    assert error.__traceback__ is not None
