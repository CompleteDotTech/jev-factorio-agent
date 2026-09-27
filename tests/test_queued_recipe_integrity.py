"""A duplicate ingredient cannot fund two queued batches or hide a shortage."""
from copy import deepcopy
import pytest
from jev_factorio.planning.demand import SupplyLedger, uncommitted_input
from test_solid_investment import scenario, offers, service_history
from solid_routes_fixtures import TARGET


@pytest.mark.parametrize('extra_amount', [1, 2, 4])
@pytest.mark.parametrize('entry_point', ['ledger', 'input', 'offers'])
def test_duplicate_ingredient_rejected_consistently(extra_amount, entry_point):
    state, catalog = scenario()
    target = state.factory['entities'][TARGET]
    target['input']['iron-gear-wheel'] = 60
    recipe = catalog.recipes['automation-science-pack']
    duplicate = deepcopy(next(i for i in recipe['ingredients'] if i['name'] == 'iron-gear-wheel'))
    duplicate['amount'] = extra_amount
    recipe['ingredients'].append(duplicate)
    if entry_point == 'offers':
        selected, _ = offers(state, catalog, service_history(state))
        assert selected == []
    else:
        with pytest.raises(ValueError, match='[Dd]uplicate|[Aa]mbiguous'):
            if entry_point == 'ledger':
                SupplyLedger.capture(state, catalog)
            else:
                uncommitted_input(target, catalog, 'iron-gear-wheel')
