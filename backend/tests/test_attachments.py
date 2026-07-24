"""Attachments: upload/list/download/delete with validation and access rules."""

import pytest

from app.config import settings

from tests.factories import make_customer, make_fabric


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "attachments_dir", str(tmp_path / "attachments"))


def _upload(client, entity_type, entity_id, filename="spec.pdf", content=b"%PDF-1.4 test"):
    return client.post(
        "/attachments",
        params={"entity_type": entity_type, "entity_id": entity_id},
        files={"file": (filename, content, "application/pdf")},
    )


def test_upload_list_download_roundtrip(client):
    material_id = make_fabric(client)
    created = _upload(client, "material", material_id).json()
    assert created["filename"] == "spec.pdf"
    assert created["size_bytes"] == len(b"%PDF-1.4 test")
    assert created["sha256"]

    listed = client.get(
        "/attachments", params={"entity_type": "material", "entity_id": material_id}
    ).json()
    assert [a["id"] for a in listed] == [created["id"]]

    downloaded = client.get(f"/attachments/{created['id']}/download")
    assert downloaded.status_code == 200
    assert downloaded.content == b"%PDF-1.4 test"
    assert "spec.pdf" in downloaded.headers["content-disposition"]


def test_upload_rejects_unknown_entity_and_missing_record(client):
    material_id = make_fabric(client)
    assert _upload(client, "spaceship", material_id).status_code == 422
    assert _upload(client, "customer", 99999).status_code == 404


def test_upload_rejects_disallowed_extension_and_empty_file(client):
    customer_id = make_customer(client)
    rejected = _upload(client, "customer", customer_id, filename="malware.exe")
    assert rejected.status_code == 422
    empty = _upload(client, "customer", customer_id, content=b"")
    assert empty.status_code == 422


def test_upload_rejects_oversize_file(client, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_bytes", 100)
    material_id = make_fabric(client)
    response = _upload(client, "material", material_id, content=b"x" * 200)
    assert response.status_code == 413


def test_delete_removes_record_and_file(client):
    material_id = make_fabric(client)
    created = _upload(client, "material", material_id).json()
    assert client.delete(f"/attachments/{created['id']}").status_code == 204
    assert client.get(f"/attachments/{created['id']}/download").status_code == 404
    listed = client.get(
        "/attachments", params={"entity_type": "material", "entity_id": material_id}
    ).json()
    assert listed == []
