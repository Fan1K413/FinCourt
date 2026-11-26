from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest
from fincourt.demo import demo_content, example_disclosures, load_disclosures
from fincourt.documents import DomainError, evidence_for, import_document
from fincourt.models import Amount, Cutoff, Period
from pydantic import ValidationError


def evidence(client, day, subject="DEMO-XINGQIAO", source="SIMULATED", **parameters):
    params = {"subject": subject, "source_kind": source, **parameters}
    if day:
        params["date"] = day
    response = client.get("/api/evidence", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_time_and_field_level_corrections(workspace):
    config, engine, client = workspace
    with engine.begin() as connection:
        load_disclosures(connection, config.storage_dir, "SIMULATED", "all")
    first = evidence(client, "2025-03-01")
    assert [doc["id"] for doc in first["documents"]] == ["sample-original"]
    assert first["corrections"] == []
    earlier = evidence(client, None, mode="PUBLIC_INSTANT", at="2025-04-12T23:59:59+08:00")
    assert first["facts"] == earlier["facts"]
    corrected = evidence(client, "2025-04-12")
    assert corrected["corrections"] == [
        {"original_id": "sample-revenue-original", "replacement_id": "sample-revenue-corrected"}
    ]
    assert {fact["id"] for fact in corrected["facts"] if fact["applicable"]} == {
        "sample-assets-original",
        "sample-revenue-corrected",
    }
    assert all(doc["time_precision"] == "DATE_ONLY" for doc in corrected["documents"])
    assert evidence(client, "2025-05-01", source="REAL")["facts"] == []


def test_exact_cutoff_and_period():
    cutoff = Cutoff(mode="PUBLIC_INSTANT", at="2025-04-12T11:00:00+08:00")
    assert cutoff.includes("2025-04-12", "2025-04-12T03:00:00+00:00")
    assert not cutoff.includes("2025-04-12", None)
    with pytest.raises(ValidationError):
        Period(start=date(2024, 12, 31), end=date(2024, 1, 1))
    assert (
        Amount(value="1", unit="yi_yuan", currency="CNY").normalized()
        == (Decimal("100000000"), "money", "CNY")
        == Amount(value="10000", unit="wan_yuan", currency="CNY").normalized()
    )
    with pytest.raises(ValidationError):
        Amount(value="1", unit="yuan")


@pytest.mark.parametrize("mutation", ["page", "quote", "value"])
def test_invalid_citation_rolls_back_document(workspace, mutation):
    config, engine, client = workspace
    item = example_disclosures("SIMULATED")[0]
    content, media = demo_content(item)
    metadata = deepcopy(item["metadata"])
    metadata["facts"][0][mutation] = {"page": 2, "quote": "不存在的字句", "value": "9"}[mutation]
    with pytest.raises(DomainError) as caught:
        with engine.begin() as connection:
            import_document(connection, config.storage_dir, metadata, content, media)
    assert caught.value.code == (
        "CITATION_VALUE_MISMATCH" if mutation == "value" else "INVALID_CITATION"
    )
    assert client.get("/api/documents").json() == []


def test_document_import_idempotent_and_immutable(workspace):
    config, engine, client = workspace
    item = example_disclosures("SIMULATED")[0]
    content, media = demo_content(item)
    with engine.begin() as connection:
        first = import_document(connection, config.storage_dir, item["metadata"], content, media)
        assert (
            import_document(connection, config.storage_dir, item["metadata"], content, media)
            == first
        )
    with pytest.raises(DomainError, match="新 ID"):
        with engine.begin() as connection:
            import_document(connection, config.storage_dir, item["metadata"], content + b"!", media)
    assert client.get("/api/documents/sample-original/file").content == content
    assert client.get("/api/documents/sample-original/pages/1?version=unknown").status_code == 404


def test_real_pdf_citation_and_original_bytes(workspace):
    config, engine, client = workspace
    try:
        for entry in example_disclosures("REAL"):
            demo_content(entry)
    except DomainError as problem:
        if problem.code != "SOURCE_MISSING":
            raise
        pytest.skip("运行 import-demo 后检查真实 PDF")
    with engine.begin() as connection:
        load_disclosures(connection, config.storage_dir, "REAL", "all")
    for day, fact_id, page in [
        ("2025-03-01", "gd-revenue-original", 1),
        ("2025-04-13", "gd-revenue-corrected", 2),
    ]:
        view = evidence(client, day, "688287", "REAL")
        fact = next(item for item in view["facts"] if item["id"] == fact_id)
        assert fact["applicable"]
        with engine.connect() as connection:
            span = evidence_for(connection, fact)
        assert span["page"] == page
        response = client.get(f"/api/documents/{span['document_id']}/pages/{page}")
        assert response.json()["text"][span["start"] : span["end"]] == span["text"]
        assert client.get(f"/api/documents/{span['document_id']}/file").content.startswith(b"%PDF-")
