import json

from .database import ROOT
from .demo import demo_content
from .documents import import_document
from .knowledge import PatchInput, current_state, propose_patch


def import_agreement(connection, storage, *, download=False):
    source = json.loads((ROOT / "examples" / "agreement.json").read_text(encoding="utf-8"))
    entry = source["document"]
    document = import_document(
        connection, storage, entry["metadata"], *demo_content(entry, download=download)
    )
    location = {"document_id": document["id"], "version": document["parses"][-1]["version"]}
    item = PatchInput.model_validate(
        {
            "base_release": current_state(connection)["active_release"],
            "scope": "AGREEMENT",
            "gap": source["gap"],
            "migration": source["migration"],
            "operations": [
                {
                    "op": "ADD_TYPE",
                    "id": "conditional_agreement",
                    "definition": "根据协议阶段、金额用途和先决条件解释交易披露。",
                    "fields": ["stage", "amount_role", "amount", "recognition_basis", "conditions"],
                }
            ],
            "example": {
                **source["terms"],
                **{
                    name: {**location, **reference}
                    for name, reference in source["references"].items()
                },
            },
        }
    )
    return propose_patch(connection, item)
