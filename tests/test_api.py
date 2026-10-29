import json
import re

import pytest
from fastapi.testclient import TestClient

from service.api import create_app
from tests.conftest import DEFAULT_CONFIG, GOLDEN, payment

SCREENSHARE = payment(amount=45000, median=250, network="home", new_payee=True, remote=True)


@pytest.fixture()
def log_path(tmp_path):
    return tmp_path / "decisions.jsonl"


@pytest.fixture()
def client(log_path):
    return TestClient(create_app(DEFAULT_CONFIG, log_path))


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and len(body["policy_version"]) == 12


def test_get_policy(client, cfg):
    p = client.get("/policy").json()
    assert p["weights"] == cfg.policy.weights.model_dump()
    assert p["thresholds"] == [20, 50, 70] and p["shift"] == 0


def test_score_matches_guide_example(client):
    r = client.post("/score", json=SCREENSHARE)
    assert r.status_code == 200
    body = r.json()
    assert body["score"] == 70 and body["raw_total"] == 70
    assert body["tier"] == "3" and body["tier_name"] == "Block and call back"
    assert body["breakdown"] == [
        {"signal": "new_payee", "points": 15},
        {"signal": "amt_over_10x", "points": 25},
        {"signal": "remote", "points": 30},
    ]
    assert body["thresholds"] == [20, 50, 70]
    assert body["policy_version"] == client.get("/health").json()["policy_version"]


@pytest.mark.parametrize("name", list(GOLDEN))
def test_golden_cases_through_api(client, name):
    inputs, want_score, want_tier = GOLDEN[name]
    body = client.post("/score", json=payment(**inputs)).json()
    assert (body["score"], body["tier"]) == (want_score, want_tier)


def test_cap_reports_raw_total(client):
    inputs, _, _ = GOLDEN["C_simswap"]
    body = client.post("/score", json=payment(**inputs)).json()
    assert body["score"] == 100 and body["raw_total"] == 110


@pytest.mark.parametrize("patch,field", [
    ({"amount": -5}, "amount"),
    ({"amount": 0}, "amount"),
    ({"median": 0}, "median"),
    ({"network": "satellite"}, "network"),
    ({"fri": 4}, "fri"),
    ({"fri": -1}, "fri"),
    ({"new_payee": "yes"}, "new_payee"),
    ({"surprise": 1}, "surprise"),
])
def test_score_rejects_bad_input(client, patch, field):
    r = client.post("/score", json={**SCREENSHARE, **patch})
    assert r.status_code == 422
    body = r.json()
    assert field in body["detail"]
    assert any(e["field"] == field for e in body["errors"])


def test_score_rejects_missing_field(client):
    bad = dict(SCREENSHARE)
    del bad["remote"]
    r = client.post("/score", json=bad)
    assert r.status_code == 422 and "remote" in r.json()["detail"]


def test_score_rejects_huge_amount(client, cfg):
    r = client.post("/score", json={**SCREENSHARE, "amount": cfg.service.max_amount * 2})
    assert r.status_code == 422 and "amount" in r.json()["detail"]


def test_batch(client):
    pays = [payment(**GOLDEN[k][0]) for k in GOLDEN]
    r = client.post("/score/batch", json={"payments": pays})
    assert r.status_code == 200
    assert [x["score"] for x in r.json()["results"]] == [g[1] for g in GOLDEN.values()]


def test_batch_limits(client, cfg):
    assert client.post("/score/batch", json={"payments": []}).status_code == 422
    too_many = [SCREENSHARE] * (cfg.service.max_batch + 1)
    r = client.post("/score/batch", json={"payments": too_many})
    assert r.status_code == 422 and "at most" in r.json()["detail"]


def test_compare(client):
    big_fri = {**SCREENSHARE, "fri": 3}
    body = client.post("/compare", json=big_fri).json()
    assert body["engine"]["tier"] == "3"
    assert body["baselines"] == {"pin_only": "0", "always_strict": "1",
                                 "amount_rule": "1", "fri_only": "3"}
    small = payment(amount=288, median=400, network="mobile", fri=1)
    b2 = client.post("/compare", json=small).json()["baselines"]
    assert b2["amount_rule"] == "0" and b2["fri_only"] == "W"
    assert "W" in body["tier_names"]


