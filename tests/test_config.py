from pathlib import Path

import pytest
import yaml

from common.config import ConfigError, load_config
from tests.conftest import DEFAULT_CONFIG


def _write(tmp_path: Path, name: str, data: dict) -> Path:
    p = tmp_path / name
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def _default_dict() -> dict:
    return yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))


def test_default_loads_with_guide_numbers(cfg):
    assert cfg.sizes.n_users == 1000 and cfg.sizes.attacks_per_type == 300
    assert cfg.policy.thresholds == [20, 50, 70]
    assert cfg.policy.weights.fri_3 == 35
    assert cfg.costs.friction.as_dict() == {"0": 0, "W": 0.5, "1": 1, "2": 5, "3": 10}
    assert cfg.costs.p_stop.as_dict()["3"]["replay"] == 0.95
    assert cfg.generator.coverage.sim_change == 0.90
    assert cfg.generator.attacks.simswap.ratio_center == 25


def test_unknown_key_is_named(tmp_path):
    d = _default_dict()
    d["policy"]["weigths"] = {}
    with pytest.raises(ConfigError, match=r"unknown key 'policy\.weigths'"):
        load_config(_write(tmp_path, "bad.yaml", d))


def test_missing_key_is_named(tmp_path):
    d = _default_dict()
    del d["policy"]["weights"]["remote"]
    with pytest.raises(ConfigError, match=r"missing key 'policy\.weights\.remote'"):
        load_config(_write(tmp_path, "bad.yaml", d))


def test_missing_section_is_named(tmp_path):
    d = _default_dict()
    del d["costs"]
    with pytest.raises(ConfigError, match=r"missing key 'costs'"):
        load_config(_write(tmp_path, "bad.yaml", d))


@pytest.mark.parametrize("path,value,msg", [
    (("generator", "legit", "new_payee"), 1.5, "less than or equal to 1"),
    (("generator", "legit", "fri"), [0.5, 0.5, 0.5, 0.0], "sum to 1"),
    (("generator", "legit", "network", "home"), 0.9, "sum to 1"),
    (("policy", "thresholds"), [50, 20, 70], "strictly increasing"),
    (("policy", "weights", "new_payee"), 150, "less than or equal to 100"),
    (("costs", "abandon", "2"), 1.5, "probability"),
])
def test_bad_values_rejected(tmp_path, path, value, msg):
    d = _default_dict()
    node = d
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = value
    with pytest.raises(ConfigError, match=msg):
        load_config(_write(tmp_path, "bad.yaml", d))


def test_unquoted_tier_keys_are_accepted(tmp_path):
    text = DEFAULT_CONFIG.read_text(encoding="utf-8").replace(
        'friction: {"0": 0, "W": 0.5, "1": 1, "2": 5, "3": 10}',
        "friction: {0: 0, W: 0.5, 1: 1, 2: 5, 3: 10}",
    )
    p = tmp_path / "c.yaml"
    p.write_text(text, encoding="utf-8")
    assert load_config(p).costs.friction.as_dict()["2"] == 5


def test_override_dict_dotted_and_nested(cfg):
    c = load_config(DEFAULT_CONFIG, overrides={"costs.friction.2": 10,
                                               "policy": {"shift": -5}})
    assert c.costs.friction.as_dict()["2"] == 10
    assert c.policy.shift == -5
    assert c.policy.weights == cfg.policy.weights  # untouched


def test_override_rejects_unknown_key():
    with pytest.raises(ConfigError, match=r"unknown key 'costs\.fricton'"):
        load_config(DEFAULT_CONFIG, overrides={"costs.fricton.2": 10})


def test_override_file_and_extends(tmp_path):
    over = _write(tmp_path, "x2.yaml", {"costs": {"friction": {"2": 10, "3": 20}}})
    c = load_config(DEFAULT_CONFIG, override_files=[over])
    assert c.costs.friction.as_dict()["3"] == 20 and c.costs.friction.as_dict()["1"] == 1

    child = _write(tmp_path, "child.yaml", {"extends": str(DEFAULT_CONFIG),
                                            "sizes": {"n_users": 10}})
    c2 = load_config(child)
    assert c2.sizes.n_users == 10 and c2.sizes.payments_per_user == 30


def test_with_overrides_validates(cfg):
    assert cfg.with_overrides({"generator.evasive": True}).generator.evasive is True
    with pytest.raises(ConfigError, match="bad value"):
        cfg.with_overrides({"policy.thresholds": [1, 2]})


def test_missing_file_and_bad_yaml(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")
    p = tmp_path / "broken.yaml"
    p.write_text("a: [1, 2", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(p)


def test_every_shipped_config_is_valid():
    for p in sorted(DEFAULT_CONFIG.parent.glob("*.yaml")):
        load_config(p)


def test_fingerprint_changes_with_values(cfg):
    assert cfg.fingerprint() == load_config(DEFAULT_CONFIG).fingerprint()
    assert cfg.fingerprint() != cfg.with_overrides({"policy.shift": 1}).fingerprint()
