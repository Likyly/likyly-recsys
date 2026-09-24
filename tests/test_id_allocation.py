"""Internal id allocation under concurrency: the ALS matrix is indexed by these integers, so
they must be unique per namespace, stable per external id, and dense."""
from concurrent.futures import ThreadPoolExecutor

import db
from conftest import make_tenant


def test_concurrent_first_sightings_get_unique_dense_stable_ids():
    tenant = make_tenant()
    namespace = "race"

    def allocate(worker: int) -> dict[str, int]:
        # 30 ids private to the worker + 10 shared by everyone: contention on both kinds
        wanted = [f"w{worker}-{i}" for i in range(30)] + [f"shared-{i}" for i in range(10)]
        return db.resolve_internal_ids(tenant.client_id, namespace, db.KIND_ITEM, wanted, create=True)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(allocate, range(8)))

    merged: dict[str, set[int]] = {}
    for result in results:
        for external, internal in result.items():
            merged.setdefault(external, set()).add(internal)

    assert all(len(ids) == 1 for ids in merged.values()), "an external id was given two internal ids"
    internal_ids = sorted(next(iter(ids)) for ids in merged.values())
    assert internal_ids == list(range(len(merged))), "internal ids are not unique and dense"
    assert len(merged) == 8 * 30 + 10


def test_reads_never_create_mappings():
    tenant = make_tenant()
    assert db.resolve_internal_ids(tenant.client_id, "x", db.KIND_USER, ["ghost"]) == {}
    with db.SessionLocal() as session:
        assert session.query(db.IdMapModel).filter_by(client_id=tenant.client_id).count() == 0


def test_items_and_users_have_independent_id_spaces():
    tenant = make_tenant()
    item = db.resolve_internal_ids(tenant.client_id, "x", db.KIND_ITEM, ["a"], create=True)["a"]
    user = db.resolve_internal_ids(tenant.client_id, "x", db.KIND_USER, ["a"], create=True)["a"]
    assert item == user == 0
