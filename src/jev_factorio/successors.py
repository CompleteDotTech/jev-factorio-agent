"""Strict additive-producer evidence. A paid furnace is not a useful factory."""
from __future__ import annotations

import math

ROLES = {'growth:iron-plate': 'iron-ore', 'growth:copper-plate': 'copper-ore'}
PHASES = {'reserved', 'output_building', 'output_commissioning', 'input_building', 'producing', 'preferred'}
USE_RECIPES = {'iron-gear-wheel', 'copper-cable', 'electronic-circuit',
               'automation-science-pack', 'logistic-science-pack'}
MARKER = 'successor_project'
MAX_PROJECT_TICKS = 216000
TRIAL_LIMIT = 200
SCIENCE_RESERVE = 20


def integer(value, minimum=0, maximum=2**53-1):
    return type(value) is int and minimum <= value <= maximum


def text(value):
    return isinstance(value, str) and 0 < len(value) <= 128


def sources(snapshot) -> dict:
    data = snapshot.factory.get('successors')
    if (not isinstance(data, dict) or not integer(data.get('protocol'), 1, 1)
            or data.get('session_id') != snapshot.session_id or not integer(data.get('tick'))
            or data['tick'] != snapshot.tick or not isinstance(data.get('sources'), dict)
            or set(data['sources']) - ROLES.keys()):
        raise ValueError('Missing or stale successor evidence')
    entities = snapshot.factory.get('entities', {})
    for role, row in data['sources'].items():
        item = role[7:]
        if (not isinstance(row, dict) or row.get('source') != role or row.get('item') != item
                or row.get('predecessor') != 'recipe:' + item or not text(row.get('anchor'))
                or not row['anchor'].startswith('cell-site:') or row.get('phase') not in PHASES
                or not integer(row.get('predecessor_unit'), 1) or not integer(row.get('source_unit'))
                or not integer(row.get('paid'), 0, 1) or not integer(row.get('seeded'), 0, 10)
                or not integer(row.get('trial_collected')) or not integer(row.get('credit'))
                or not integer(row.get('attribution_resets')) or not integer(row.get('started_tick'))
                or row['started_tick'] > snapshot.tick or not integer(row.get('remaining_ore'))
                or not isinstance(row.get('use'), dict) or not isinstance(row.get('qualification'), dict)
                or row.get('fault')):
            raise ValueError('Invalid or faulted successor evidence')
        old = entities.get(row['predecessor'], {})
        if old.get('unit_number') != row['predecessor_unit'] or old.get('name') != 'stone-furnace':
            raise ValueError('Successor predecessor identity changed')
        if row['source_unit']:
            entity = entities.get(role, {})
            site = snapshot.factory.get('production_sites', {}).get('sources', {}).get(role, {})
            if (row['source_unit'] == row['predecessor_unit'] or row['paid'] != 1
                    or entity.get('unit_number') != row['source_unit'] or entity.get('name') != 'stone-furnace'
                    or site.get('source_unit') != row['source_unit'] or site.get('anchor') != row['anchor']
                    or site.get('state') != 'owned' or entity.get('position') != site.get('position')):
                raise ValueError('Successor owned producer changed')
        elif row['paid'] or row['phase'] != 'reserved':
            raise ValueError('Unpaid successor cannot progress')
        use = row['use']
        if use and (not text(use.get('job_id')) or use.get('recipe') not in USE_RECIPES
                    or not integer(use.get('quantity'), 3, TRIAL_LIMIT * 200)
                    or use.get('source_unit') != row['source_unit'] or type(use.get('source_unit')) is not int
                    or not text(use.get('input_layout')) or not integer(use.get('completed_tick'))
                    or not row['started_tick'] <= use['completed_tick'] <= snapshot.tick
                    or not integer(use.get('requested'), 1, 200) or type(use.get('finished')) is not int
                    or use['finished'] != use['requested'] or not isinstance(use.get('outputs'), dict)
                    or not use['outputs'] or any(not text(k) or not integer(v, 1) for k, v in use['outputs'].items())):
            raise ValueError('Invalid downstream craft witness')
        proof = row['qualification']
        if proof and (not use or any(not integer(proof.get(key)) for key in
                ('first_tick', 'last_tick', 'positive_samples', 'produced', 'source_unit', 'last_progress_tick', 'max_observation_gap'))
                or proof['source_unit'] != row['source_unit'] or proof.get('input_layout') != use['input_layout']
                or proof.get('use_job_id') != use['job_id'] or proof['positive_samples'] < 3
                or proof['produced'] < 3 or proof['last_tick'] - proof['first_tick'] < 36000
                or not proof['first_tick'] <= proof['last_progress_tick'] <= proof['last_tick']
                or proof['last_tick'] - proof['last_progress_tick'] > 1800 or proof['max_observation_gap'] > 1800
                or not row['started_tick'] <= proof['first_tick'] <= proof['last_tick'] <= snapshot.tick):
            raise ValueError('Invalid successor qualification')
        if row['phase'] == 'preferred' and not proof:
            raise ValueError('Preferred successor lacks qualification')
    return data['sources']


