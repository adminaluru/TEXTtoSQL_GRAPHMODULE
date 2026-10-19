"""Load clarification choice labels (decision D-003) from reviewed data.

Kept as data, not code, so table names stay out of Python and the labels can
move into ontology metadata later without changing EntityMatchGuard.
"""
import json
from pathlib import Path

from planning.entity_match_guard import ChoiceLabel

DEFAULT_PATH = Path(__file__).with_name("entity_choice_labels.json")


def load_choice_labels(path: Path = DEFAULT_PATH) -> dict[str, list[ChoiceLabel]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    labels = {
        table: [ChoiceLabel.model_validate(item) for item in items]
        for table, items in raw.items()
        if not table.startswith("_")
    }
    for table, items in labels.items():
        if not items:
            raise ValueError(f"No choice labels configured for {table}.")
    return labels
