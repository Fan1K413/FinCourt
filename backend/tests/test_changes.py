from copy import deepcopy

from fincourt.demo import demo_content, example_disclosures, load_demo_claims, load_disclosures
from fincourt.documents import import_document


def seed(workspace):
    config, engine, _ = workspace
    with engine.begin() as connection:
        load_disclosures(connection, config.storage_dir, "SIMULATED", "initial")
        load_demo_claims(connection, config.storage_dir, "SIMULATED")


def test_change_is_automatically_applied_and_idempotent(workspace):
    _, _, client = workspace
    seed(workspace)
    initial = client.get("/api/claims/sample-c2").json()["reviews"]
    create = client.post(
        "/api/claims",
        json={
            "id": "depends-revenue",
            "kind": "CALCULATION",
            "timing": "ROLLING",
            "text": "派生观点依赖营收是否超过阈值",
            "subject": "DEMO-XINGQIAO",
            "source_kind": "SIMULATED",
            "proofs": [{"premises": [{"claim_id": "sample-c5"}]}],
        },
    )
    assert create.status_code == 201
    first = client.post("/api/demo/correction", json={})
    assert first.status_code == 200, first.text
    event = first.json()
    assert event["changes"][0]["before"]["id"] == "sample-revenue-original"
    assert event["changes"][0]["after"]["id"] == "sample-revenue-corrected"
    assert "sample-c2" in event["fixed_claims_preserved"]
    assert [item["claim_id"] for item in event["claims"]] == ["sample-c5", "depends-revenue"]
    rolling, dependent = [item["review"] for item in event["claims"]]
    assert rolling["purpose"] == "CHANGE_REVIEW"
    assert rolling["result"]["decision"] == "REFUTED"
    assert rolling["result"]["change"]["previous_decision"] == "SUPPORTED"
    assert dependent["result"]["decision"] == "INSUFFICIENT"
    assert client.get("/api/claims/sample-c2").json()["reviews"] == initial
    for _ in range(2):
        repeated = client.post("/api/change-events", json={"document_id": "sample-correction"})
        assert repeated.json() == event
    assert len(client.get("/api/claims/sample-c5").json()["reviews"]) == 2
    replay = client.post(
        "/api/reviews",
        json={
            "claim_id": "sample-c5",
            "cutoff": {"date": "2025-03-01"},
            "purpose": "PUBLIC_REPLAY",
        },
    ).json()
    assert replay["result"]["decision"] == "SUPPORTED"
    assert replay["purpose"] == "PUBLIC_REPLAY"
    assert replay["id"] != "sample-c5-initial"


def test_matching_discovers_new_refutation_without_edges_and_groups_same_source(workspace):
    config, engine, client = workspace
    seed(workspace)
    base = deepcopy(example_disclosures("SIMULATED")[0])
    new = base["metadata"]
    new.update(id="new-disclosure", published_date="2025-03-05", source_cluster="new-primary")
    new["facts"] = new["facts"][:1]
    new["facts"][0].update(id="new-disclosed-revenue", value="9000", quote="营业总收入 9,000 万元")
    new_text = "【模拟一手披露】营业总收入 9,000 万元"
    with engine.begin() as connection:
        import_document(connection, config.storage_dir, new, new_text.encode(), "text/plain")
        duplicate = deepcopy(new)
        duplicate.update(id="duplicate-release", published_date="2025-03-04")
        duplicate["facts"][0]["id"] = "duplicate-revenue"
        import_document(connection, config.storage_dir, duplicate, new_text.encode(), "text/plain")
        for index in range(10):
            news = deepcopy(new)
            news.update(id=f"repost-{index}", disclosure_type="COMMENTARY")
            news["facts"][0]["id"] = f"repost-value-{index}"
            import_document(connection, config.storage_dir, news, new_text.encode(), "text/plain")
    matched = client.post("/api/change-events", json={"document_id": new["id"]}).json()
    entry = matched["claims"][0]
    assert entry["reasons"] == ["MATCHING_SUBJECT_METRIC_PERIOD"]
    result = entry["review"]["result"]
    assert result["decision"] == "DISPUTED"
    assert set(result["source_clusters"]) == {"sample-original", "new-primary"}
    assert len(result["evidence"]) == 2
    with engine.begin() as connection:
        load_disclosures(connection, config.storage_dir, "SIMULATED", "all")
    update = client.post("/api/change-events", json={"document_id": "sample-update"}).json()
    assert update["claims"] == []
    assert len(client.get("/api/claims/sample-c5").json()["reviews"]) == 2


def test_unaligned_correction_waits_for_confirmation(workspace):
    config, engine, client = workspace
    seed(workspace)
    entry = deepcopy(example_disclosures("SIMULATED")[1])
    entry["metadata"]["id"] = "unaligned"
    entry["metadata"]["facts"][0]["replaces"] = []
    with engine.begin() as connection:
        import_document(connection, config.storage_dir, entry["metadata"], *demo_content(entry))
    result = client.post("/api/change-events", json={"document_id": "unaligned"}).json()
    assert result["requires_confirmation"]
    assert result["claims"][0]["review"]["result"]["decision"] == "SUPPORTED"
