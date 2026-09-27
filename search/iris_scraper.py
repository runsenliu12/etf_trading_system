"""
iris.findtruman.io 量化行业研报爬虫
=================================
作为 WorkBuddy 研报定时器的补充数据源，爬取 iris 量化平台的
「行业研究报告」结构化数据，生成 Markdown 表格，追加到研报文件。

与 9 大财经平台联网搜索互为补充：
- 联网搜索：覆盖广、偏观点/快讯
- iris API：结构化、偏券商正式行业研报（标题/行业/评级/机构/分析师）

用法：
  python search/iris_scraper.py --target morning      # 追加到今日盘前研报
  python search/iris_scraper.py --target afternoon    # 追加到今日午后研报
  python search/iris_scraper.py --print               # 仅打印不写入
  python search/iris_scraper.py --top 40              # 取最新 N 条
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

import requests

IRIS_URL = "https://iris.findtruman.io/quantitative_api/stock-selection/industry-research-reports"

# 项目根目录（脚本位于 search/ 下，上一级即根）
ROOT = Path(__file__).resolve().parent.parent
RESEARCH_DIR = ROOT / "logs" / "research"


def fetch_iris_reports(top_n: int = 30) -> list:
    """爬取 iris 行业研报 API，按发布日期倒序返回最新 top_n 条。"""
    resp = requests.get(IRIS_URL, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get("data", []) or []
    # 按发布日期倒序
    data.sort(key=lambda x: x.get("publishDate", ""), reverse=True)
    return data[:top_n]


def format_markdown(reports: list) -> str:
    """将研报列表格式化为「## iris 量化行业数据（补充）」小节 Markdown。"""
    lines = [
        "## iris 量化行业数据（补充）",
        "",
        "> 数据源：iris.findtruman.io 量化行业研报 API（结构化券商行业研报，作为 9 大平台联网搜索的补充）",
        "",
        "| 序号 | 行业 | 报告标题 | 评级 | 机构 | 分析师 | 日期 |",
        "|------|------|---------|------|------|--------|------|",
    ]
    for i, rep in enumerate(reports, 1):
        title = (rep.get("title") or "-").replace("|", "/")
        ind = rep.get("industryName") or "-"
        rating = rep.get("emRatingName") or rep.get("sRatingName") or "-"
        org = rep.get("orgSName") or rep.get("orgName") or "-"
        author = rep.get("researcher") or "-"
        date = (rep.get("publishDate") or "-")[:10]
        lines.append(f"| {i} | {ind} | {title} | {rating} | {org} | {author} | {date} |")
    lines.append("")
    lines.append(f"> 共获取 {len(reports)} 条最新行业研报（按发布日期倒序，结构化数据）")
    return "\n".join(lines)


def append_to_research(target: str = "morning", top_n: int = 30):
    today = datetime.now().strftime("%Y-%m-%d")
    path = RESEARCH_DIR / f"research_{target}_{today}.md"
    if not path.exists():
        return False, f"研报文件不存在: {path}（请先由 WorkBuddy 定时器生成基础研报）"
    # 去重：已包含 iris 小节则跳过，避免定时器重跑时重复追加
    existing = path.read_text(encoding="utf-8")
    if "## iris 量化行业数据（补充）" in existing:
        return True, f"已包含 iris 小节，跳过（{path}）"
    reports = fetch_iris_reports(top_n)
    md = format_markdown(reports)
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n\n" + md + "\n")
    return True, f"已追加 {len(reports)} 条 iris 行业研报到 {path}"


def main():
    parser = argparse.ArgumentParser(description="iris.findtruman.io 行业研报爬虫")
    parser.add_argument("--target", choices=["morning", "afternoon"], default="morning",
                        help="追加到盘前(morning)或午后(afternoon)研报文件")
    parser.add_argument("--top", type=int, default=30, help="取最新 N 条")
    parser.add_argument("--print", action="store_true", help="仅打印 Markdown，不写入文件")
    args = parser.parse_args()

    try:
        reports = fetch_iris_reports(args.top)
    except Exception as e:
        print(f"ERROR: 爬取 iris 失败: {e}", file=sys.stderr)
        sys.exit(1)

    md = format_markdown(reports)
    if args.print:
        print(md)
    else:
        ok, msg = append_to_research(args.target, args.top)
        print(msg)
        if not ok:
            sys.exit(2)


if __name__ == "__main__":
    main()
