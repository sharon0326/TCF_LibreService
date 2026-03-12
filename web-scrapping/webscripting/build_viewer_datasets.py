import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ROOT = Path(__file__).resolve().parent
ORAL_SOURCE = ROOT / "task2_titles_classified.json"
WRITING_SOURCE_DIR = ROOT / "写作题库"
VIEWER_DATA_DIR = ROOT / "viewer" / "data"


_WRITING_FILE_RE = re.compile(r"^(\d{4})-(\d{2})_(.+)\.json$", re.IGNORECASE)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _parse_primary_secondary(item: Dict[str, Any]) -> Tuple[str, str]:
    raw = str(item.get("raw") or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            primary = _clean_text(parsed.get("primary"))
            secondary = _clean_text(parsed.get("secondary"))
            if primary or secondary:
                return primary, secondary
        except Exception:
            pass

    category = _clean_text(item.get("category"))
    if category:
        parts = [part.strip() for part in category.split("/")]
        primary = parts[0] if parts else ""
        secondary = parts[1] if len(parts) > 1 else ""
        return primary, secondary

    return "", ""


def _parse_writing_file_info(path: Path) -> Tuple[Optional[int], Optional[int], str]:
    match = _WRITING_FILE_RE.match(path.name)
    if not match:
        return None, None, path.name
    return int(match.group(1)), int(match.group(2)), match.group(3)


def _record_id(prefix: str, year: Optional[int], month: Optional[int], combinaison: Optional[int], index: int) -> str:
    year_part = str(year) if year is not None else "0000"
    month_part = f"{month:02d}" if month is not None else "00"
    comb_part = str(combinaison) if combinaison is not None else "0"
    return f"{prefix}-{year_part}-{month_part}-{comb_part}-{index}"


def build_oral_dataset() -> Dict[str, Any]:
    payload = json.loads(ORAL_SOURCE.read_text(encoding="utf-8"))
    results = payload.get("results", [])

    records: List[Dict[str, Any]] = []
    for index, item in enumerate(results):
        year = int(item["year"])
        month = int(item["month"])
        primary, secondary = _parse_primary_secondary(item)
        record = {
            "id": _record_id("oral", year, month, None, index),
            "exam": "oral",
            "task": 2,
            "year": year,
            "month": month,
            "source_file": item.get("source_file", ""),
            "combinaison": None,
            "title": _clean_text(item.get("title")),
            "answer": "",
            "document1": "",
            "document2": "",
            "category_primary": primary,
            "category_secondary": secondary,
        }
        if record["title"]:
            records.append(record)

    records.sort(key=lambda r: (r["year"], r["month"], r["title"]), reverse=True)
    return {
        "section": "oral",
        "generated_at": _utc_timestamp(),
        "count": len(records),
        "results": records,
    }


def build_writing_datasets() -> Dict[str, Dict[str, Any]]:
    grouped: Dict[int, List[Dict[str, Any]]] = {1: [], 2: [], 3: []}

    source_files = sorted(
        path for path in WRITING_SOURCE_DIR.glob("*.json") if path.name != "_summary.json"
    )

    for path in source_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        year, month, _ = _parse_writing_file_info(path)
        combinations = payload.get("combinations", [])

        for index, combo in enumerate(combinations):
            combinaison = combo.get("combinaison")
            source_file = path.name

            task1_title = _clean_text(combo.get("tache1_prompt"))
            if task1_title:
                grouped[1].append(
                    {
                        "id": _record_id("writing-1", year, month, combinaison, index),
                        "exam": "writing",
                        "task": 1,
                        "year": year,
                        "month": month,
                        "source_file": source_file,
                        "combinaison": combinaison,
                        "title": task1_title,
                        "answer": _clean_text(combo.get("tache1_answer")),
                        "document1": "",
                        "document2": "",
                    }
                )

            task2_title = _clean_text(combo.get("tache2_prompt"))
            if task2_title:
                grouped[2].append(
                    {
                        "id": _record_id("writing-2", year, month, combinaison, index),
                        "exam": "writing",
                        "task": 2,
                        "year": year,
                        "month": month,
                        "source_file": source_file,
                        "combinaison": combinaison,
                        "title": task2_title,
                        "answer": _clean_text(combo.get("tache2_answer")),
                        "document1": "",
                        "document2": "",
                    }
                )

            task3_title = _clean_text(combo.get("tache3_prompt"))
            if task3_title:
                grouped[3].append(
                    {
                        "id": _record_id("writing-3", year, month, combinaison, index),
                        "exam": "writing",
                        "task": 3,
                        "year": year,
                        "month": month,
                        "source_file": source_file,
                        "combinaison": combinaison,
                        "title": task3_title,
                        "answer": _clean_text(combo.get("tache3_answer")),
                        "document1": _clean_text(combo.get("document1")),
                        "document2": _clean_text(combo.get("document2")),
                    }
                )

    outputs: Dict[str, Dict[str, Any]] = {}
    for task in (1, 2, 3):
        grouped[task].sort(
            key=lambda r: (r["year"] or 0, r["month"] or 0, r.get("combinaison") or 0),
            reverse=True,
        )
        outputs[f"writing_task{task}.json"] = {
            "section": f"writing-task-{task}",
            "generated_at": _utc_timestamp(),
            "count": len(grouped[task]),
            "results": grouped[task],
        }

    return outputs


def main() -> int:
    VIEWER_DATA_DIR.mkdir(parents=True, exist_ok=True)

    outputs: Dict[str, Dict[str, Any]] = {
        "oral.json": build_oral_dataset(),
        **build_writing_datasets(),
    }

    summary = []
    for filename, payload in outputs.items():
        out_path = VIEWER_DATA_DIR / filename
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        summary.append({"file": filename, "count": payload.get("count", 0)})

    print(json.dumps({"generated_at": _utc_timestamp(), "outputs": summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
