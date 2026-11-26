from copy import deepcopy

import pytest
from fincourt.demo import demo_content, example_disclosures, load_demo_claims, load_disclosures
from fincourt.documents import DomainError, import_document

LITERAL = {"literal": {"value": "100000000", "unit": "yuan", "currency": "CNY"}}


def fixture_claim(client, identifier, **properties):
    response = client.post(
        "/api/claims",
        json={
            "id": identifier,
            "text": "可复核的计算观点",
            "subject": "DEMO-XINGQIAO",
            "source_kind": "SIMULATED",
            "kind": "CALCULATION",
            **properties,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def run(client, identifier, day=None, **options):
    payload = {"claim_id": identifier, **options}
    if day:
        payload["cutoff"] = {"date": day}
    response = client.post("/api/reviews", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def proof_from(*facts):
    return {
        "premises": [
            {"expression": {"op": "gt", "args": [{"fact_id": fact}, LITERAL]}} for fact in facts
        ]
    }


def test_fixed_and_rolling_fixture_history(workspace, monkeypatch):
    config, engine, client = workspace
    with engine.begin() as connection:
        load_disclosures(connection, config.storage_dir, "SIMULATED", "initial")
        load_demo_claims(connection, config.storage_dir, "SIMULATED")
    first = client.get("/api/reviews/sample-c5-initial").json()
    assert first["result"]["decision"] == "SUPPORTED"
    assert first["result"]["checks"][0]["steps"][0]["inputs"][0]["value"] == "120000000.00"
    with engine.begin() as connection:
        load_disclosures(connection, config.storage_dir, "SIMULATED", "correction")
    second = run(client, "sample-c5", "2025-04-13")
    assert second["result"]["decision"] == "REFUTED"
    assert second["result"]["checks"][0]["steps"][0]["inputs"][0]["value"] == "92000000.00"
    assert second["result"]["change"]["previous_run_id"] == first["id"]

    fixed = run(client, "sample-c2")
    assert fixed["result"]["decision"] == "SUPPORTED"
    assert {doc["id"] for doc in fixed["manifest"]["documents"]} == {"sample-original"}
    assert client.get("/api/reviews/sample-c2-initial").json()["version_status"] == "SUPERSEDED"
    fixed_error = client.post(
        "/api/reviews",
        json={
            "claim_id": "sample-c2",
            "cutoff": {"date": "2025-04-13"},
        },
    )
    assert fixed_error.status_code == 422
    assert fixed_error.json()["detail"]["code"] == "FIXED_CUTOFF"

    def forbidden(*_):
        raise AssertionError("读取历史结果不应重新执行")

    monkeypatch.setattr("fincourt.reviews.assess_claim", forbidden)
    assert client.get("/api/reviews/sample-c5-initial").json() == first


def test_multiple_proofs_and_cycle_rejection(workspace):
    config, engine, client = workspace
    with engine.begin() as connection:
        load_disclosures(connection, config.storage_dir, "SIMULATED", "all")
    a_and_b = proof_from("sample-assets-original", "sample-revenue-original")
    d = proof_from("sample-assets-original")
    fixture_claim(client, "calculation", proofs=[a_and_b, d])
    assert run(client, "calculation", "2025-04-13")["result"]["decision"] == "SUPPORTED"
    fixture_claim(client, "one-proof", proofs=[a_and_b])
    assert run(client, "one-proof", "2025-04-13")["result"]["decision"] == "INSUFFICIENT"

    self_reference = client.post(
        "/api/claims",
        json={
            "id": "cycle",
            "text": "循环推导观点",
            "subject": "DEMO-XINGQIAO",
            "source_kind": "SIMULATED",
            "kind": "CALCULATION",
            "proofs": [{"premises": [{"claim_id": "cycle"}]}],
        },
    )
    assert self_reference.status_code == 422
    assert self_reference.json()["detail"]["code"] == "DEPENDENCY_CYCLE"


def test_expression_validation_and_execution_failure(workspace):
    config, engine, client = workspace
    payload = {
        "id": "invalid",
        "text": "不可执行的计算观点",
        "subject": "DEMO-XINGQIAO",
        "source_kind": "SIMULATED",
        "kind": "CALCULATION",
        "proofs": [{"premises": [{"expression": {"op": "open", "args": []}}]}],
    }
    assert client.post("/api/claims", json=payload).status_code == 422
    fixture_claim(
        client,
        "division",
        proofs=[
            {
                "premises": [
                    {
                        "expression": {
                            "op": "div",
                            "args": [LITERAL, {"literal": {"value": "0", "unit": "ratio"}}],
                        }
                    }
                ]
            }
        ],
    )
    failed = run(client, "division", "2025-04-13")
    assert failed["status"] == "FAILED"
    assert failed["error"]["code"] == "DIVISION_BY_ZERO"
    assert failed["result"] is None
    live = client.post("/api/reviews", json={"claim_id": "division", "execution_mode": "live"})
    assert live.status_code == 503
    assert live.json()["detail"]["code"] == "NEXENT_CONFIGURATION_MISSING"


def test_disputed_and_period_filters(workspace):
    config, engine, client = workspace
    new = deepcopy(example_disclosures("SIMULATED")[0])
    new["metadata"]["id"] = "parallel-disclosure"
    new["metadata"]["source_cluster"] = "parallel-disclosure"
    new["metadata"]["published_date"] = "2025-03-01"
    new["metadata"]["facts"] = new["metadata"]["facts"][:1]
    new["metadata"]["facts"][0].update(
        id="parallel-revenue", value="9200", quote="营业总收入 9,200.00 万元。"
    )
    new["text"] = "【模拟披露】营业总收入 9,200.00 万元。"
    alternate = deepcopy(new)
    alternate["metadata"]["id"] = "other-period"
    alternate["metadata"]["facts"][0].update(
        id="other-period-revenue",
        period={
            "start": "2024-01-01",
            "end": "2024-03-31",
        },
    )
    with engine.begin() as connection:
        load_disclosures(connection, config.storage_dir, "SIMULATED", "initial")
        load_demo_claims(connection, config.storage_dir, "SIMULATED")
        import_document(
            connection, config.storage_dir, alternate["metadata"], *demo_content(alternate)
        )
    assert run(client, "sample-c2")["result"]["decision"] == "SUPPORTED"
    with engine.begin() as connection:
        import_document(connection, config.storage_dir, new["metadata"], *demo_content(new))
    disputed = run(client, "sample-c2")["result"]
    assert disputed["decision"] == "DISPUTED"
    assert {span["fact_id"] for span in disputed["evidence"]} == {
        "sample-revenue-original",
        "parallel-revenue",
    }


def test_real_case_c1_to_c5(workspace):
    config, engine, client = workspace
    try:
        for entry in example_disclosures("REAL"):
            demo_content(entry)
    except DomainError as problem:
        if problem.code != "SOURCE_MISSING":
            raise
        pytest.skip("运行 import-demo 后检查真实公告观点")
    with engine.begin() as connection:
        load_disclosures(connection, config.storage_dir, "REAL", "initial")
        load_demo_claims(connection, config.storage_dir, "REAL")
        load_disclosures(connection, config.storage_dir, "REAL", "correction")
        load_demo_claims(connection, config.storage_dir, "REAL")
    for index, expectation in enumerate(
        ["SUPPORTED", "SUPPORTED", "REFUTED", "INSUFFICIENT", "SUPPORTED"], 1
    ):
        item = client.get(f"/api/reviews/gd-c{index}-initial").json()
        assert item["result"]["decision"] == expectation
        assert item["result"]["evidence"]
    for index, amount, page in [(2, "118937200.00", 1), (3, "89983800.00", 2)]:
        result = client.get(f"/api/reviews/gd-c{index}-initial").json()["result"]
        assert result["evidence"][0]["page"] == page
        assert result["checks"][0]["steps"][0]["inputs"][0]["value"] == amount
