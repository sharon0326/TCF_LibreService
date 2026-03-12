import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from llm_service import call_llm


logger = logging.getLogger("webscripting.classify")


TAXONOMY: Dict[str, List[str]] = {
    "家庭生活": [
        "家庭生活",
        "搬家",
        "宠物照顾",
        "儿童看护",
        "住房租赁/购买",
        "日常家务",
    ],
    "工作职场": [
        "工作职场",
        "新工作/入职",
        "同事关系",
        "工作内容/职责",
        "职业发展/培训",
        "工作条件（工资、工时）",
    ],
    "社交聚会": [
        "社交聚会",
        "婚礼",
        "生日",
        "节日/新年",
        "邀请/聚会",
        "邻里活动",
    ],
    "旅游度假": [
        "旅游度假",
        "旅行计划/咨询",
        "住宿（酒店、民宿、出租）",
        "景点/活动推荐",
        "交通方式",
        "周末游",
        "海边/山区/乡村度假",
        "游乐园/主题公园",
    ],
    "体育运动": [
        "体育运动",
        "健身/瑜伽",
        "游泳",
        "徒步/登山",
        "滑雪/冬季运动",
        "骑行",
        "运动俱乐部/课程",
    ],
    "文化艺术": [
        "文化艺术",
        "电影",
        "音乐会/音乐节",
        "戏剧/表演",
        "博物馆/展览",
        "摄影",
        "写作/阅读",
    ],
    "教育学习": [
        "教育学习",
        "语言课程",
        "音乐课（吉他、钢琴等）",
        "兴趣班（烹饪、手工艺）",
        "留学/交换项目",
        "课外活动（儿童）",
    ],
    "日常生活": [
        "日常生活",
        "邻里信息/社区",
        "购物/商业",
        "餐饮/餐厅",
        "公共交通",
        "个人健康/医疗",
        "保险/事故",
    ],
    "志愿服务": [
        "志愿服务",
        "协会/慈善",
        "动物保护",
        "社区服务",
    ],
    "交通出行": [
        "交通出行",
        "公共交通（公交、地铁）",
        "车辆租赁/买卖",
        "骑行/自行车",
        "出行规划",
    ],
    "其他": [
        "其他",
    ],
}


def _iter_titles(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = payload.get("items")
    if not isinstance(items, list):
        return []

    out: List[Dict[str, Any]] = []
    for i, it in enumerate(items):
        titles = it.get("titles")
        if not isinstance(titles, list):
            continue
        for j, t in enumerate(titles):
            if not isinstance(t, str):
                continue
            title = t.strip()
            if not title:
                continue
            out.append({
                "item_index": i,
                "title_index": j,
                "year": it.get("year"),
                "month": it.get("month"),
                "source_file": it.get("source_file"),
                "title": title,
            })
    return out


def _build_prompt(title: str) -> str:
    return (
        "你是一个用于法语口语考试（TCF Canada Expression Orale Task 2）的题目分类器。\n"
        "我会给你一条法语题目（通常是角色扮演情境）。你的任务是把它归类到我提供的类目体系中，输出一级类与二级类。\n\n"
        "类目体系（一级类 -> 允许的二级类）：\n"
        f"{json.dumps(TAXONOMY, ensure_ascii=False)}\n\n"
        "规则：\n"
        "1) primary 必须是上面 taxonomy 的一个一级类。\n"
        "2) secondary 必须是该 primary 下允许的一个二级类。\n"
        "3) 如果无法判断，primary=其他 且 secondary=其他。\n"
        "4) 只输出严格 JSON（不要代码块、不要解释、不要多余文本）。\n\n"
        "输出 JSON 格式：{\"primary\": \"...\", \"secondary\": \"...\"}\n\n"
        f"题目：{title}"
    )


def _normalize_category(raw: str) -> Dict[str, str]:
    try:
        obj = json.loads((raw or "").strip())
        if not isinstance(obj, dict):
            raise ValueError("not a dict")
        primary = str(obj.get("primary", "")).strip()
        secondary = str(obj.get("secondary", "")).strip()
        if not primary or not secondary:
            raise ValueError("missing fields")
        if primary not in TAXONOMY:
            raise ValueError("invalid primary")
        if secondary not in TAXONOMY.get(primary, []):
            raise ValueError("invalid secondary")
        return {"primary": primary, "secondary": secondary}
    except Exception:
        return {"primary": "其他", "secondary": "其他"}


async def _classify_one(title: str) -> Dict[str, Any]:
    prompt = _build_prompt(title)
    raw = await call_llm(prompt)
    norm = _normalize_category(raw)
    category = f"{norm['primary']} / {norm['secondary']}"
    return {"category": category, "raw": raw}


async def classify_all(records: List[Dict[str, Any]], *, concurrency: int = 5) -> List[Dict[str, Any]]:
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _run(rec: Dict[str, Any]) -> Dict[str, Any]:
        async with sem:
            result = await _classify_one(rec["title"])
            return {**rec, **result}

    tasks = [asyncio.create_task(_run(r)) for r in records]
    return await asyncio.gather(*tasks)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        default="task2_titles_only.json",
        help="Input JSON path (default: task2_titles_only.json)",
    )
    parser.add_argument(
        "--output",
        default="task2_titles_classified.json",
        help="Output JSON path (default: task2_titles_classified.json)",
    )
    parser.add_argument("--concurrency", type=int, default=5)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

    in_path = Path(args.input)
    payload = json.loads(in_path.read_text(encoding="utf-8"))
    records = _iter_titles(payload)

    logger.info("Loaded %s titles from %s", len(records), in_path)

    results = asyncio.run(classify_all(records, concurrency=args.concurrency))

    out_payload = {
        "input": str(in_path),
        "count": len(results),
        "results": results,
    }
    out_path = Path(args.output)
    out_path.write_text(json.dumps(out_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info("Saved %s results to %s", len(results), out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
