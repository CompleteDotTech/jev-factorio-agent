"""Wire snapshots produced by the Lua API double, not independently invented rows."""
from copy import deepcopy
from test_coal_supply_lua import runtime
from solid_routes_fixtures import fixture as base
from jev_factorio import coal_supply as coal, solid_routes as solid

TARGETS = ["alpha", "beta"]
INTENTS = coal.intents(TARGETS)


def plain(value):
    if hasattr(value, 'items') and not isinstance(value, dict):
        keys = list(value.keys())
        if keys and all(type(k) is int for k in keys) and set(keys) == set(range(1, len(keys)+1)):
            return [plain(value[i]) for i in range(1, len(keys)+1)]
        return {k: plain(v) for k, v in value.items()}
    return value


def snapshot(lua):
    s=base();native=plain(lua.eval('campaign.observe()'))
    s.session_id='solid-fixture';s.tick=lua.eval('game.tick')
    s.inventory=plain(lua.globals().stock)
    s.factory=native
    return s


def fixture():
    lua=runtime();return snapshot(lua)


def full():
    lua=runtime();lua.execute('coal_all();coal_pulse();coal_pulse();coal_pulse()')
    return snapshot(lua)


def paid_source(s, parameters):
    rows=s.factory['coal_supply']['sources'];row=rows[parameters['target']]
    assert coal.allowed(parameters,s)
    s.factory['coal_supply']['committed']=True
    for r in rows.values(): r['state']='building'
    spec=next(v for v in row['steps'] if v['part']==parameters['part'])
    part={'role':coal.role(parameters['target'],parameters['part']),
          'unit_number':max(e['unit_number'] for e in s.factory['entities'].values())+1,
          'receipt':parameters['receipt'],'paid':1}
    row['parts'][parameters['part']]=part
    s.inventory[spec['name']]-=1
    s.factory['entities'][part['role']]={**deepcopy(spec),'unit_number':part['unit_number']}
    row['pending']={}
    if parameters['part']=='chest':
        target=row['target']; key=f"solid:{part['unit_number']}:{target['unit_number']}:coal:fuel"
        source={'role':part['role'],'name':'wooden-chest','position':deepcopy(spec['position']),
                'unit_number':part['unit_number'],'bounds':deepcopy(row['chest_bounds']),'recipe':'','inventory':'chest'}
        s.factory['solid_routes']['routes'][key]={'route':key,'layout':key+':layout','item':'coal',
            'source':source,'target':deepcopy(target),'steps':deepcopy(row['corridor']),
            'parts':{},'state':'proposed','topology':False,'flow':{},'pending':{},'reason':'proposal'}


def paid_corridor(s, parameters):
    r=s.factory['solid_routes']['routes'][parameters['route']]
    assert solid.allowed(parameters,s)
    spec=next(v for v in r['steps'] if v['part']==parameters['part'])
    unit=max(e['unit_number'] for e in s.factory['entities'].values())+1
    name=r['route']+':'+spec['part']
    r['parts'][spec['part']]={'role':name,'unit_number':unit,'receipt':parameters['receipt'],'paid':1}
    s.factory['entities'][name]={'unit_number':unit,'name':spec['name'],'position':deepcopy(spec['position'])}
    s.inventory[spec['name']]-=1
    r['pending']={};r['state']='ready' if len(r['steps'])==len(r['parts']) else 'building'
    r['topology']=r['state']=='ready'
    s.factory['coal_supply']['sources'][r['target']['role']]['route']=r['route']
