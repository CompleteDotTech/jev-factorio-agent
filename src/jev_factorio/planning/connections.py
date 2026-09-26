"""Bounded connection planning over caller-verified native placement cells."""
from __future__ import annotations

from collections import deque
import math
from numbers import Real
from typing import Iterable


Position = tuple[float, float]
MAX_CELLS = 65_536
MAX_PATH_LENGTH = 512


def _position(value: object) -> Position:
    if not isinstance(value, tuple) or len(value) != 2:
        raise ValueError("Positions must be coordinate pairs")
    if any(isinstance(coordinate, bool) or not isinstance(coordinate, Real)
           for coordinate in value):
        raise ValueError("Coordinates must be finite numbers")
    try:
        position = (float(value[0]), float(value[1]))
    except (OverflowError, ValueError) as error:
        raise ValueError("Coordinates must be finite numbers") from error
    if any(not math.isfinite(coordinate) or abs(coordinate) > 1_000_000
           or not (coordinate * 2).is_integer() for coordinate in position):
        raise ValueError("Coordinates must be integer or half-integer map positions")
    return position


def _cells(values: Iterable[Position]) -> set[Position]:
    cells: set[Position] = set()
    try:
        for count, value in enumerate(values, start=1):
            if count > MAX_CELLS:
                raise ValueError("Too many connection cells")
            cells.add(_position(value))
    except TypeError as error:
        raise ValueError("Connection cells must be iterable") from error
    return cells


def shortest_pipe_path(
    start: Position,
    end: Position,
    buildable: set[Position],
    existing: Iterable[Position] = (),
) -> list[Position]:
    """Return a shortest four-neighbor route including both reachable endpoints.

    Every endpoint must appear in buildable or existing. Existing cells mean
    compatible pipes verified by the caller, not arbitrary occupied tiles.
    """
    origin, target = _position(start), _position(end)
    passable = _cells(buildable) | _cells(existing)
    if len(passable) > MAX_CELLS:
        raise ValueError("Too many connection cells")
    if origin not in passable or target not in passable:
        raise ValueError("Connection endpoints must be passable")
    if any((origin[axis] - target[axis]) % 1 for axis in (0, 1)):
        raise ValueError("Connection endpoints must share a unit grid")
    frontier = deque([origin])
    previous: dict[Position, Position | None] = {origin: None}
    while frontier:
        current = frontier.popleft()
        if current == target:
            route = []
            cursor: Position | None = target
            while cursor is not None:
                route.append(cursor)
                if len(route) > MAX_PATH_LENGTH:
                    raise ValueError("Connection path exceeds maximum length")
                cursor = previous[cursor]
            return list(reversed(route))
        for delta_x, delta_y in ((1, 0), (0, 1), (-1, 0), (0, -1)):
            neighbor = (current[0] + delta_x, current[1] + delta_y)
            if neighbor in passable and neighbor not in previous:
                previous[neighbor] = current
                frontier.append(neighbor)
    raise ValueError("No passable connection path")


def shortest_wire_path(
    start: Position,
    end: Position,
    buildable: set[Position],
    existing: Iterable[Position] = (),
    max_wire_distance: float = 7.5,
) -> list[Position]:
    """Return a bounded fair pole route over verified native placement cells.

    Unlike pipes, power wires may span an unbuildable tile.  Every returned
    endpoint is still a caller-verified existing pole or a manual-buildable
    placement cell; the caller remains responsible for walking to and placing
    each new pole through the fair adapter.
    """
    return shortest_wire_path_between_regions(
        (start,), (end,), buildable, existing, max_wire_distance=max_wire_distance,
    )


def shortest_wire_path_between_regions(
    starts: Iterable[Position],
    ends: Iterable[Position],
    buildable: set[Position],
    existing: Iterable[Position] = (),
    max_wire_distance: float = 7.5,
) -> list[Position]:
    """Find a wire route between verified pole cells near both native endpoints."""
    if (isinstance(max_wire_distance, bool)
            or not isinstance(max_wire_distance, Real)):
        raise ValueError("Wire distance must be a finite positive number")
    try:
        wire_distance = float(max_wire_distance)
    except (OverflowError, ValueError) as error:
        raise ValueError("Wire distance must be a finite positive number") from error
    if not math.isfinite(wire_distance) or wire_distance <= 0:
        raise ValueError("Wire distance must be a finite positive number")
    passable = _cells(buildable) | _cells(existing)
    if len(passable) > MAX_CELLS:
        raise ValueError("Too many connection cells")
    origins, targets = _cells(starts), _cells(ends)
    if not origins or not targets or not origins <= passable or not targets <= passable:
        raise ValueError("Connection endpoints must be passable")
    buckets: dict[tuple[int, int], list[Position]] = {}
    for point in passable:
        bucket = (
            math.floor(point[0] / wire_distance),
            math.floor(point[1] / wire_distance),
        )
        buckets.setdefault(bucket, []).append(point)
    for values in buckets.values():
        values.sort()
    frontier = deque(sorted(origins))
    previous: dict[Position, Position | None] = {origin: None for origin in origins}
    while frontier:
        current = frontier.popleft()
        if current in targets:
            route = []
            cursor: Position | None = current
            while cursor is not None:
                route.append(cursor)
                if len(route) > MAX_PATH_LENGTH:
                    raise ValueError("Connection path exceeds maximum length")
                cursor = previous[cursor]
            return list(reversed(route))
        bucket = (
            math.floor(current[0] / wire_distance),
            math.floor(current[1] / wire_distance),
        )
        for horizontal in range(bucket[0] - 1, bucket[0] + 2):
            for vertical in range(bucket[1] - 1, bucket[1] + 2):
                for candidate in buckets.get((horizontal, vertical), []):
                    if (candidate not in previous
                            and math.dist(current, candidate) <= wire_distance):
                        previous[candidate] = current
                        frontier.append(candidate)
    raise ValueError("No passable wire route")


def select_pole_positions(
    path: Iterable[Position], max_wire_distance: float = 7.5,
) -> list[Position]:
    """Select reachable wire spans along a caller-verified pole placement route."""
    if (isinstance(max_wire_distance, bool)
            or not isinstance(max_wire_distance, Real)):
        raise ValueError("Wire distance must be a finite positive number")
    try:
        wire_distance = float(max_wire_distance)
    except (OverflowError, ValueError) as error:
        raise ValueError("Wire distance must be a finite positive number") from error
    if not math.isfinite(wire_distance) or wire_distance <= 0:
        raise ValueError("Wire distance must be a finite positive number")
    positions: list[Position] = []
    try:
        for value in path:
            if len(positions) >= MAX_PATH_LENGTH:
                raise ValueError("Connection path exceeds maximum length")
            positions.append(_position(value))
    except TypeError as error:
        raise ValueError("Connection path must be iterable") from error
    if not positions:
        raise ValueError("Connection path is empty")
    if any(math.dist(left, right) > wire_distance
           for left, right in zip(positions, positions[1:])):
        raise ValueError("Consecutive route positions exceed wire distance")
    selected = [positions[0]]
    anchor = 0
    while anchor < len(positions) - 1:
        next_anchor = anchor + 1
        while (next_anchor + 1 < len(positions)
               and math.dist(positions[anchor], positions[next_anchor + 1])
               <= wire_distance):
            next_anchor += 1
        if positions[next_anchor] != selected[-1]:
            selected.append(positions[next_anchor])
        anchor = next_anchor
    return selected
