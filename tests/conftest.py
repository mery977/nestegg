import pytest

from nestegg.demo import PERSONAS, build_demo
from nestegg.importers import read_csv
from nestegg.store import Store


@pytest.fixture(scope="session")
def demo_files(tmp_path_factory):
    out = tmp_path_factory.mktemp("demo")
    return out, build_demo(out)


@pytest.fixture()
def store(demo_files):
    _, paths = demo_files
    s = Store()
    for path, persona in zip(paths, PERSONAS, strict=True):
        txs, _ = read_csv(path, persona.customer_id)
        s.for_customer(persona.customer_id).add(txs)
    yield s
    s.close()
