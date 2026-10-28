"""F-1a: tenant-bound upload object store + parse-job status.

Two binding rules are asserted directly against the kernel helpers:

* TWO-INPUT: two different inputs produce two different object_ids AND two
  different stored paths, and the stored bytes read back == what was sent
  (input-derived content).
* CROSS-TENANT ISOLATION: tenant A's upload is neither listed nor readable
  under tenant B's digest, and the stored path sits under B's own digest dir.
"""

from __future__ import annotations

import pytest

from app.cerebrum_product_kernel.ingestion import object_store
from app.cerebrum_product_kernel.ingestion.object_store import (
    ObjectStoreError,
    list_objects,
    read_object,
    save_object,
    tenant_uploads_dir,
)
from app.cerebrum_product_kernel.ingestion.parse_job import (
    ParseStatus,
    create_job,
    get_job,
    list_jobs,
    set_status,
)
from app.factory.build.tenant_bind import bind_tenant_store


@pytest.fixture(autouse=True)
def _tenant_root(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("FACTORY_OUTPUTS_ROOT", str(tmp_path / "factory_outputs"))
    yield


def test_two_inputs_two_outputs_input_derived():
    a = save_object("tenant-a", "one.pdf", b"content-ONE")
    b = save_object("tenant-a", "two.csv", b"content-TWO-different")

    # Two different object ids and two different stored paths.
    assert a.object_id != b.object_id
    assert a.path != b.path

    # Stored content is input-derived: reading it back == what was sent.
    assert read_object("tenant-a", a.object_id) == b"content-ONE"
    assert read_object("tenant-a", b.object_id) == b"content-TWO-different"
    assert a.path.read_bytes() == b"content-ONE"


def test_cross_tenant_isolation():
    a = save_object("tenant-a", "secret.pdf", b"tenant-a-only")
    save_object("tenant-b", "theirs.pdf", b"tenant-b-only")

    # B's listing never contains A's object.
    b_ids = {o.object_id for o in list_objects("tenant-b")}
    assert a.object_id not in b_ids

    # A's object is not readable under B's digest.
    with pytest.raises(ObjectStoreError):
        read_object("tenant-b", a.object_id)

    # The stored path sits under B's own server-derived digest directory.
    b_digest = bind_tenant_store("tenant-b").tenant_key
    b_dir = tenant_uploads_dir("tenant-b")
    assert b_digest in str(b_dir)
    for obj in list_objects("tenant-b"):
        assert str(b_dir) in str(obj.path)
        # And A's digest dir is a different path entirely.
        assert bind_tenant_store("tenant-a").tenant_key not in str(obj.path)


def test_empty_identity_refused_by_name():
    with pytest.raises(ObjectStoreError):
        save_object("", "x.pdf", b"data")
    with pytest.raises(ObjectStoreError):
        save_object("   ", "x.pdf", b"data")


def test_parse_job_is_real_queued_record():
    stored = save_object("tenant-a", "doc.docx", b"docx-bytes")
    job = create_job(
        "tenant-a", object_id=stored.object_id, source_name=stored.source_name
    )
    assert job.status is ParseStatus.QUEUED
    assert job.object_id == stored.object_id

    loaded = get_job("tenant-a", job.job_id)
    assert loaded is not None and loaded.status is ParseStatus.QUEUED

    # The job is scoped to the tenant that created it.
    assert get_job("tenant-b", job.job_id) is None
    assert job.job_id in {j.job_id for j in list_jobs("tenant-a")}

    # set_status is the real transition F-1b will drive.
    advanced = set_status("tenant-a", job.job_id, ParseStatus.PARSING)
    assert advanced.status is ParseStatus.PARSING
