"""Read-only relevance check for selected native research and the rocket goal.

This proves neither a future commitment nor a net material or coal deficit.
It is a prerequisite predicate for a later receipt-bound admission protocol.
"""
from __future__ import annotations

from ..coal_economic_observation import NativeEconomics
from ..memory import CampaignMemory
from ..state import GameSnapshot
from .catalog import Catalog
from .economics import capability_technology

MAX_TECHNOLOGIES = 512
MAX_PREREQUISITES = 32


def _path_to(graph: dict, root: str, selected: str) -> tuple[str, ...] | None:
    """Traverse a fresh bounded graph; malformed, missing or cyclic links fail."""
    visiting: set[str] = set()
    paths: dict[str, tuple[str, ...] | None] = {}
    visited = 0

    def visit(name: str) -> tuple[str, ...] | None:
        nonlocal visited
        if name in visiting:
            raise ValueError('cyclic_or_oversized_technology_graph')
        if name in paths:
            return paths[name]
        visited += 1
        if visited > MAX_TECHNOLOGIES:
            raise ValueError('cyclic_or_oversized_technology_graph')
        row = graph.get(name)
        if (not isinstance(row, dict) or row.get('enabled') is not True
                or not isinstance(row.get('prerequisites'), list)
                or len(row['prerequisites']) > MAX_PREREQUISITES):
            raise ValueError('unsupported_technology_graph')
        parents = row['prerequisites']
        if (any(not isinstance(parent, str) or not parent or parent not in graph
                for parent in parents) or len(set(parents)) != len(parents)):
            raise ValueError('unsupported_technology_graph')
        visiting.add(name)
        found = (name,) if name == selected else None
        for parent in sorted(parents):
            child = visit(parent)
            if found is None and child is not None:
                found = (name, *child)
        visiting.remove(name)
        paths[name] = found
        return found

    return visit(root)


def evaluate(snapshot: GameSnapshot, memory: CampaignMemory, catalog: Catalog,
             native: NativeEconomics) -> dict:
    """Return current-goal alignment only; every result denies mutation authority."""
    result = {'schema': 'jev.coal-goal-alignment.v1', 'relevant': False,
              'reason': 'unqualified_inputs', 'technology': '', 'path': [],
              'future_commitment': False, 'mutation_authorized': False}
    try:
        if (not isinstance(snapshot, GameSnapshot) or not isinstance(memory, CampaignMemory)
                or not isinstance(catalog, Catalog) or not isinstance(native, NativeEconomics)
                or catalog.version != '2.0.77' or snapshot.game_version != '2.0.77'
                or snapshot.world_kind != 'fle' or memory.session_id != snapshot.session_id
                or memory.last_tick != snapshot.tick or memory.target != 'rocket_launch'
                or memory.active_goal != 'rocket_launch' or memory.status != 'running'
                or getattr(memory, 'coal_economic_admission', False) is not True
                or getattr(memory, 'coal_kit_policy', False) is not True
                or memory.pending is not None or memory.attempt is not None):
            return result
        runtime = snapshot.factory.get('acceptance_runtime')
        epoch = native.epoch
        if (not isinstance(runtime, dict) or runtime.get('session_id') != snapshot.session_id
                or runtime.get('tick') != snapshot.tick
                or type(runtime.get('actor_unit')) is not int
                or runtime['actor_unit'] != native.actor_unit
                or epoch.session_id != snapshot.session_id or epoch.tick != snapshot.tick
                or epoch.actor_index != runtime.get('player_index')
                or epoch.surface_index != runtime.get('surface_index')
                or epoch.force_index != runtime.get('force_index')
                or native.material_scope is None
                or native.material_scope.closure_complete is not False
                or native.mutation_authorized is not False
                or native.native_payback_proven is not False):
            result['reason'] = 'native_epoch_or_scope_changed'
            return result
        work = native.research_work
        if work is None or snapshot.factory.get('research') != work.technology:
            result['reason'] = 'selected_research_unavailable'
            return result
        result['technology'] = work.technology
        if (not isinstance(snapshot.researched, list)
                or work.technology in snapshot.researched
                or 'rocket-silo' in snapshot.researched):
            result['reason'] = 'research_already_complete_or_unknown'
            return result
        selected = catalog.technologies.get(work.technology)
        if (not isinstance(selected, dict) or selected.get('trigger')
                or not isinstance(selected.get('ingredients'), list)):
            result['reason'] = 'selected_technology_unsupported'
            return result
        bill = selected['ingredients']
        if (len(bill) != len(work.ingredients)
                or any(not isinstance(item, dict)
                       or set(item) not in ({'name', 'amount'}, {'name', 'amount', 'type'})
                       or item.get('type', 'item') != 'item' for item in bill)
                or sorted((item['name'], item['amount']) for item in bill)
                   != list(work.ingredients)):
            result['reason'] = 'selected_technology_bill_changed'
            return result
        anchors = [('rocket_silo_prerequisite', 'rocket-silo')]
        capability = capability_technology(catalog, snapshot.researched)
        if capability is not None:
            anchors.append(('assembler_capability_prerequisite', capability))
        for route, root in anchors:
            path = _path_to(catalog.technologies, root, work.technology)
            if path is not None:
                result.update(relevant=True, reason=route, path=list(path))
                return result
        result['reason'] = 'selected_research_unrelated_to_goal'
        return result
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        result['reason'] = 'unsupported_goal_graph_or_binding'
        return result
