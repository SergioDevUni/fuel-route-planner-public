"""Shared captured graph identity for one benchmark process."""

from app.planning.fuel_graph import DirectedFuelGraph

HOSTED_GRAPHS: dict[str, DirectedFuelGraph] = {}
