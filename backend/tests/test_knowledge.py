from copy import deepcopy

import pytest
from fincourt.agreement import import_agreement
from fincourt.demo import load_demo_claims, load_disclosures
from fincourt.documents import DomainError, import_document
from fincourt.reviews import BASE_RELEASE

AUTH = {"Authorization": "Bearer local-test-token"}


def patch_url(draft):
    return f"/api/ontology-patches/{draft['id']}"


def test_real_patch_projection_and_rollback(workspace, monkeypatch):
    settings, engine, client = workspace
    monkeypatch.setenv("FINCOURT_ADMIN_TOKEN", "local-test-token")
    with engine.begin() as connection:
        try:
            draft = import_agreement(connection, settings.storage_dir)
        except DomainError as error:
            if error.code == "SOURCE_MISSING":
                pytest.skip("运行 import-agreement 后检查真实协议原文")
            raise
        load_disclosures(connection, settings.storage_dir, "SIMULATED", "initial")
        load_demo_claims(connection, settings.storage_dir, "SIMULATED")
    path = patch_url(draft)
    original = client.get("/api/knowledge-releases").json()
    assert original["active_release"] == BASE_RELEASE
    assert original["pending_documents"] == ["cninfo-1225682693"]
    assert original["projections"] == []
    assert client.post(path + "/approve", headers=AUTH, json={"passed": True}).status_code == 409
    assert client.post(path + "/approve").status_code == 403
    validated = client.post(path + "/validate").json()
    assert validated["status"] == "READY_FOR_REVIEW", validated
    assert validated["validation"]["after"]["amount"]["value"] == "1164155942.99"
    assert validated["validation"]["after"]["recognized_revenue"] is None
    assert [item["page"] for item in validated["validation"]["after"]["citations"]] == [3, 1, 1]
    approved = client.post(path + "/approve", headers=AUTH).json()
    assert approved["status"] == "APPROVED"
    published = client.post(path + "/publish", headers=AUTH).json()
    assert published["status"] == "PUBLISHED"
    release = client.get("/api/knowledge-releases").json()
    assert release["active_release"] == published["published_release"]
    assert release["projections"][0]["release_id"] == release["active_release"]
    assert release["pending_documents"] == []
    reviewed = client.post("/api/reviews", json={"claim_id": "sample-c2"}).json()
    assert reviewed["manifest"]["knowledge_release"]["id"] == release["active_release"]
    with engine.begin() as connection:
        load_disclosures(connection, settings.storage_dir, "SIMULATED", "correction")
    restored = client.post(f"/api/knowledge-releases/{BASE_RELEASE}/rollback", headers=AUTH).json()
    assert restored["active_release"] == BASE_RELEASE
    assert restored["projections"] == []
    assert restored["pending_documents"] == ["cninfo-1225682693"]
    assert [item["action"] for item in restored["history"]] == ["ROLLBACK", "PUBLISH"]
    assert client.get("/api/documents/cninfo-1225682693/file").content.startswith(b"%PDF-")
    assert client.get("/api/documents/sample-correction").status_code == 200
    assert client.get("/api/reviews/" + reviewed["id"]).json()["manifest"] == reviewed["manifest"]


def simulated_patch(workspace, client):
    settings, engine, _ = workspace
    text = "备忘录的最高金额：1,000.00 元。\n协议暂未生效，自双方签字盖章后生效。\n需签署正式合同，采用净额法。"
    with engine.begin() as connection:
        document = import_document(
            connection,
            settings.storage_dir,
            {
                "id": "sim-agreement",
                "title": "模拟备忘录",
                "subject": "SIM-AGREE",
                "source_kind": "SIMULATED",
                "disclosure_type": "AGREEMENT",
                "published_date": "2026-11-21",
            },
            text.encode(),
            "text/plain",
        )
    reference = {
        "document_id": "sim-agreement",
        "version": document["parses"][0]["version"],
        "page": 1,
    }
    terms = {
        "base_release": BASE_RELEASE,
        "scope": "AGREEMENT",
        "gap": "原始数据结构无法记录本协议的阶段和口径。",
        "migration": "仅查看协议指定文字并保留原始文件与历史。",
        "operations": [
            {
                "op": "ADD_TYPE",
                "id": "conditional_agreement",
                "definition": "记录协议阶段和条件及其金额用途。",
                "fields": ["stage", "amount_role", "amount", "recognition_basis", "conditions"],
            }
        ],
        "example": {
            "amount": {"value": "1000.00", "unit": "yuan", "currency": "CNY"},
            "amount_role": "MEMORANDUM_CEILING",
            "stage": "AWARDED_NOT_EFFECTIVE",
            "recognition_basis": "NET_AFTER_FORMAL_CONTRACT",
            "conditions": ["SIGN_AND_SEAL", "FORMAL_CONTRACT"],
            "amount_reference": {**reference, "quote": text.splitlines()[0]},
            "stage_reference": {**reference, "quote": text.splitlines()[1]},
            "recognition_reference": {**reference, "quote": text.splitlines()[2]},
        },
    }
    draft = client.post("/api/ontology-patches", json=terms)
    assert draft.status_code == 201, draft.text
    return terms, draft.json()


def test_validation_conflicts_on_new_data_and_base_release(workspace, monkeypatch):
    settings, engine, client = workspace
    monkeypatch.setenv("FINCOURT_ADMIN_TOKEN", "local-test-token")
    terms, draft = simulated_patch(workspace, client)
    path = patch_url(draft)
    assert client.post(path + "/validate").json()["status"] == "READY_FOR_REVIEW"
    assert client.post(path + "/approve", headers=AUTH).status_code == 200
    with engine.begin() as connection:
        load_disclosures(connection, settings.storage_dir, "SIMULATED", "initial")
    rejected = client.post(path + "/publish", headers=AUTH)
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "VALIDATION_STALE"
    assert client.post(path + "/validate").json()["status"] == "READY_FOR_REVIEW"
    assert client.post(path + "/approve", headers=AUTH).status_code == 200
    another = client.post(
        "/api/ontology-patches", json={**terms, "gap": terms["gap"] + "另作复核。"}
    ).json()
    alternate = patch_url(another)
    assert client.post(alternate + "/validate").json()["status"] == "READY_FOR_REVIEW"
    assert client.post(alternate + "/approve", headers=AUTH).status_code == 200
    new_release = client.post(path + "/publish", headers=AUTH).json()["published_release"]
    assert client.post(alternate + "/publish", headers=AUTH).status_code == 409
    assert client.get("/api/knowledge-releases").json()["active_release"] == new_release


def test_regression_failure_and_forged_approval_cannot_publish(workspace, monkeypatch):
    _, _, client = workspace
    monkeypatch.setenv("FINCOURT_ADMIN_TOKEN", "local-test-token")
    terms, draft = simulated_patch(workspace, client)
    forged = deepcopy(terms)
    forged["passed"] = True
    assert client.post("/api/ontology-patches", json=forged).status_code == 422
    monkeypatch.setattr(
        "fincourt.knowledge._run_regressions", lambda: [{"id": "regression", "passed": False}]
    )
    path = patch_url(draft)
    rejected = client.post(path + "/validate").json()
    assert rejected["status"] == "VALIDATION_FAILED"
    assert rejected["validation"]["after"] is not None
    assert not rejected["validation"]["passed"]
    assert client.post(path + "/approve", headers=AUTH, json={"passed": True}).status_code == 409
    assert client.post(path + "/publish", headers=AUTH).status_code == 409
    assert client.get("/api/knowledge-releases").json()["active_release"] == BASE_RELEASE
