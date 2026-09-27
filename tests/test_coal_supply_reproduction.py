"""Regression: the real contract must expose an explicit paid coal source action."""
from jev_factorio.factory_contract import validate_command


def test_paid_coal_source_action_is_a_real_factory_command():
    validate_command("factory_coal_build", {
        "target": "utility:boiler", "layout": "coal-network:1",
        "part": "chest", "receipt": "coal-fixture:chest",
    })
