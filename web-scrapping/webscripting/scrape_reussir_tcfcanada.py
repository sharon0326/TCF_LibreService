import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup


def _clean_text(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s


def _slugify_filename(s: str) -> str:
    s = _clean_text(s)
    s = s.lower()
    s = re.sub(r"[^a-z0-9\-_]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "output"


def _month_key_from_label(label: str) -> Optional[Tuple[int, int]]:
    label = _clean_text(label)
    months = {
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
    m = re.search(r"\b(20\d{2})\b", label)
    if not m:
        return None
    year = int(m.group(1))
    month = None
    lower = label.lower()
    for name, num in months.items():
        if name in lower:
            month = num
            break
    if month is None:
        return None
    return (year, month)


def _should_include_month(key: Tuple[int, int]) -> bool:
    year, month = key
    if year == 2025:
        return True
    if year == 2026 and month <= 2:
        return True
    return False


def _get_session(user_agent: Optional[str] = None) -> requests.Session:
    sess = requests.Session()
    sess.headers.update(
        {
            "User-Agent": user_agent
            or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8,zh-CN;q=0.7,zh;q=0.6",
        }
    )
    return sess


def _fetch_html(session: requests.Session, url: str, timeout: int = 30, retries: int = 3) -> str:
    last_err: Optional[Exception] = None
    for _ in range(max(1, retries)):
        try:
            resp = session.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            last_err = e
    assert last_err is not None
    raise last_err


def _extract_accordion_sections(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    sections: List[Dict[str, Any]] = []

    # Elementor accordion structure is usually:
    # - .elementor-accordion-item contains
    #   - .elementor-tab-title (question/title)
    #   - .elementor-tab-content (content)
    # This page may have multiple accordion widgets; we collect all items.
    items = soup.select(".elementor-accordion .elementor-accordion-item")
    if not items:
        # fallback: sometimes accordion items are not wrapped as expected
        items = soup.select(".elementor-accordion-item")

    for item in items:
        title_el = item.select_one(".elementor-tab-title, .elementor-accordion-title")
        content_el = item.select_one(".elementor-tab-content")

        title = _clean_text(title_el.get_text(" ", strip=True)) if title_el else ""

        # Content parsing: frequently includes a <p> with numbered <strong> questions
        # and then sample answers as additional <p> blocks.
        questions: List[str] = []
        sample_answers: List[str] = []

        if content_el:
            # Collect questions from strong tags that look like numbering.
            strongs = content_el.find_all("strong")
            for st in strongs:
                t = _clean_text(st.get_text(" ", strip=True))
                if re.match(r"^\d+\.?\s+", t):
                    questions.append(t)

            # If questions weren't found via <strong>, fallback to splitting text lines.
            if not questions:
                raw = content_el.get_text("\n", strip=True)
                for line in [x.strip() for x in raw.split("\n") if x.strip()]:
                    if re.match(r"^\d+\.?\s+", line):
                        questions.append(_clean_text(line))

            # Sample answers: keep paragraph text that is not just the numbered list.
            ps = content_el.find_all(["p", "div", "li"], recursive=True)
            for p in ps:
                t = _clean_text(p.get_text(" ", strip=True))
                if not t:
                    continue
                # Skip if it's exactly one of the extracted questions or is just numbering.
                if any(t == q for q in questions):
                    continue
                if re.match(r"^\d+\.?\s*$", t):
                    continue
                # Skip blocks that are basically a concatenation of all questions
                if len(questions) >= 3 and all(q.split(" ", 1)[0].rstrip(".").isdigit() for q in questions):
                    # If the paragraph contains many question numbers, it's likely the questions block.
                    if len(re.findall(r"\b\d+\.", t)) >= 3:
                        continue
                sample_answers.append(t)

        sections.append(
            {
                "title": title,
                "questions": questions,
                "sample_answers": sample_answers,
            }
        )

    # Deduplicate by title (some Elementor duplicates can appear)
    deduped: List[Dict[str, Any]] = []
    seen: set[Tuple[str, str]] = set()
    for s in sections:
        key = (s.get("title", ""), "|".join(s.get("questions", [])[:3]))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(s)

    return deduped


def _extract_task3_subject_answers(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    # Task 3 pages on this site are typically a sequence:
    # <a>Subject?</a>
    # <p>answer paragraph 1</p>
    # <p>answer paragraph 2</p>
    # ...
    # <a>Next subject?</a>
    # We'll treat each anchor text that looks like a question as a subject delimiter.
    items: List[Dict[str, Any]] = []

    # Try to restrict to main content first; fall back to full soup.
    container = soup.select_one("main") or soup.select_one(".elementor") or soup

    subjects = container.select('a[href]')
    subject_anchors = []
    for a in subjects:
        t = _clean_text(a.get_text(" ", strip=True))
        if not t:
            continue
        if "?" not in t:
            continue
        # Avoid navigation anchors
        if len(t) < 10:
            continue
        subject_anchors.append(a)

    for a in subject_anchors:
        subject = _clean_text(a.get_text(" ", strip=True))
        answer_parts: List[str] = []
        for sib in a.next_siblings:
            if getattr(sib, "name", None) == "a":
                nt = _clean_text(sib.get_text(" ", strip=True))
                if nt and "?" in nt:
                    break
            if getattr(sib, "name", None) in {"p", "div", "li"}:
                txt = _clean_text(sib.get_text(" ", strip=True))
                if txt:
                    answer_parts.append(txt)

        # Some pages put content not as direct siblings; fallback to scanning forward elements
        if not answer_parts:
            for el in a.find_all_next(["p", "div", "li", "a"], limit=200):
                if el.name == "a":
                    nt = _clean_text(el.get_text(" ", strip=True))
                    if nt and "?" in nt and nt != subject:
                        break
                else:
                    txt = _clean_text(el.get_text(" ", strip=True))
                    if txt:
                        answer_parts.append(txt)

        items.append({"subject": subject, "answer": answer_parts})

    # De-dup subjects while keeping order
    seen: set[str] = set()
    deduped: List[Dict[str, Any]] = []
    for it in items:
        s = it.get("subject", "")
        if not s or s in seen:
            continue
        seen.add(s)
        deduped.append(it)
    return deduped


def scrape(url: str, timeout: int = 30, user_agent: Optional[str] = None) -> Dict[str, Any]:
    session = _get_session(user_agent=user_agent)
    html = _fetch_html(session, url, timeout=timeout, retries=3)
    soup = BeautifulSoup(html, "html.parser")

    data: Dict[str, Any] = {
        "url": url,
        "scraped_at": datetime.utcnow().isoformat() + "Z",
        "page_title": _clean_text(soup.title.get_text(strip=True)) if soup.title else "",
        "sections": _extract_accordion_sections(soup),
    }

    return data


def collect_task3_category_pages(task3_index_url: str, timeout: int = 30, user_agent: Optional[str] = None) -> List[Dict[str, Any]]:
    session = _get_session(user_agent=user_agent)
    html = _fetch_html(session, task3_index_url, timeout=timeout, retries=3)
    soup = BeautifulSoup(html, "html.parser")

    categories: List[Dict[str, Any]] = []
    for a in soup.select('a[href]'):
        href = a.get("href")
        if not href:
            continue
        full = urljoin(task3_index_url, href)
        path = urlparse(full).path.lower()
        if "correction-des-sujets-dexpression-orale" in path and "tache-3" in path and "correction" in path:
            label = _clean_text(a.get_text(" ", strip=True))
            categories.append({"label": label, "url": full})

    # De-dup by URL
    dedup: Dict[str, Dict[str, Any]] = {}
    for c in categories:
        dedup[c["url"]] = c
    return list(dedup.values())


def scrape_task3_category(url: str, timeout: int = 30, user_agent: Optional[str] = None) -> Dict[str, Any]:
    session = _get_session(user_agent=user_agent)
    html = _fetch_html(session, url, timeout=timeout, retries=3)
    soup = BeautifulSoup(html, "html.parser")

    return {
        "url": url,
        "scraped_at": datetime.utcnow().isoformat() + "Z",
        "page_title": _clean_text(soup.title.get_text(strip=True)) if soup.title else "",
        "items": _extract_task3_subject_answers(soup),
    }


def collect_task2_correction_pages(task2_index_url: str, timeout: int = 30, user_agent: Optional[str] = None) -> List[Dict[str, Any]]:
    session = _get_session(user_agent=user_agent)
    html = _fetch_html(session, task2_index_url, timeout=timeout, retries=3)
    soup = BeautifulSoup(html, "html.parser")

    # On this page, Task 2 month blocks appear as headings like "Février 2026" followed by
    # an anchor "Voir la correction" to a URL like ...-correction-expression-orale-tache-2-pro/
    candidates: List[Dict[str, Any]] = []
    for a in soup.select('a[href]'):
        href = a.get("href")
        if not href:
            continue
        full = urljoin(task2_index_url, href)
        path = urlparse(full).path.lower()
        if "correction-expression-orale" not in path or "tache-2" not in path:
            continue
        if "reussir-tcfcanada.com" not in full:
            continue

        # Try to infer label from nearby heading
        label = _clean_text(a.get_text(" ", strip=True))
        heading = None
        for prev in a.find_all_previous(["h1", "h2", "h3", "h4"], limit=6):
            t = _clean_text(prev.get_text(" ", strip=True))
            if _month_key_from_label(t):
                heading = t
                break
        if heading:
            label = heading

        key = _month_key_from_label(label)
        if not key or not _should_include_month(key):
            continue

        candidates.append({"label": label, "year": key[0], "month": key[1], "url": full})

    # De-dup by URL
    dedup: Dict[str, Dict[str, Any]] = {}
    for c in candidates:
        dedup[c["url"]] = c
    results = list(dedup.values())
    results.sort(key=lambda x: (x["year"], x["month"]))
    return results


def batch_scrape_task2(task2_index_url: str, out_dir: str, timeout: int = 30, user_agent: Optional[str] = None) -> Dict[str, Any]:
    out_base = Path(out_dir)
    out_base.mkdir(parents=True, exist_ok=True)

    pages = collect_task2_correction_pages(task2_index_url, timeout=timeout, user_agent=user_agent)
    summary: Dict[str, Any] = {"index_url": task2_index_url, "count": len(pages), "items": []}

    for p in pages:
        try:
            data = scrape(p["url"], timeout=timeout, user_agent=user_agent)
            data["month_label"] = p["label"]
            data["correction_url"] = p["url"]

            filename = f"{p['year']}-{p['month']:02d}_{_slugify_filename(p['label'])}.json"
            out_path = out_base / filename
            out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

            summary["items"].append({**p, "output": str(out_path), "error": None})
        except Exception as e:
            summary["items"].append({**p, "output": None, "error": str(e)})

    return summary


def collect_month_pages(index_url: str, timeout: int = 30, user_agent: Optional[str] = None) -> List[Dict[str, Any]]:
    session = _get_session(user_agent=user_agent)
    html = _fetch_html(session, index_url, timeout=timeout, retries=3)
    soup = BeautifulSoup(html, "html.parser")

    results: List[Dict[str, Any]] = []
    for a in soup.select('a[href]'):
        label = _clean_text(a.get_text(" ", strip=True))
        key = _month_key_from_label(label)
        if not key or not _should_include_month(key):
            continue
        href = a.get("href")
        if not href:
            continue
        url = urljoin(index_url, href)
        results.append({"label": label, "year": key[0], "month": key[1], "url": url})

    # De-dup by URL
    dedup: Dict[str, Dict[str, Any]] = {}
    for r in results:
        dedup[r["url"]] = r
    results = list(dedup.values())

    results.sort(key=lambda x: (x["year"], x["month"]))
    return results


def find_correction_page(month_page_url: str, timeout: int = 30, user_agent: Optional[str] = None) -> Optional[str]:
    session = _get_session(user_agent=user_agent)
    html = _fetch_html(session, month_page_url, timeout=timeout, retries=3)
    soup = BeautifulSoup(html, "html.parser")

    # Typical pattern on site: month page contains links like:
    # - ...-correction-expression-orale-tache-2-pro/
    # - ...-correction-expression-orale-tache-3-pro/
    candidates: List[str] = []
    for a in soup.select('a[href]'):
        href = a.get("href")
        if not href:
            continue
        full = urljoin(month_page_url, href)
        path = urlparse(full).path.lower()
        if "correction" in path and "expression-orale" in path and "tache" in path:
            candidates.append(full)

    # Prefer task-2 correction if present, otherwise first candidate
    for c in candidates:
        if re.search(r"tache[-_ ]?2", c.lower()):
            return c
    return candidates[0] if candidates else None


def batch_scrape(index_url: str, out_dir: str, timeout: int = 30, user_agent: Optional[str] = None) -> Dict[str, Any]:
    out_base = Path(out_dir)
    out_base.mkdir(parents=True, exist_ok=True)

    months = collect_month_pages(index_url, timeout=timeout, user_agent=user_agent)
    summary: Dict[str, Any] = {"index_url": index_url, "count": len(months), "items": []}

    for m in months:
        label = m["label"]
        month_page_url = m["url"]
        correction_url = find_correction_page(month_page_url, timeout=timeout, user_agent=user_agent)
        if not correction_url:
            summary["items"].append({**m, "correction_url": None, "output": None, "error": "correction_url_not_found"})
            continue

        try:
            data = scrape(correction_url, timeout=timeout, user_agent=user_agent)
            data["month_label"] = label
            data["month_page_url"] = month_page_url
            data["correction_url"] = correction_url

            filename = f"{m['year']}-{m['month']:02d}_{_slugify_filename(label)}.json"
            out_path = out_base / filename
            out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

            summary["items"].append({**m, "correction_url": correction_url, "output": str(out_path), "error": None})
        except Exception as e:
            summary["items"].append({**m, "correction_url": correction_url, "output": None, "error": str(e)})

    return summary


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=None)
    parser.add_argument("--out", default="output_reussir_tcfcanada.json")
    parser.add_argument(
        "--index-url",
        default=None,
        help="If provided, batch scrape from index page (e.g. https://reussir-tcfcanada.com/expression-orale/)",
    )
    parser.add_argument(
        "--out-dir",
        default="outputs",
        help="Output directory for batch mode (one JSON per month)",
    )
    parser.add_argument(
        "--task3-index-url",
        default=None,
        help="If provided, scrape task-3 category corrections from this index (e.g. https://reussir-tcfcanada.com/correction-des-sujets-dexpression-orale-v-pro/)",
    )
    parser.add_argument(
        "--task3-out",
        default="task3_subject_answers.json",
        help="Output JSON for task-3 category scrape mode",
    )
    parser.add_argument(
        "--task2-index-url",
        default=None,
        help="If provided, batch scrape Task 2 month correction pages from this index (e.g. https://reussir-tcfcanada.com/correction-des-sujets-dexpression-orale-v-pro/)",
    )
    parser.add_argument(
        "--task2-out-dir",
        default="task2_outputs",
        help="Output directory for Task 2 batch mode (one JSON per month)",
    )
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args(argv)

    if args.task2_index_url:
        summary = batch_scrape_task2(args.task2_index_url, args.task2_out_dir, timeout=args.timeout)
        summary_path = Path(args.task2_out_dir) / "_summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Saved summary: {summary_path}")
        ok = sum(1 for it in summary["items"] if not it.get("error"))
        print(f"Task2 batch done. OK: {ok}/{summary['count']}")
        return 0

    if args.task3_index_url:
        categories = collect_task3_category_pages(args.task3_index_url, timeout=args.timeout)
        out_path = Path(args.task3_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        all_data: Dict[str, Any] = {
            "index_url": args.task3_index_url,
            "scraped_at": datetime.utcnow().isoformat() + "Z",
            "categories": [],
        }
        for c in categories:
            try:
                cat_data = scrape_task3_category(c["url"], timeout=args.timeout)
                cat_data["category"] = c.get("label")
                all_data["categories"].append(cat_data)
            except Exception as e:
                all_data["categories"].append({"category": c.get("label"), "url": c["url"], "error": str(e), "items": []})

        out_path.write_text(json.dumps(all_data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Saved: {out_path}")
        ok = sum(1 for c in all_data["categories"] if not c.get("error"))
        print(f"Task3 done. OK: {ok}/{len(all_data['categories'])}")
        return 0

    if args.index_url:
        summary = batch_scrape(args.index_url, args.out_dir, timeout=args.timeout)
        summary_path = Path(args.out_dir) / "_summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Saved summary: {summary_path}")
        ok = sum(1 for it in summary["items"] if not it.get("error"))
        print(f"Batch done. OK: {ok}/{summary['count']}")
        return 0

    url = args.url or "https://reussir-tcfcanada.com/fevrier-2026-correction-expression-orale-tache-2-pro/"
    data = scrape(url, timeout=args.timeout)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    sections = data.get("sections", [])
    print(f"Saved: {out_path}")
    print(f"Sections: {len(sections)}")
    for i, s in enumerate(sections[:5], start=1):
        print(f"  {i}. {s.get('title','')[:120]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
