"""Add mining capacity for legacy manual cells, preserving those cells in place."""
from __future__ import annotations

import math
from dataclasses import replace

from ..mining_outposts import (COMMAND, PARTS, RESOURCES, current, flow_complete,
                               remaining_kit, role, sources)
from .input_routes import InputRoutePlanner
from .research_trigger import current_machine_input_requirement, current_trigger
from .service_visits import service_visit

# First actions a direct (non-investment) alternative may take; passive waits never qualify.
DIRECT_ALTERNATIVE_ACTIONS = frozenset({'factory_gather', 'factory_insert', 'factory_extract', 'factory_craft'})


class MiningOutpostPlanner(InputRoutePlanner):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._outpost_acquiring = False
        # Plan id -> the (item, amount, path) whose proposed-outpost investment it answers.
        self._proposed_outposts = {}

    def _acquire_outpost(self, item, amount, path):
        previous = (self._outpost_acquiring, self._acquiring_route,
                    getattr(self, '_economic_acquiring', False))
        self._outpost_acquiring = self._acquiring_route = self._economic_acquiring = True
        try:
            # The paid outpost kit is a separate bounded objective. Its drill
            # can require plates from the producer whose ore need prompted the
            # outpost; retaining that outer path falsely makes the drill's
            # legitimate plate prerequisite a production cycle. While paying
            # for the kit, neither a nested route nor a speculative investment
            # may replace its current manual/output-buffer-aware prerequisite.
            return super()._need(item, amount, ())
        finally:
            self._outpost_acquiring, self._acquiring_route, self._economic_acquiring = previous

    def _current_research_trigger_input(self, item, amount, path):
        context = self._active_native_research_trigger
        if not isinstance(context, dict):
            return False
        current = current_trigger(self.snapshot, self.catalog,
                                  context.get('technology'), context.get('outer_recipe'))
        if current != context:
            return False
        item_path = [entry.removeprefix('item:') for entry in path
                     if isinstance(entry, str) and entry.startswith('item:')]
        if item_path[-1:] != [current['trigger_item']]:
            return False
        typed_path = [current['outer_recipe'], current['trigger_item'], item]
        need = current_machine_input_requirement(
            self.snapshot, self.catalog, current, item, typed_path)
        return (isinstance(need, dict)
                and type(amount) is int
                and amount == need['machine_input_units_required_now'])

    def _need(self, item, amount, path=()):
        if (item not in RESOURCES or self._outpost_acquiring or self.goal != 'rocket_launch'
                or self.snapshot.inventory.get(item, 0) >= amount):
            return super()._need(item, amount, path)
        requested_amount = math.ceil(amount)
        shortage = requested_amount - self.snapshot.inventory.get(item, 0)
        row = sources(self.snapshot).get(item)
        if not row or row['state'] == 'fault':
            return super()._need(item, amount, path)
        if row['state'] == 'proposed':
            # A finite, current craft-item technology trigger is an immediate
            # production demand. Do not start the whole speculative outpost kit
            # when its enabled recipe input can be gathered or transferred
            # directly. Paid/building prefixes retain their existing path.
            if self._current_research_trigger_input(item, amount, path):
                return super()._need(item, amount, path)
            machine = self.entities.get(RESOURCES[item], {})
            direct = self.factory.get('input_routes', {}).get('sources', {}).get(RESOURCES[item])
            # Small bootstrap work and an available direct route take precedence.
            # Only invest for an established manually supplied producer.
            if (direct or machine.get('products_finished', 0) < 20 or shortage < 10):
                return super()._need(item, amount, path)
            # Collect already-paid legacy ore rather than hiding it behind an investment.
            if any(e.get('output', {}).get(item, 0) for e in self.entities.values()):
                return super()._need(item, amount, path)
        if self.focus is None:
            self._set_focus(item, amount)
        kit = remaining_kit(row)
        if kit:
            self._buffer_service = True  # Keep kit acquisition/construction on the primary path.
            for name, count in {**kit, 'coal': 5}.items():
                prerequisite = self._acquire_outpost(name, count, path)
                if prerequisite:
                    # Keep the outpost's parent demand separate from the
                    # bounded child-kit request. _acquire_outpost deliberately
                    # resets its recursive path so a kit recipe may use an
                    # existing producer whose raw input is the outpost's own
                    # resource. Do not rewrite that child path as if it were
                    # the outer consumer's direct recipe chain.
                    parent_path = [entry.removeprefix('item:') for entry in path
                                   if entry.startswith('item:')]
                    if parent_path[-1:] != [item]:
                        parent_path.append(item)
                    producer = self.entities.get(RESOURCES[item], {})
                    output_ready = any(
                        isinstance(entity.get('output'), dict)
                        and entity['output'].get(item, 0) > 0
                        for entity in self.entities.values())
                    if name in PARTS.values():
                        child_kind = 'outpost_component'
                    else:
                        child_kind = 'outpost_construction_fuel'
                    if row['state'] == 'proposed':
                        admission = {
                            'classification': 'existing_proposed_outpost_policy_heuristic',
                            'state_at_admission': row['state'],
                            'source_role': RESOURCES[item],
                            'source_unit': producer.get('unit_number'),
                            'products_finished': producer.get('products_finished'),
                            'minimum_products_finished': 20,
                            'direct_input_route_absent': not bool(direct),
                            'shortage_now': shortage,
                            'minimum_shortage': 10,
                            'paid_output_absent': not output_ready,
                            'native_outpost_payback_observed': False,
                            'basis': 'current_planner_direct_route_and_minimum_runway_policy',
                        }
                    else:
                        admission = {
                            'classification': 'current_paid_outpost_prefix_continuation',
                            'state_at_admission': row['state'],
                            'paid_parts': sorted(row['parts']),
                            'current_paid_prefix': bool(row['parts']) and current(row, self.snapshot),
                            'native_outpost_payback_observed': False,
                            'basis': 'current_validated_outpost_component_receipts',
                        }
                    materials = dict(prerequisite.materials or {})
                    materials['outpost_kit_prerequisite'] = {
                        'schema': 1,
                        'observed_tick': self.snapshot.tick,
                        'outpost_resource': item,
                        'outpost_layout': row['layout'],
                        'outpost_remaining': row['remaining'],
                        'parent_request': {
                            'item': item,
                            'amount': requested_amount,
                            'inventory_now': self.snapshot.inventory.get(item, 0),
                            'planner_item_path': parent_path,
                            'local_target_item': self.focus[0] if self.focus else item,
                        },
                        'child_request': {
                            'item': name,
                            'quantity': count,
                            'kind': child_kind,
                        },
                        'admission': admission,
                    }
                    prerequisite = replace(prerequisite, materials=materials)
                    return prerequisite
            spec = next(s for s in row['steps'] if s['part'] not in row['parts'])
            plan = self._plan(COMMAND, 'outpost_component', parameters={
                'resource': item, 'layout': row['layout'], 'part': spec['part'],
                'receipt': f"{self.snapshot.tick}:{row['layout']}:{spec['part']}",
            }, costs={**kit, 'coal': 5}, timeout=18000,
                identity=f"{row['layout']}:{spec['part']}",
                description=f"Build paid {spec['name']} for {item} mining outpost; preserve existing furnace")
            if row['state'] == 'proposed':
                # The planner chose this speculative investment by policy over the
                # direct path; remember the request so the alternative can be offered.
                self._proposed_outposts[plan.id] = {
                    'item': item, 'amount': requested_amount, 'path': tuple(path)}
            return plan
        if not row['topology']:
            self._buffer_service = True
            return self._wait('outpost_flow', row['layout'], 3, item, timeout=1800,
                              identity=f"commission:{row['layout']}")
        chest = role(item, 'chest')
        available = self.entities[chest].get('output', {}).get(item, 0)
        missing = math.ceil(amount - self.snapshot.inventory.get(item, 0))
        # A certified, currently owned output is already paid for. Preserve the
        # existing bounded collection/tail policy, but do not make its collection
        # depend on upstream fuel that is unnecessary for this request.
        target = min(50, missing, available + row['remaining'])
        commissioned = flow_complete(item, row['layout'], self.snapshot)
        if commissioned and available >= target and available:
            plan = self._transfer(chest, item, min(50, available, max(missing, self.collection_batch)), extracting=True)
            return replace(plan, materials={**(plan.materials or {}), 'maintenance_policy': {
                'schema': 1, 'reason': 'collect_ready_owned_outpost_before_upstream_refill',
                'observed_tick': self.snapshot.tick, 'required': missing,
                'ready': available, 'source': RESOURCES[item],
            }})
        drill = self.entities[role(item, 'drill')]
        if row['remaining'] and drill.get('fuel', {}).get('coal', 0) < 2:
            self._buffer_service = True
            from .fuel_service import service_plan
            return service_plan(self, role(item, 'drill'), None, path, self._acquire_outpost)
        if not commissioned:
            self._buffer_service = True
            return self._wait('outpost_flow', row['layout'], 3, item, timeout=3600,
                              identity=f"commission:{row['layout']}")
        if row['remaining']:
            return self._wait('machine_output', item, target, chest, timeout=18000,
                              identity=f"outpost-collect:{row['layout']}:{target}")
        return super()._need(item, amount, path)

    def _with_direct_alternative(self, plans):
        """Offer the direct path next to a lone policy-chosen outpost investment.

        An infrastructure primary is otherwise the whole frontier, so declining a
        speculative investment left nothing to do. The alternative is the plan the
        same request yields with the investment policy bypassed, exactly as the
        policy's own early return computes it. Only a still-proposed outpost
        qualifies; a started paid prefix remains the sole candidate. Nothing here
        lowers a gate or grants execution: the model still judges both plans and
        native preconditions still decide dispatch.
        """
        if len(plans) != 1:
            return plans
        primary = plans[0]
        context = self._proposed_outposts.get(primary.id)
        if context is None or primary.steps[0].action != COMMAND:
            return plans
        worker = self._candidate_worker()
        try:
            alternative = InputRoutePlanner._need(
                worker, context['item'], context['amount'], context['path'])
        except (KeyError, ValueError):
            return plans
        if (alternative is None or alternative.id == primary.id
                or alternative.steps[0].action not in DIRECT_ALTERNATIVE_ACTIONS
                or not alternative.steps[0].allowed(self.snapshot)
                or alternative.steps[0].satisfied(self.snapshot)):
            return plans
        alternative = replace(alternative, materials={
            **(alternative.materials or {}),
            'direct_alternative_to_proposed_outpost': {
                'schema': 1, 'observed_tick': self.snapshot.tick,
                'resource': context['item'], 'requested_amount': context['amount'],
                'investment_plan_id': primary.id,
                'basis': 'planner_policy_investment_has_no_native_payback_evidence'}})
        return [*plans, service_visit(self, alternative)]

    def candidates(self):
        plans = self._with_direct_alternative(super().candidates())
        evidence = {key: {name: row[name] for name in ('layout', 'state', 'remaining', 'topology', 'flow')}
                    for key, row in sources(self.snapshot).items()}
        return [replace(plan, materials={**(plan.materials or {}), 'mining_outposts': evidence,
                                        'ore_transport': 'bounded_manual_hauling'}) for plan in plans]
