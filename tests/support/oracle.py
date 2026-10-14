from scipy.optimize import linprog


def independent_path_cost(
    prices: tuple[int, ...],
    leg_miles: list[int],
    initial: int,
    reserve: int = 0,
    capacity: int = 50,
) -> float | None:
    count = len(prices)
    if count == 0:
        return 0.0 if sum(leg_miles) <= (initial - reserve) * 10 else None
    consumed = [miles / 10 for miles in leg_miles]
    constraints = []
    ceilings = []
    for index in range(count):
        # Arrival at stop index must have nonnegative fuel.
        constraints.append(
            [-1.0 if previous < index else 0.0 for previous in range(count)]
        )
        ceilings.append(initial - reserve - sum(consumed[: index + 1]))
        # Departure fuel may not exceed the 50-gallon tank.
        constraints.append(
            [1.0 if previous <= index else 0.0 for previous in range(count)]
        )
        ceilings.append(capacity - initial + sum(consumed[: index + 1]))
    constraints.append([-1.0] * count)
    ceilings.append(initial - reserve - sum(consumed))
    result = linprog(
        prices,
        A_ub=constraints,
        b_ub=ceilings,
        bounds=[(0, None)] * count,
        method="highs",
    )
    return float(result.fun) if result.success else None
