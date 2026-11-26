import json
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.engine import Connection

from . import schema_v1 as db
from .claims import ClaimInput, create_claim
from .database import ROOT
from .documents import MAX_FILE, DomainError, import_document, sha256
from .models import Cutoff
from .reviews import ReviewInput, run_review


def example_disclosures(kind: str) -> list[dict]:
    name = "real-disclosures" if kind == "REAL" else "simulated-disclosures"
    return json.loads((ROOT / "examples" / f"{name}.json").read_text(encoding="utf-8"))["documents"]


def demo_content(entry: dict, download: bool = False) -> tuple[bytes, str]:
    if "text" in entry:
        return entry["text"].encode("utf-8"), "text/plain"
    source_url = entry["metadata"]["source_url"]
    source_path = ROOT / "var" / "source" / Path(urlsplit(source_url).path).name.lower()
    if not source_path.is_file() and download:
        # The two URLs are packaged with the source manifest. Uploads never fetch a supplied URL.
        with urllib.request.urlopen(source_url, timeout=25) as response:
            data = response.read(MAX_FILE + 1)
        if len(data) > MAX_FILE or sha256(data) != entry["sha256"]:
            raise DomainError("SOURCE_HASH", "下载的公告与来源清单不一致")
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(data)
    if not source_path.is_file():
        raise DomainError("SOURCE_MISSING", f"未下载公告 {source_path.name}，请先运行 import-demo")
    data = source_path.read_bytes()
    if sha256(data) != entry["sha256"]:
        raise DomainError("SOURCE_HASH", f"公告 {source_path.name} 与来源清单不一致")
    return data, "application/pdf"


def load_disclosures(
    connection: Connection, storage: Path, kind: str, stage: str, *, download: bool = False
) -> list[dict]:
    entries = example_disclosures(kind)
    if stage == "initial":
        entries = entries[:1]
    if stage == "correction":
        entries = entries[1:2]
    results = []
    for entry in entries:
        data, media_type = demo_content(entry, download)
        results.append(import_document(connection, storage, entry["metadata"], data, media_type))
    return results


def load_demo_claims(connection: Connection, storage: Path, kind: str) -> list[dict]:
    example = json.loads((ROOT / "examples" / "demo-claims.json").read_text(encoding="utf-8"))[kind]
    company = example["company"]
    threshold = {"value": "1", "unit": "yi_yuan", "currency": "CNY"}
    common = {
        "metric": "operating_revenue",
        "period": {"start": "2024-01-01", "end": "2024-12-31"},
        "scope": "CONSOLIDATED",
        "operator": "gt",
        "threshold": threshold,
    }
    before = Cutoff(date="2025-03-01")
    after = Cutoff(date="2025-04-13")
    variants = [
        (
            "DISCLOSURE_FACT",
            f"{company}在 2 月 28 日快报中披露营业总收入 {example['original_amount']} 万元。",
            before,
            {
                **common,
                "operator": "eq",
                "document_id": example["original"],
                "threshold": {
                    "value": example["original_amount"],
                    "unit": "wan_yuan",
                    "currency": "CNY",
                },
            },
        ),
        (
            "AS_OF_METRIC",
            f"截至 3 月 1 日，{company} 2024 年度披露的营业总收入超过 1 亿元。",
            before,
            common,
        ),
        (
            "AS_OF_METRIC",
            f"截至 4 月 13 日，{company} 2024 年度披露的营业总收入仍超过 1 亿元。",
            after,
            common,
        ),
        (
            "AS_OF_METRIC",
            f"已有材料能够证明 {company}披露的 {example['corrected_amount']} 万元是最终审计结果。",
            after,
            {
                **common,
                "operator": "eq",
                "require_audited": True,
                "threshold": {
                    "value": example["corrected_amount"],
                    "unit": "wan_yuan",
                    "currency": "CNY",
                },
            },
        ),
        (
            "AS_OF_METRIC",
            f"跟踪{company} 2024 年度最新披露的营业总收入是否超过 1 亿元。",
            None,
            common,
        ),
    ]
    correction_present = connection.scalar(
        select(db.document.c.id).where(db.document.c.id == example["correction"])
    )
    out = []
    for index, (claim_type, sentence, fixed_cutoff, check) in enumerate(variants, 1):
        claim_id = f"{example['prefix']}-c{index}"
        candidate = ClaimInput(
            id=claim_id,
            subject=example["subject"],
            source_kind=kind,
            kind=claim_type,
            text=sentence,
            timing="FIXED" if fixed_cutoff else "ROLLING",
            fixed_cutoff=fixed_cutoff,
            check=check,
        )
        out.append(create_claim(connection, candidate))
        run_id = f"{claim_id}-initial"
        if index in {3, 4} and not correction_present:
            continue
        if not connection.scalar(select(db.review.c.id).where(db.review.c.id == run_id)):
            result = run_review(
                connection,
                ReviewInput(
                    claim_id=claim_id,
                    cutoff=fixed_cutoff or before,
                ),
                storage,
                run_id=run_id,
            )
            if result["status"] != "SUCCEEDED":
                raise DomainError("DEMO_REVIEW_FAILED", result["error"]["message"])
    return out
