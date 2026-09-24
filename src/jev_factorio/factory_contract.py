"""Typed factory commands and observation-only completion predicates."""
from __future__ import annotations

import math

from .state import GameSnapshot
from . import launch_readiness
from . import output_buffers, input_routes, production_sites, mining_outposts, successors

COMMAND_FIELDS = {
    **launch_readiness.COMMANDS,
    mining_outposts.COMMAND: mining_outposts.FIELDS,
    output_buffers.COMMAND: output_buffers.FIELDS,
    input_routes.COMMAND: input_routes.FIELDS,
    "factory_bind": set(),
    "factory_explore": {"radius"},
    "factory_gather": {"resource", "quantity"},
    "factory_craft": {"recipe", "batches"},
    "factory_craft_job": {"recipe", "batches", "receipt"},
    "factory_place": {"role", "name", "anchor"},
    "factory_configure": {"role", "recipe"},
    "factory_insert": {"role", "item", "quantity", "receipt"},
    "factory_extract": {"role", "item", "quantity", "receipt"},
    "factory_connect": {"source", "target", "kind", "fluid"},
    "factory_research": {"technology"},
    "factory_launch": {"role"},
    "factory_wait": set(),
}
EFFECTS = {
    *launch_readiness.EFFECTS,
    "successor_route_available", "player_bound", "machine", "machine_recipe", "machine_input", "machine_fuel",
    "machine_output", "connection", "research_started", "researched", "research_progress",
    "crafting_idle", "rocket_ready", "rocket_parts", "rocket_launched", "produced", "transfer",
    "explored", "powered", "craft_job_complete", *mining_outposts.EFFECTS, *output_buffers.EFFECTS, *input_routes.EFFECTS,
}


def validate_command(action: str, parameters: dict) -> None:
    if action in launch_readiness.COMMANDS:
        launch_readiness.validate(action, parameters)
        return
    if action == mining_outposts.COMMAND:
        mining_outposts.validate(parameters)
        return
    if action == input_routes.COMMAND:
        input_routes.validate(parameters)
        return
    if action == output_buffers.COMMAND:
        output_buffers.validate(parameters)
        return
    if action not in COMMAND_FIELDS or not isinstance(parameters, dict):
        raise ValueError("Unknown factory command")
    if set(parameters) != COMMAND_FIELDS[action]:
        raise ValueError("Factory command fields do not match its contract")
    for key, value in parameters.items():
        if key == "radius":
            if type(value) is not int or not 1 <= value <= 32:
                raise ValueError("Exploration radius must be in [1, 32]")
        elif key in {"quantity", "batches"}:
            if type(value) is not int or not 1 <= value <= 200:
                raise ValueError("Factory batch must be an integer in [1, 200]")
        elif not isinstance(value, str) or not value or len(value) > 128:
            raise ValueError("Factory identifiers must be bounded strings")
    if action == "factory_connect" and parameters["kind"] not in {"pipe", "small-electric-pole"}:
        raise ValueError("Unsupported factory connection type")


def connected(factory: dict, source: str, target: str, kind: str, fluid: str) -> bool:
    entities = factory.get("entities", {})
    before, after = entities.get(source, {}), entities.get(target, {})
    if not before or not after:
        return False
    if kind == "small-electric-pole":
        network = before.get("electric_network_id")
        return bool(network) and network == after.get("electric_network_id")

    def segments(entity):
        return {port["id"] for port in entity.get("fluid_ports", [])
                if port.get("id") and port.get("fluid", "") in {"", fluid}}

    return bool(segments(before) & segments(after))


