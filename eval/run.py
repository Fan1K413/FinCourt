import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from fincourt.changes import confirm_event  # noqa: E402
from fincourt.claims import ClaimInput, create_claim, get_claim  # noqa: E402
from fincourt.database import Settings, migrate  # noqa: E402
from fincourt.demo import load_demo_claims, load_disclosures  # noqa: E402
from fincourt.documents import fact_rows, list_documents  # noqa: E402
from fincourt.expressions import COMPARISONS, fact_quantity  # noqa: E402
from fincourt.models import Cutoff  # noqa: E402
from fincourt.reviews import ReviewInput, run_review  # noqa: E402


def fresh_database(path):
    config = Settings(f"sqlite:///{(path / 'eval.db').as_posix()}", path / "files")
    engine = config.engine()
    migrate(config, engine)
    return config, engine


def newest_without_cutoff(claim, facts):
    check = claim.check
    candidates = [
        item
        for item in facts
        if item["metric"] == check.metric
        and item["scope"] == check.scope
        and item["period_start"] == check.period.start.isoformat()
        and item["period_end"] == check.period.end.isoformat()
        and item["disclosure_type"] in {"RESULTS", "CORRECTION", "AUDIT"}
        and (not check.document_id or item["document_id"] == check.document_id)
        and (not check.require_audited or item["audit_status"] == "AUDITED")
    ]
    if not candidates:
        return "INSUFFICIENT"
    left = fact_quantity(candidates[-1])
    right, dimension, currency = check.threshold.normalized()
    if (left.dimension, left.currency) != (dimension, currency):
        return "INSUFFICIENT"
    return "SUPPORTED" if COMPARISONS[check.operator](left.value, right) else "REFUTED"


def metrics(cases, predictor):
    labels = sorted({item["expected"] for item in cases})
    classes = {}
    for label in labels:
        tp = sum(item["expected"] == label and item[predictor] == label for item in cases)
        fp = sum(item["expected"] != label and item[predictor] == label for item in cases)
        fn = sum(item["expected"] == label and item[predictor] != label for item in cases)
        classes[label] = {"support": tp + fn, "f1": round(2 * tp / (2 * tp + fp + fn), 4)}
    return {
        "n": len(cases),
        "matched": sum(item["expected"] == item[predictor] for item in cases),
        "macro_f1_on_present_labels": round(
            statistics.mean(item["f1"] for item in classes.values()), 4
        ),
        "classes": classes,
    }


def review_cases(path, plan, include_public):
    plan_groups = [
        group for group in plan["groups"] if include_public or group["source"] == "SIMULATED"
    ]
    companies = [group["subject"] for group in plan["groups"]]
    if len(set(companies)) != len(companies):
        raise ValueError("同一公司不可跨划分或重复定义")
    config, engine = fresh_database(path)
    try:
        cases = []
        with engine.begin() as connection:
            for group in plan_groups:
                kind = group["source"]
                load_disclosures(connection, config.storage_dir, kind, "all")
                load_demo_claims(connection, config.storage_dir, kind)
                files = [
                    item
                    for item in list_documents(connection)
                    if item["subject"] == group["subject"] and item["source_kind"] == kind
                ]
                records = fact_rows(connection, [item["id"] for item in files])
                for case in group["cases"]:
                    item = get_claim(connection, case["claim"])
                    candidate = ClaimInput.model_validate(item["definition"])
                    cutoff = Cutoff(date=case["date"]) if "date" in case else None
                    run = run_review(
                        connection,
                        ReviewInput(
                            claim_id=item["id"],
                            cutoff=cutoff,
                            purpose="PUBLIC_REPLAY",
                        ),
                        config.storage_dir,
                    )
                    assert run["status"] == "SUCCEEDED", run["error"]
                    result = run["result"]
                    cases.append(
                        {
                            **case,
                            "group": group["id"],
                            "split": group["split"],
                            "source": kind,
                            "fixture": result["decision"],
                            "latest_without_cutoff": newest_without_cutoff(candidate, records),
                            "expected_citation_found": any(
                                ref["fact_id"] == case["fact"] and ref["page"] == case["page"]
                                for ref in result["evidence"]
                            ),
                        }
                    )
    finally:
        engine.dispose()
    return cases


