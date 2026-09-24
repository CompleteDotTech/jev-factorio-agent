"""Player-controlled actions without teleporting or synthetic harvests."""
from __future__ import annotations

import json
import math
import time
from importlib.resources import files
from types import SimpleNamespace
from typing import Any
from .errors import ConnectionPreflightRejected


class FairActions:
    def __init__(self, backend: Any) -> None:
        self.backend = backend
        self.command(files("jev_factorio").joinpath("lua/fair_actions.lua").read_text())
        self.call("bind")

    def command(self, script: str) -> str:
        result = self.backend._instance.rcon_client.send_command("/sc " + script) or ""
        if result.startswith("Cannot execute command."):
            raise RuntimeError(result)
        return result

    def call(self, function: str, *arguments: Any) -> dict:
        encoded = ", ".join(
            "helpers.json_to_table(" + json.dumps(json.dumps(value, allow_nan=False)) + ")"
            for value in arguments
        )
        return json.loads(self.command(
            f"rcon.print(helpers.table_to_json(storage.fair.{function}({encoded})))"
        ))

    @staticmethod
    def position(position: Any) -> dict:
        result = {"x": float(position.x), "y": float(position.y)}
        if not all(math.isfinite(value) for value in result.values()):
            raise ValueError("Position must be finite")
        return result

    def wait(self, timeout: float = 180) -> dict:
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                state = self.call("observe")
                if state["status"] == "completed":
                    return state
                if state["status"] == "failed":
                    raise RuntimeError(state.get("error", "Native controls failed"))
                time.sleep(0.1)
            raise TimeoutError("Native action exceeded its bounded observation window")
        finally:
            self.command("storage.fair.stop()")

    def _note(self, key: str, amount: int = 1) -> None:
        if not hasattr(self, "metrics"):
            self.metrics = {}
        self.metrics[key] = self.metrics.get(key, 0) + amount

    def move_to(self, position: Any) -> Any:
        from fle.env import Position

        self._note("move_requests")
        self.call("begin_move", self.position(position))
        state = self.wait()
        result = Position(**state["position"])
        self.backend._instance.namespace.player_location = result
        return result

    def approach(self, position: Any, name: str = "character") -> None:
        from fle.env import Position

        center = self.position(position)
        self._note("approach_requests")
        result = json.loads(self.command(
            "local player = storage.fair.actor(); local prototype = prototypes.entity["
            + json.dumps(name) + "]; local box = prototype.selection_box; "
            "local target = helpers.json_to_table(" + json.dumps(json.dumps(center)) + "); "
            "local entity = player.surface.find_entity(" + json.dumps(name) + ", target); "
            "if entity and entity.valid and player.can_reach_entity(entity) then "
            "rcon.print(helpers.table_to_json({reachable=true})); return end; "
            "target.x = target.x + math.max(math.abs(box.left_top.x), "
            "math.abs(box.right_bottom.x)) + 1.5; "
            "local position = player.surface.find_non_colliding_position('character', target, 8, 0.25); "
            "assert(position, 'No collision-free approach'); rcon.print(helpers.table_to_json(position))"
        ))
        if result.get("reachable") is True:
            self._note("approaches_skipped_in_reach")
            return
        self.move_to(Position(**result))

    def harvest(self, resource: str, position: Any, quantity: int) -> int:
        from fle.env import Position

        if type(quantity) is not int or quantity <= 0:
            raise ValueError("Mining quantity must be positive")
        self._note("mining_batches_started")
        gained = 0
        for attempt in range(quantity):
            if gained == 0:
                # Honor the resource coordinate from the observation that
                # authorized this action.  It is the target to which the
                # controller committed, rather than merely a same-name node
                # near the player.
                target = self.position(position)
            else:
                # After native mining depleted a node, reacquire around the
                # actor rather than searching around the stale original
                # coordinate. This only selects a target: approach() still
                # walks normally and begin_mine() enforces reach.
                target = self.call("next_mine_target", resource, 64).get("position")
                if not isinstance(target, dict):
                    raise RuntimeError("No mineable resource observed near the walking actor")
            approach = self.call("mine_approach", target, resource)
            if not approach["reachable"]:
                self.move_to(Position(**approach["position"]))
            self._note("mining_starts")
            self.call("begin_mine", target, resource, quantity - gained)
            try:
                result = self.wait()
            except RuntimeError:
                result = self.call("observe")
                if result.get("error") != "Resource depleted before requested amount" or result["gained"] <= 0:
                    raise
            gained += result["gained"]
            self._note("mined_items", result["gained"])
            if gained >= quantity:
                return gained
        raise RuntimeError("Mining target budget exhausted")

    def approach_build(self, position: Any) -> None:
        """Walk only when the native placement center is outside build reach."""
        from fle.env import Position

        center = self.position(position)
        self._note("approach_requests")
        result = json.loads(self.command(
            "local player=storage.fair.actor(); local target=helpers.json_to_table("
            + json.dumps(json.dumps(center)) + "); "
            "local dx=player.position.x-target.x; local dy=player.position.y-target.y; "
            "local distance_squared=dx*dx+dy*dy; local reach=player.build_distance; "
            "if distance_squared<=reach^2 then "
            "rcon.print(helpers.table_to_json({reachable=true})); return end; "
            "assert(reach>0, 'No native build reach'); "
            "local distance=math.sqrt(distance_squared); local radius=math.max(0,reach-0.5); "
            "local near={x=target.x+dx/distance*radius,y=target.y+dy/distance*radius}; "
            "local position=player.surface.find_non_colliding_position('character',near,1,0.25); "
            "assert(position, 'No collision-free build approach'); "
            "assert((position.x-target.x)^2+(position.y-target.y)^2<=reach^2, "
            "'Build approach is outside normal reach'); "
            "rcon.print(helpers.table_to_json(position))"
        ))
        if result.get("reachable") is True:
            self._note("approaches_skipped_in_reach")
            return
        self.move_to(Position(**result))

    def place_entity(self, prototype: Any, position: Any, direction: Any,
                     exact: bool = False) -> Any:
        from fle.env import Position

        name = prototype.value[0]
        target = self.position(position)
        direction_value = direction.value
        if not exact:
            site = self.call("find_build_site", name, target, 8)
            target, direction_value = site["position"], site["direction"]
        self.approach_build(Position(**target))
        result = self.call("place", name, target, direction_value)
        return SimpleNamespace(
            name=result["name"], position=Position(**result["position"]),
            drop_position=Position(**result["drop_position"]) if result.get("drop_position") else None,
        )

    def insert_item(self, prototype: Any, entity: Any, quantity: int) -> int:
        self.approach(entity.position, entity.name)
        return self.call("insert", entity.name, self.position(entity.position),
                         prototype.value[0], quantity)["quantity"]

    @staticmethod
    def _connection_corridor(start: dict, end: dict, *, horizontal_first: bool) -> list[tuple[int, int, int, int]]:
        """Bound native placement discovery to one collision-aware L corridor.

        Scanning the complete rectangle between two distant endpoints makes a
        narrow, ordinary pole route depend on its bounding-box area.  The
        corridor keeps the search bounded while retaining eight cells of local
        detour room around each leg.  A caller can try the opposite L without
        widening either search into a remote-placement shortcut.
        """
        source_x, source_y = math.floor(start["x"]), math.floor(start["y"])
        target_x, target_y = math.floor(end["x"]), math.floor(end["y"])
        left, right = min(source_x, target_x) - 8, max(source_x, target_x) + 8
        top, bottom = min(source_y, target_y) - 8, max(source_y, target_y) + 8
        if horizontal_first:
            return [
                (left, right, source_y - 8, source_y + 8),
                (target_x - 8, target_x + 8, top, bottom),
            ]
        return [
            (source_x - 8, source_x + 8, top, bottom),
            (left, right, target_y - 8, target_y + 8),
        ]

    def _connection_cells(self, name: str, fluid: str,
                          rectangles: list[tuple[int, int, int, int]]) -> tuple[set, set]:
        searched = sum((right - left + 1) * (bottom - top + 1)
                       for left, right, top, bottom in rectangles)
        if searched > 16_384:
            raise ValueError("Connection search exceeds bounded area")
        loops = "".join(
            f"for horizontal={left},{right} do for vertical={top},{bottom} do "
            "include(horizontal, vertical) end end; "
            for left, right, top, bottom in rectangles
        )
        cells = json.loads(self.command(
            "local player = storage.fair.actor(); local result = {buildable={}, existing={}}; "
            "local seen = {}; local function include(horizontal, vertical) "
            "local key = horizontal .. ':' .. vertical; if seen[key] then return end; seen[key] = true; "
            "local position = {x=horizontal+0.5,y=vertical+0.5}; "
            "local entity = player.surface.find_entity(" + json.dumps(name) + ", position); "
            "if entity and entity.force == player.force then "
            "local contents = #entity.fluidbox > 0 and entity.fluidbox[1]; "
            "if not contents or contents.name == " + json.dumps(fluid) + " then "
            "table.insert(result.existing, position) end "
            "elseif player.surface.can_place_entity{name=" + json.dumps(name)
            + ", position=position, force=player.force, build_check_type=defines.build_check_type.manual} "
            "then table.insert(result.buildable, position) end; end; "
            + loops + "rcon.print(helpers.table_to_json(result))"
        ))
        return (
            {(point["x"], point["y"]) for point in cells["buildable"]},
            {(point["x"], point["y"]) for point in cells["existing"]},
        )

    def connect(self, source: Any, target: Any, prototype: Any, fluid: str = "") -> None:
        from fle.env import Direction, Position
        from ..planning.connections import (
            select_pole_positions,
            shortest_pipe_path,
            shortest_wire_path,
        )

        name = prototype.value[0]
        if name not in {"pipe", "small-electric-pole"}:
            raise ValueError("Unsupported fair connection type")
        start = self.position(getattr(source, "position", source))
        end = self.position(getattr(target, "position", target))
        route = None
        route_error = None
        for horizontal_first in (True, False):
            buildable, existing = self._connection_cells(
                name, fluid, self._connection_corridor(
                    start, end, horizontal_first=horizontal_first,
                ),
            )
            origin, destination = (start["x"], start["y"]), (end["x"], end["y"])
            if name == "small-electric-pole":
                candidates = buildable | existing
                if not candidates:
                    route_error = ValueError("No ordinary pole placement cells")
                    continue
                origin = min(candidates, key=lambda point: math.dist(point, origin))
                destination = min(candidates, key=lambda point: math.dist(point, destination))
                if math.dist(origin, (start["x"], start["y"])) > 3.5 or math.dist(
                    destination, (end["x"], end["y"])
                ) > 3.5:
                    route_error = ValueError("No nearby ordinary pole placement")
                    continue
            try:
                if name == "small-electric-pole":
                    route = shortest_wire_path(
                        origin, destination, buildable, existing, max_wire_distance=6
                    )
                else:
                    route = shortest_pipe_path(origin, destination, buildable, existing)
                break
            except ValueError as error:
                route_error = error
        if route is None:
            raise ConnectionPreflightRejected("no_connection_route")
        if name == "small-electric-pole":
            route = select_pole_positions(route, max_wire_distance=6)
        required = sum(point not in existing for point in route)
        available = json.loads(self.command(
            "rcon.print(helpers.table_to_json({count=storage.fair.actor().get_item_count("
            + json.dumps(name) + ")}))"
        ))["count"]
        if available < required:
            raise ConnectionPreflightRejected("insufficient_connection_materials")
        for horizontal, vertical in route:
            if (horizontal, vertical) not in existing:
                self.place_entity(prototype, Position(x=horizontal, y=vertical),
                                  direction=Direction.UP, exact=True)
