"""
Scraper for TCF Canada Expression Écrite (写作) questions and model answers.

Index page: https://reussir-tcfcanada.com/correction-des-sujets-dexpression-ecrite-v-pro/
Each month page URL pattern: https://reussir-tcfcanada.com/{month}-{year}-correction-expression-ecrite/

Each month page contains multiple "combinations" (one per topic). Each combination has:
  - topic title  (H2)
  - Document 1 text  (source text for Tâche 1)
  - Document 2 text  (source text for Tâche 1)
  - tache1_answer : synthesis essay (immediately after Doc2, before the Tâche 2 prompt)
  - tache2_prompt : short writing task  (60–120 words)
  - tache2_answer : model answer for Tâche 2
  - tache3_prompt : longer writing task  (120–150 words)
  - tache3_answer : model answer for Tâche 3

Usage:
  # Scrape Dec 2025, Jan 2026, Feb 2026 (default targets):
  python scrape_expression_ecrite.py

  # Scrape a specific month URL:
  python scrape_expression_ecrite.py --url https://reussir-tcfcanada.com/mars-2026-correction-expression-ecrite/

  # Scrape any month from the index (all available months):
  python scrape_expression_ecrite.py --all

  # Add a new month (future update interface):
  python scrape_expression_ecrite.py --url https://reussir-tcfcanada.com/mars-2026-correction-expression-ecrite/ --month-label "Mars 2026"
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup, Tag


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INDEX_URL = "https://reussir-tcfcanada.com/correction-des-sujets-dexpression-ecrite-v-pro/"

# Default months to scrape (year, month)
DEFAULT_TARGETS: List[Tuple[int, int]] = [
    (2025, 12),
    (2026, 1),
    (2026, 2),
]

MONTH_NAMES_FR: Dict[str, int] = {
    "janvier": 1,
    "février": 2,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "août": 8,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "décembre": 12,
    "decembre": 12,
}

MONTH_NUM_TO_FR: Dict[int, str] = {
    1: "Janvier",
    2: "Février",
    3: "Mars",
    4: "Avril",
    5: "Mai",
    6: "Juin",
    7: "Juillet",
    8: "Août",
    9: "Septembre",
    10: "Octobre",
    11: "Novembre",
    12: "Décembre",
}

OUTPUT_DIR = Path(__file__).parent / "写作题库"


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _month_key_from_label(label: str) -> Optional[Tuple[int, int]]:
    label = _clean(label)
    m = re.search(r"\b(20\d{2})\b", label)
    if not m:
        return None
    year = int(m.group(1))
    lower = label.lower()
    for name, num in MONTH_NAMES_FR.items():
        if name in lower:
            return (year, num)
    return None


def _get_session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    })
    return sess


def _fetch(session: requests.Session, url: str, timeout: int = 30, retries: int = 3) -> str:
    last: Optional[Exception] = None
    for _ in range(max(1, retries)):
        try:
            r = session.get(url, timeout=timeout)
            r.raise_for_status()
            return r.text
        except Exception as e:
            last = e
    raise last  # type: ignore[misc]


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Index page: collect month URLs
# ---------------------------------------------------------------------------

def collect_month_urls(session: requests.Session, timeout: int = 30) -> List[Dict[str, Any]]:
    """Parse the index page and return all month entries with their URLs."""
    html = _fetch(session, INDEX_URL, timeout=timeout)
    soup = BeautifulSoup(html, "html.parser")

    results: List[Dict[str, Any]] = []
    seen: set = set()

    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if "correction-expression-ecrite" not in href:
            continue
        if "reussir-tcfcanada.com" not in href:
            continue

        # Derive label from surrounding heading or URL slug
        label = None
        for prev in a.find_all_previous(["h1", "h2", "h3", "h4"], limit=4):
            t = _clean(prev.get_text(" ", strip=True))
            if _month_key_from_label(t):
                label = t
                break
        if not label:
            # Try to derive from URL slug like "fevrier-2026-correction-expression-ecrite"
            slug = href.rstrip("/").split("/")[-1]
            parts = slug.split("-")
            label = " ".join(parts[:2]).title()

        key = _month_key_from_label(label)
        if not key:
            continue
        if href in seen:
            continue
        seen.add(href)

        results.append({
            "year": key[0],
            "month": key[1],
            "label": label,
            "url": href,
        })

    results.sort(key=lambda x: (x["year"], x["month"]))
    return results


# ---------------------------------------------------------------------------
# Month page parsing
# ---------------------------------------------------------------------------

# Word-count hint patterns that mark the end of a tâche prompt
_WORD_COUNT_RE = re.compile(
    r"\(\s*\d+\s*mots?\s*(minimum|min)[^)]*\)",
    re.IGNORECASE,
)

# Heading patterns used to detect section boundaries
_DOC_HEADING_RE = re.compile(r"^document\s*[12]\s*[:\.]?\s*$", re.IGNORECASE)
_NAV_KEYWORDS = re.compile(
    r"(nos contacts|les pages?|les méthodologies|à propos|nous acceptons|"
    r"découvre les corrections|whatsapp|formulaire)",
    re.IGNORECASE,
)

_COMBINAISON_RE = re.compile(r"^Combinaison\s+(\d+)$", re.IGNORECASE)
_TASK_LABEL_RE = re.compile(r"^Tâche\s+([123])$", re.IGNORECASE)
_CORRECTION_LABEL_RE = re.compile(r"^Correction\s+Tâche\s+([123])$", re.IGNORECASE)


def _is_nav_text(text: str) -> bool:
    return bool(_NAV_KEYWORDS.search(text))


def _dedupe_blocks(blocks: List[str]) -> List[str]:
    deduped: List[str] = []
    seen: set[str] = set()
    for block in blocks:
        text = _clean(block)
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped


def _join_blocks(blocks: List[str]) -> str:
    return "\n\n".join(_dedupe_blocks(blocks))


def _new_combination(topic: str, number: Optional[int]) -> Dict[str, Any]:
    return {
        "combinaison": number,
        "topic": topic,
        "tache1_prompt": "",
        "tache1_answer": "",
        "tache2_prompt": "",
        "tache2_answer": "",
        "tache3_prompt": topic,
        "document1": "",
        "document2": "",
        "tache3_answer": "",
    }


def _parse_month_page(html: str, url: str) -> Dict[str, Any]:
    """
    Parse a full month page and return structured data.

    Returns a dict with:
      url, scraped_at, month_label, combinations: [...]

    Each combination:
      topic, document1, document2,
      tache1_answer, tache2_prompt, tache2_answer,
      tache3_prompt, tache3_answer
    """
    soup = BeautifulSoup(html, "html.parser")

    page_title = _clean(soup.title.get_text(strip=True)) if soup.title else ""

    combinations: List[Dict[str, Any]] = []

    main = (
        soup.select_one("main")
        or soup.select_one(".entry-content")
        or soup.select_one("article")
        or soup
    )

    current_topic = ""
    current: Optional[Dict[str, Any]] = None
    current_section: Optional[str] = None
    current_doc: Optional[str] = None
    last_marker: Optional[Tuple[str, str]] = None

    def finalize_current() -> None:
        nonlocal current
        if not current:
            return
        for key in [
            "tache1_prompt",
            "tache1_answer",
            "tache2_prompt",
            "tache2_answer",
            "tache3_prompt",
            "document1",
            "document2",
            "tache3_answer",
        ]:
            current[key] = _join_blocks([current[key]]) if current[key] else ""
        if current.get("tache1_prompt") or current.get("tache2_prompt") or current.get("document1"):
            combinations.append(current)
        current = None

    for el in main.find_all(["h2", "span", "h3", "p", "h6"]):
        text = _clean(el.get_text(" ", strip=True))
        if not text or _is_nav_text(text):
            continue

        if el.name == "h2":
            if text.lower().startswith("des thèmes traités"):
                continue
            if text.lower().startswith("pour partager les sujets"):
                continue
            current_topic = text
            if current and current_section == "tache3_prompt":
                current["topic"] = text
                current["tache3_prompt"] = text
            continue

        if el.name == "span":
            comb_match = _COMBINAISON_RE.match(text)
            if comb_match:
                marker = ("combinaison", text)
                if marker == last_marker:
                    continue
                finalize_current()
                current = _new_combination(current_topic, int(comb_match.group(1)))
                current_section = None
                current_doc = None
                last_marker = marker
                continue

            task_match = _TASK_LABEL_RE.match(text)
            if task_match:
                marker = ("task", text)
                if marker == last_marker:
                    continue
                task_num = task_match.group(1)
                if task_num == "1":
                    current_section = "tache1_prompt"
                elif task_num == "2":
                    current_section = "tache2_prompt"
                else:
                    current_section = "tache3_prompt"
                current_doc = None
                last_marker = marker
                continue

            correction_match = _CORRECTION_LABEL_RE.match(text)
            if correction_match:
                marker = ("correction", text)
                if marker == last_marker:
                    continue
                task_num = correction_match.group(1)
                if task_num == "1":
                    current_section = "tache1_answer"
                elif task_num == "2":
                    current_section = "tache2_answer"
                else:
                    current_section = "tache3_answer"
                current_doc = None
                last_marker = marker
                continue

        if not current:
            continue

        if el.name == "h3" and _DOC_HEADING_RE.match(text):
            current_section = "tache3_docs"
            current_doc = "document1" if "1" in text else "document2"
            continue

        if el.name == "h6" and _WORD_COUNT_RE.search(text):
            continue

        if el.name != "p":
            continue

        if current_section == "tache3_docs" and current_doc:
            existing = current[current_doc]
            current[current_doc] = _join_blocks([existing, text]) if existing else text
        elif current_section in current:
            existing = current[current_section]
            current[current_section] = _join_blocks([existing, text]) if existing else text

    finalize_current()

    return {
        "url": url,
        "scraped_at": _utc_timestamp(),
        "page_title": page_title,
        "combinations": combinations,
    }


# ---------------------------------------------------------------------------
# High-level scrape functions
# ---------------------------------------------------------------------------

def scrape_month_url(url: str, session: Optional[requests.Session] = None, timeout: int = 30) -> Dict[str, Any]:
    """Scrape a single month page and return parsed data."""
    if session is None:
        session = _get_session()
    html = _fetch(session, url, timeout=timeout)
    return _parse_month_page(html, url)


def scrape_targets(
    targets: List[Tuple[int, int]],
    out_dir: Path,
    session: Optional[requests.Session] = None,
    timeout: int = 30,
) -> List[Dict[str, Any]]:
    """
    Scrape a list of (year, month) targets.
    Constructs URL from pattern, saves one JSON per month.
    Returns list of result summaries.
    """
    if session is None:
        session = _get_session()

    out_dir.mkdir(parents=True, exist_ok=True)

    # Month name slug mapping for URL construction (no accents, lowercase)
    MONTH_SLUG: Dict[int, str] = {
        1: "janvier", 2: "fevrier", 3: "mars", 4: "avril",
        5: "mai", 6: "juin", 7: "juillet", 8: "aout",
        9: "septembre", 10: "octobre", 11: "novembre", 12: "decembre",
    }

    results = []
    for year, month in targets:
        slug = MONTH_SLUG[month]
        url = f"https://reussir-tcfcanada.com/{slug}-{year}-correction-expression-ecrite/"
        month_label = f"{MONTH_NUM_TO_FR[month]} {year}"
        print(f"  Scraping {month_label} ... {url}")
        try:
            data = scrape_month_url(url, session=session, timeout=timeout)
            data["month_label"] = month_label
            data["year"] = year
            data["month"] = month

            filename = f"{year}-{month:02d}_{slug}-{year}.json"
            out_path = out_dir / filename
            out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"    -> Saved {out_path.name}  ({len(data['combinations'])} combinations)")
            results.append({"year": year, "month": month, "label": month_label, "url": url, "output": str(out_path), "error": None})
        except Exception as e:
            print(f"    -> ERROR: {e}")
            results.append({"year": year, "month": month, "label": month_label, "url": url, "output": None, "error": str(e)})

    return results


def scrape_all_from_index(
    out_dir: Path,
    session: Optional[requests.Session] = None,
    timeout: int = 30,
) -> List[Dict[str, Any]]:
    """
    Scrape ALL months listed on the index page.
    """
    if session is None:
        session = _get_session()
    months = collect_month_urls(session, timeout=timeout)
    targets = [(m["year"], m["month"]) for m in months]
    print(f"Found {len(targets)} months on index page.")
    return scrape_targets(targets, out_dir, session=session, timeout=timeout)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Scrape TCF Canada Expression Écrite (写作) questions and answers."
    )
    parser.add_argument(
        "--url",
        default=None,
        help="Scrape a single month page URL directly.",
    )
    parser.add_argument(
        "--month-label",
        default=None,
        help='Human-readable label for --url mode (e.g. "Mars 2026").',
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Scrape ALL months listed on the index page.",
    )
    parser.add_argument(
        "--out-dir",
        default=str(OUTPUT_DIR),
        help=f"Output directory (default: {OUTPUT_DIR})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="HTTP timeout in seconds.",
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    session = _get_session()

    if args.all:
        print("Scraping all months from index page...")
        results = scrape_all_from_index(out_dir, session=session, timeout=args.timeout)
    elif args.url:
        print(f"Scraping single URL: {args.url}")
        data = scrape_month_url(args.url, session=session, timeout=args.timeout)
        if args.month_label:
            data["month_label"] = args.month_label
            key = _month_key_from_label(args.month_label)
            if key:
                data["year"], data["month"] = key

        out_dir.mkdir(parents=True, exist_ok=True)
        slug = args.url.rstrip("/").split("/")[-1]
        filename = f"{slug}.json"
        out_path = out_dir / filename
        out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Saved: {out_path}  ({len(data['combinations'])} combinations)")
        results = [{"url": args.url, "output": str(out_path), "error": None}]
    else:
        # Default: scrape Dec 2025, Jan 2026, Feb 2026
        print("Scraping default targets: Décembre 2025, Janvier 2026, Février 2026")
        results = scrape_targets(DEFAULT_TARGETS, out_dir, session=session, timeout=args.timeout)

    # Write summary
    summary = {
        "scraped_at": _utc_timestamp(),
        "index_url": INDEX_URL,
        "results": results,
    }
    summary_path = out_dir / "_summary.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSummary saved: {summary_path}")

    ok = sum(1 for r in results if not r.get("error"))
    print(f"Done. OK: {ok}/{len(results)}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
