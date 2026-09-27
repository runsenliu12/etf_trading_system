"""
研报提供器（findtruman 优先，已彻底移除联网搜索）
================================================
研报文件本应由 WorkBuddy 定时器（09:00 / 12:45）生成并写入
logs/research/research_{label}_{date}.md。本模块保证即使 WorkBuddy 没生成，
也能用 iris.findtruman.io 结构化行业研报「自愈」补一份，绝不裸奔。

数据源（已彻底移除 DuckDuckGo / 9 大平台 chat_search 等联网搜索）：
  - 主源：WorkBuddy 定时器生成的今日研报文件
  - 兜底：iris.findtruman.io 量化行业研报 API（结构化券商研报，无需 Key）

不再使用任何联网搜索，避免返回无关/垃圾结果（如吃瓜网、youtube 等）。
"""
from datetime import datetime
from pathlib import Path
from loguru import logger

from search.research_mapping import scan_research_for_etfs, build_mapping_markdown

ROOT = Path(__file__).resolve().parent.parent
RESEARCH_DIR = ROOT / "logs" / "research"

# 同一进程内避免重复爬取
_cache: dict = {}

# 研报被视为「有效」必须出现的章节标记
_VALID_MARKERS = ("国外走势", "## iris", "研报聚合", "板块涨跌榜", "| 行业 |")


def ensure_research(label: str, ai_client=None) -> str:
    """
    返回指定 label（morning/afternoon）的研报文本。

    优先级：
      1. WorkBuddy 已生成的「今日有效文件」 → 直接用
      2. 缺失/为空/过旧 → 现场用 iris.findtruman.io 结构化研报兜底生成并落盘
      3. 兜底也失败 → 返回空串，由调用方决定是否降级交易（不阻塞交易）
    """
    today = datetime.now().strftime("%Y-%m-%d")
    path = RESEARCH_DIR / f"research_{label}_{today}.md"

    # 1) 优先：WorkBuddy 已生成的今日有效文件
    if path.exists():
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"[研报] 读取 {path.name} 失败: {e}")
            content = ""
        if _is_valid(content):
            logger.info(f"[研报] 使用今日已生成研报文件: {path.name}")
            return content

    # 2) 兜底：WorkBuddy 没生成 → 直接爬 iris.findtruman.io 生成今日研报
    cache_key = (label, today)
    if cache_key in _cache:
        return _cache[cache_key]

    logger.warning(
        f"[研报] WorkBuddy 未生成 {label} 研报（缺失/为空/过旧），"
        f"改用 iris.findtruman.io 结构化研报兜底..."
    )
    generated = _generate_from_iris(label)
    if generated:
        try:
            RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(generated, encoding="utf-8")
            logger.info(f"[研报] iris 研报已写入: {path.name}")
        except Exception as e:
            logger.error(f"[研报] iris 文件写入失败: {e}")
        _cache[cache_key] = generated
        return generated

    # 3) 兜底也失败
    logger.error(
        f"[研报缺失] {label} 研报未能获取（WorkBuddy 未生成且 iris 兜底失败），"
        f"AI 将在无研报上下文下决策。"
    )
    _cache[cache_key] = ""
    return ""


def ensure_research_with_mapping(label: str, ai_client=None):
    """
    返回指定 label 的研报文本，并在末尾追加「研报→ETF 自动映射」小节。

    返回: (research_text_with_mapping, mapping_markdown)
    - research_text_with_mapping: 原研报 + 映射小节，直接喂给模型 / 注入 prompt
    - mapping_markdown: 单独的小节文本，供日报/公众号引用

    映射仅追加在内存文本中，不写回研报文件，避免重复累积。
    """
    text = ensure_research(label, ai_client)
    if not text:
        return "", ""
    mapping = scan_research_for_etfs(text)
    md = build_mapping_markdown(mapping)
    if md:
        return text + "\n\n" + md, md
    return text, ""


def precrawl_research(label: str) -> bool:
    """
    开盘前预爬：若今日研报文件缺失/无效，提前用 iris.findtruman.io 生成，
    保证 M1（09:25）决策时已有研报可用，不依赖 WorkBuddy 定时器是否成功。

    已存在有效文件则跳过（不覆盖 WorkBuddy 生成的更丰富内容）。
    返回 True 表示当前已有可用研报（无论来源）。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    path = RESEARCH_DIR / f"research_{label}_{today}.md"

    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8")
        except Exception:
            existing = ""
        if _is_valid(existing):
            logger.info(f"[研报预爬] {label} 研报已存在且有效，跳过预爬")
            return True

    logger.info(f"[研报预爬] {label} 研报缺失/无效，启动 iris 预爬...")
    generated = _generate_from_iris(label)
    if generated:
        try:
            RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(generated, encoding="utf-8")
            logger.info(f"[研报预爬] iris 研报已预爬写入: {path.name}")
            return True
        except Exception as e:
            logger.error(f"[研报预爬] 写入失败: {e}")
            return False
    logger.error(f"[研报预爬] {label} iris 预爬失败，M1 决策将在无研报下运行")
    return False


def _is_valid(content: str) -> bool:
    """研报文本是否有效：非空、有核心章节标记、长度足够。"""
    if not content or len(content.strip()) < 150:
        return False
    return any(m in content for m in _VALID_MARKERS)


def _generate_from_iris(label: str) -> str:
    """
    用 iris.findtruman.io 结构化行业研报生成今日研报（主内容，不再混合联网搜索）。
    任一步失败都不抛异常，仅降级返回空串。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        from search.iris_scraper import fetch_iris_reports, format_markdown
        reports = fetch_iris_reports(40)
        if not reports:
            return ""
        parts = [
            f"# {today} 行业研报聚合 ({label}) [iris.findtruman.io]",
            "",
            "> 数据源：iris.findtruman.io 量化行业研报 API（结构化券商行业研报，已取代联网搜索）。",
            "",
            format_markdown(reports),
        ]
        return "\n".join(parts)
    except Exception as e:
        logger.error(f"[研报] iris 生成失败: {e}")
        return ""