def flowing(role, snapshot) -> bool:
    from .input_routes import flow_complete
    from .output_buffers import flow_complete as output_complete
    route = snapshot.factory.get('input_routes', {}).get('sources', {}).get(role, {})
    output = snapshot.factory.get('output_buffers', {}).get('sources', {}).get(role, {})
    return flow_complete(role, route.get('layout', ''), snapshot) and output_complete(role, output.get('layout', ''), snapshot)


def qualified(role, snapshot) -> bool:
    row = sources(snapshot).get(role)
    if not row or row['phase'] != 'preferred' or not row['qualification'] or not flowing(role, snapshot):
        return False
    route = snapshot.factory['input_routes']['sources'][role]
    return row['qualification']['input_layout'] == route['layout'] and row['use']['input_layout'] == route['layout']


def private_output(role, snapshot) -> bool:
    """Only the successor-aware planner chooses trial/preferred collections."""
    if role in ROLES:
        return True
    for source, row in snapshot.factory.get('output_buffers', {}).get('sources', {}).items():
        if source in ROLES and role == row.get('chest_role'):
            return True
    return False


def permits(action, parameters, snapshot) -> bool:
    rows = sources(snapshot)
    role = parameters.get('role')
    if action == 'factory_place' and role in ROLES:
        site = snapshot.factory.get('production_sites', {}).get('sources', {}).get(role, {})
        old = snapshot.factory.get('entities', {}).get('recipe:' + role[7:], {})
        return bool(parameters.get('anchor') == site.get('anchor') and site.get('state') == 'proposed'
                    and old.get('name') == 'stone-furnace' and old.get('products_finished', 0) >= 20
                    and all(snapshot.inventory.get(k, 0) >= v for k, v in initial_kit(site, ROLES[role]).items()))
    for source, row in rows.items():
        if role == source:
            if action in {'factory_extract', 'factory_configure'}:
                return False
            if action == 'factory_insert' and parameters.get('item') != 'coal':
                return (parameters.get('item') == ROLES[source]
                        and row['seeded'] + parameters.get('quantity', TRIAL_LIMIT) <= 10
                        and source not in snapshot.factory.get('input_routes', {}).get('sources', {}))
        output = snapshot.factory.get('output_buffers', {}).get('sources', {}).get(source, {})
        if role == output.get('chest_role') and action == 'factory_extract':
            return bool(flowing(source, snapshot) and (qualified(source, snapshot)
                        or row['trial_collected'] + parameters['quantity'] <= TRIAL_LIMIT))
    return True


def initial_kit(site, ore) -> dict:
    bill = dict(site.get('bill', {}))
    if not bill or ore not in ROLES.values():
        raise ValueError('Missing successor bill')
    bill['transport-belt'] = bill.get('transport-belt', 0) + SCIENCE_RESERVE
    bill.update(coal=30)
    bill[ore] = 10
    return bill


def project_valid(project, role, tick):
    if (role not in ROLES or not isinstance(project, dict)
            or set(project) != {'anchor', 'predecessor_unit', 'source_unit', 'started_tick', 'deadline_tick', 'status'}
            or not text(project['anchor']) or not project['anchor'].startswith('cell-site:')
            or not integer(project['predecessor_unit'], 1) or not integer(project['source_unit'])
            or not integer(project['started_tick']) or not integer(project['deadline_tick'])
            or not project['started_tick'] <= tick
            or project['deadline_tick'] != project['started_tick'] + MAX_PROJECT_TICKS
            or project['status'] not in {'active', 'qualified', 'paused'}):
        raise ValueError('Invalid retained successor project')