def satisfied(effect: str, item: str, threshold: float, parameters: dict, snapshot: GameSnapshot,
              action: str = "") -> bool:
    if effect in launch_readiness.EFFECTS:
        return launch_readiness.satisfied(effect, action, parameters, snapshot)
    if effect == "successor_route_available":
        return action == "factory_wait" and parameters.get("role") in input_routes.sources(snapshot)
    if effect == "outpost_component":
        return action == mining_outposts.COMMAND and mining_outposts.component_complete(parameters, snapshot)
    if effect == "outpost_flow":
        return action == "factory_wait" and mining_outposts.flow_complete(parameters.get("role", ""), item, snapshot)
    if effect == "input_component":
        return action == input_routes.COMMAND and input_routes.component_complete(parameters, snapshot)
    if effect == "input_flow":
        return action == "factory_wait" and input_routes.flow_complete(parameters.get("role", ""), item, snapshot)
    if effect == "buffer_component":
        return action == output_buffers.COMMAND and output_buffers.component_complete(parameters, snapshot)
    if effect == "buffer_flow":
        return action == "factory_wait" and output_buffers.flow_complete(parameters.get("role", ""), item, snapshot)
    factory = snapshot.factory
    entities = factory.get("entities", {})
    machine = entities.get(parameters.get("role", ""), {})
    if effect == "craft_job_complete":
        from .craft_jobs import craft_complete

        return action == "factory_craft_job" and craft_complete(parameters, snapshot)
    if effect == "player_bound":
        return factory.get("player_bound") is True
    if effect == "explored":
        return factory.get("exploration_radius", 0) >= threshold
    if effect == "machine":
        if parameters.get("anchor", "").startswith("cell-site:"):
            return action == "factory_place" and production_sites.complete(parameters, snapshot)
        return machine.get("name") == parameters["name"]
    if effect == "machine_recipe":
        return machine.get("recipe") == parameters["recipe"]
    if effect == "powered":
        return machine.get("energy", 0) > 0
    if effect in {"machine_input", "machine_output", "machine_fuel"}:
        return machine.get(effect.removeprefix("machine_"), {}).get(item, 0) >= threshold
    if effect == "connection":
        return connected(factory, **parameters)
    if effect == "transfer":
        receipt = factory.get("receipts", {}).get(parameters["receipt"], {})
        return (receipt.get("role") == parameters["role"]
                and action in {"factory_insert", "factory_extract"}
                and receipt.get("extracting") is (action == "factory_extract")
                and receipt.get("unit_number") == machine.get("unit_number")
                and receipt.get("item") == parameters["item"]
                and receipt.get("quantity") == parameters["quantity"])
    if effect == "research_started":
        return factory.get("research") == item or item in (snapshot.researched or [])
    if effect == "researched":
        return item in (snapshot.researched or [])
    if effect == "research_progress":
        return item in (snapshot.researched or []) or (
            factory.get("research") == item and factory.get("research_progress", 0) >= threshold
        )
    if effect == "crafting_idle":
        return factory.get("crafting_queue", math.inf) == 0
    if effect == "rocket_ready":
        return machine.get("rocket_ready") is True
    if effect == "rocket_parts":
        return machine.get("rocket_parts", 0) >= threshold or machine.get("rocket_ready") is True
    if effect == "rocket_launched":
        return snapshot.victory is True and snapshot.victory_source == "native:base-game-rocket-launch"
    if effect == "produced":
        return factory.get("produced", {}).get(item, 0) >= threshold
    raise ValueError(f"Unknown factory effect: {effect}")


def allowed(action: str, parameters: dict, snapshot: GameSnapshot) -> bool:
    validate_command(action, parameters)
    if "successors" in snapshot.factory:
        try:
            if not successors.permits(action, parameters, snapshot):
                return False
        except (ValueError, TypeError, KeyError, AttributeError):
            return False
    elif parameters.get('role') in successors.ROLES or parameters.get('source') in successors.ROLES:
        return False
    if "mining_outposts" in snapshot.factory:
        try:
            if not mining_outposts.permits(action, parameters, snapshot):
                return False
        except (ValueError, TypeError, KeyError):
            return False
    if "input_routes" in snapshot.factory:
        try:
            if not input_routes.permits(action, parameters, snapshot):
                return False
        except (ValueError, TypeError, KeyError):
            return False
    if action == input_routes.COMMAND:
        return input_routes.allowed(parameters, snapshot)
    if action == output_buffers.COMMAND:
        return output_buffers.allowed(parameters, snapshot)
    if "output_buffers" in snapshot.factory:
        try:
            if not output_buffers.permits(action, parameters, snapshot):
                return False
        except ValueError:
            return False
    if action == mining_outposts.COMMAND:
        return mining_outposts.allowed(parameters, snapshot)
    if action in launch_readiness.COMMANDS:
        return launch_readiness.allowed(action, parameters, snapshot)
    if action == "factory_launch":
        return parameters.get("role") == launch_readiness.SILO and launch_readiness.ready(snapshot)
    if action == "factory_insert" and not launch_readiness.affordable(action, {parameters["item"]: parameters["quantity"]}, snapshot):
        return False
    factory = snapshot.factory
    entities = factory.get("entities", {})
    machine = entities.get(parameters.get("role", ""), {})
    if not factory:
        return False
    if action == "factory_bind":
        return factory.get("player_connected") is True
    if action == "factory_gather":
        return parameters["resource"] in snapshot.nearby_resources
    if action == "factory_place":
        if parameters["anchor"].startswith("cell-site:") and not production_sites.allowed(parameters, snapshot):
            return False
        return not machine and snapshot.inventory.get(parameters["name"], 0) >= 1
    if action == "factory_craft_job":
        return (type(factory.get("craft_jobs_protocol")) is int
                and factory["craft_jobs_protocol"] == 1
                and allowed("factory_craft", {key: parameters[key] for key in ("recipe", "batches")}, snapshot))
    if action == "factory_craft":
        return (factory.get("player_connected") is True
                and factory.get("player_bound") is True and factory.get("crafting_queue") == 0)
    if action in {"factory_insert", "factory_extract", "factory_configure", "factory_launch"}:
        if not machine:
            return False
        if action == "factory_insert":
            return snapshot.inventory.get(parameters["item"], 0) >= parameters["quantity"]
        if action == "factory_extract":
            return machine.get("output", {}).get(parameters["item"], 0) >= parameters["quantity"]
    if action == "factory_connect":
        return parameters["source"] in entities and parameters["target"] in entities
    if action == "factory_research":
        return factory.get("research", "") in {"", parameters["technology"]}
    return True