def test_policy_update_changes_version_and_tier(client):
    v0 = client.get("/health").json()["policy_version"]
    chai = payment(**GOLDEN["A_chai"][0])
    assert client.post("/score", json=chai).json()["tier"] == "0"

    r = client.post("/policy", json={"shift": -16})
    assert r.status_code == 200
    p = r.json()
    assert p["thresholds"] == [4, 34, 54] and p["policy_version"] != v0
    after = client.post("/score", json=chai).json()
    assert after["tier"] == "1" and after["thresholds"] == [4, 34, 54]

    p2 = client.post("/policy", json={"weights": {"net_mobile": 0}}).json()
    assert p2["weights"]["net_mobile"] == 0 and p2["shift"] == -16
    assert client.post("/score", json=chai).json()["score"] == 0

    reset = client.post("/policy", json={"reset": True}).json()
    assert reset["policy_version"] == v0


@pytest.mark.parametrize("body,msg", [
    ({"weights": {"bogus": 5}}, "unknown signal"),
    ({"weights": {"remote": 101}}, "0..100"),
    ({"weights": {"remote": "high"}}, "remote"),
    ({"shift": 500}, "shift"),
    ({"thresholds": [70, 50, 20]}, "strictly increasing"),
    ({}, "at least one"),
])
def test_policy_update_validation(client, body, msg):
    v0 = client.get("/health").json()["policy_version"]
    r = client.post("/policy", json=body)
    assert r.status_code == 422 and msg in r.json()["detail"]
    assert client.get("/health").json()["policy_version"] == v0  # unchanged


def test_simulate_small(client):
    r = client.post("/simulate", json={"seed": 3, "n_users": 50, "payments_per_user": 10,
                                       "attacks_per_type": 40, "shift_start": -30,
                                       "shift_stop": 60, "shift_step": 15})
    assert r.status_code == 200
    body = r.json()
    assert body["n_legit"] == 500 and body["n_attacks"] == 200
    assert [p["shift"] for p in body["curve"]] == [-30, -15, 0, 15, 30, 45, 60]
    # stricter policy (lower shift) never has less friction
    fr = [p["friction"] for p in body["curve"]]
    assert fr == sorted(fr, reverse=True)
    names = {b["name"] for b in body["baselines"]}
    assert names == {"pin_only", "always_strict", "amount_rule", "fri_only"}
    pin = next(b for b in body["baselines"] if b["name"] == "pin_only")
    assert pin["friction"] == 0 and pin["prevented_pct"] == 0
    assert body["current"]["shift"] == 0


def test_simulate_is_reproducible_and_evasive_differs(client):
    req = {"seed": 11, "n_users": 40, "payments_per_user": 10, "attacks_per_type": 30}
    a = client.post("/simulate", json=req).json()
    b = client.post("/simulate", json=req).json()
    assert a["curve"] == b["curve"]
    ev = client.post("/simulate", json={**req, "evasive": True}).json()
    assert ev["evasive"] and ev["current"] != a["current"]


def test_simulate_limits(client, cfg):
    r = client.post("/simulate", json={"n_users": cfg.service.simulate_limits.max_users + 1})
    assert r.status_code == 422 and "n_users" in r.json()["detail"]
    r = client.post("/simulate", json={"shift_start": 10, "shift_stop": 0})
    assert r.status_code == 422


def test_decision_log_written_and_readable(client, log_path):
    client.post("/score", json=SCREENSHARE)
    client.post("/score/batch", json={"payments": [payment(**GOLDEN["A_chai"][0])] * 2})
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    first = json.loads(lines[0])
    for key in ("timestamp", "inputs", "score", "breakdown", "tier", "thresholds",
                "policy_version"):
        assert key in first
    assert first["inputs"]["amount"] == 45000 and first["tier"] == "3"

    recent = client.get("/decisions?limit=2").json()["decisions"]
    assert len(recent) == 2 and recent[0]["score"] == 5  # newest first
    assert len(client.get("/decisions").json()["decisions"]) == 3


def test_decisions_limit_validation(client):
    assert client.get("/decisions?limit=0").status_code == 422
    assert client.get("/decisions?limit=100000").status_code == 422


def test_rejected_input_is_not_logged(client, log_path):
    client.post("/score", json={**SCREENSHARE, "amount": -1})
    assert not log_path.exists() or log_path.read_text(encoding="utf-8") == ""


def test_web_app_is_served(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/ui/"
    page = client.get("/ui/")
    assert page.status_code == 200 and "Step-up simulation engine" in page.text
    # The built React app (frontend/ -> service/web/) must reference assets that are served.
    assets = re.findall(r'(?:src|href)="(/ui/assets/[^"]+)"', page.text)
    assert any(a.endswith(".js") for a in assets) and any(a.endswith(".css") for a in assets)
    for a in assets:
        assert client.get(a).status_code == 200, a
