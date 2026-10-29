import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.config import load_config  # noqa: E402

DEFAULT_CONFIG = ROOT / "configs" / "default.yaml"


@pytest.fixture(scope="session")
def cfg():
    return load_config(DEFAULT_CONFIG)


# The five golden cases from guide B2 (d = 0). ratio = amount / median.
GOLDEN = {
    "A_chai": (dict(amount=288, median=400, network="mobile"), 5, "0"),
    "B_rent": (dict(amount=45000, median=250, network="home", new_payee=True), 40, "1"),
    "C_simswap": (dict(amount=45000, median=250, network="public", new_device=True,
                       new_payee=True, sim_change=True), 100, "3"),
    "D_screenshare": (dict(amount=45000, median=250, network="home", new_payee=True,
                           remote=True), 70, "3"),
    "E_stolen": (dict(amount=45000, median=250, network="home", new_payee=True,
                      velocity=True, far_location=True), 65, "2"),
}


def payment(**kw) -> dict:
    base = dict(new_device=False, new_payee=False, velocity=False, sim_change=False,
                remote=False, far_location=False, fri=0)
    base.update(kw)
    return base