def propagation_trial(path):
    config, engine = fresh_database(path)
    try:
        with engine.begin() as connection:
            load_disclosures(connection, config.storage_dir, "SIMULATED", "initial")
            load_demo_claims(connection, config.storage_dir, "SIMULATED")
            unaffected = []
            check = get_claim(connection, "sample-c5")["definition"]["check"]
            for index in range(8):
                seed = create_claim(
                    connection,
                    ClaimInput.model_validate(
                        {
                            "id": f"assets-{index}",
                            "text": f"2024年总资产高于 {index + 1} 千万元",
                            "subject": "DEMO-XINGQIAO",
                            "source_kind": "SIMULATED",
                            "kind": "AS_OF_METRIC",
                            "check": {
                                **check,
                                "metric": "total_assets",
                                "threshold": {
                                    "value": str((index + 1) * 1000),
                                    "unit": "wan_yuan",
                                    "currency": "CNY",
                                },
                            },
                        }
                    ),
                )
                unaffected.append(seed["id"])
            load_disclosures(connection, config.storage_dir, "SIMULATED", "correction")
            started = time.perf_counter()
            event = confirm_event(connection, "sample-correction", config.storage_dir)
            local_ms = (time.perf_counter() - started) * 1000
            local = {
                item["claim_id"]: item["review"]["result"]["decision"] for item in event["claims"]
            }
            started = time.perf_counter()
            full = {}
            for identifier in ["sample-c5", *unaffected]:
                item = run_review(
                    connection,
                    ReviewInput(
                        claim_id=identifier,
                        cutoff=Cutoff(date="2025-04-12"),
                        purpose="PUBLIC_REPLAY",
                    ),
                    config.storage_dir,
                )
                full[identifier] = item["result"]["decision"]
            full_ms = (time.perf_counter() - started) * 1000
            assert local == {"sample-c5": "REFUTED"}
            assert {identifier: full[identifier] for identifier in local} == local
            return {
                "local_ms": local_ms,
                "full_ms": full_ms,
                "local": len(local),
                "full": len(full),
            }
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="执行 FinCourt 时序与局部重审评测")
    parser.add_argument("--real", action="store_true", help="检查已缓存的真实公告")
    parser.add_argument("--repetitions", type=int, default=5, choices=range(1, 21), metavar="1..20")
    args = parser.parse_args()
    data = json.loads((ROOT / "eval" / "cases.json").read_text(encoding="utf-8"))
    local = ROOT / "var" / "evaluation"
    local.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=local) as directory:
        base = Path(directory)
        rows = review_cases(base / "cases", data, args.real)
        samples = [propagation_trial(base / f"trial-{n}") for n in range(args.repetitions)]
    report = {
        "mode": "fixture",
        "knowledge": "fincourt-disclosures-v1",
        "n": len(rows),
        "sample_breakdown": {
            kind: sum(item["source"] == kind for item in rows) for kind in ("REAL", "SIMULATED")
        },
        "review": {
            method: metrics(rows, method) for method in ("fixture", "latest_without_cutoff")
        },
        "fixture_expected_citations": sum(item["expected_citation_found"] for item in rows),
        "errors": [
            row
            for row in rows
            if row["expected"] != row["fixture"] or row["expected"] != row["latest_without_cutoff"]
        ],
        "propagation": {
            "trials": len(samples),
            "targeted_runs": samples[0]["local"],
            "full_runs": samples[0]["full"],
            "targeted_median_ms": round(statistics.median(item["local_ms"] for item in samples), 1),
            "full_median_ms": round(statistics.median(item["full_ms"] for item in samples), 1),
            "identical_for_affected": True,
            "model_calls": 0,
        },
        "held_out_test_companies": data["held_out_test_companies"],
    }
    (local / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
