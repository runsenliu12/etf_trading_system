"""
集合竞价交易决策 9:25-9:30
开盘价已出，直接下单
超过1000字的详细Prompt
"""
import pathlib



class Moment1Prompt:
    """9:25集合竞价Prompt - 1000字以上"""

    SEARCH_QUERIES = [
        "今日A股重大政策 突发消息 利好利空 2026",
        "隔夜美股涨跌 中概股 A50期货 人民币汇率 2026",
        "昨日北向资金净流入流出 近期趋势",
        "昨日行业涨跌幅排名 资金流入流出板块",
        "近3天A股行业轮动 板块持续性分析",
        "近5天各行业ETF涨跌幅对比",
        "今日盘前机构观点 A股市场情绪",
        "昨日龙虎榜 主力资金 机构动向",
        "昨日融资融券余额 杠杆资金变化",
        "今日A股解禁 新股申购 资金面压力"
    ]

    _PROMPT_DIR = pathlib.Path(__file__).resolve().parent
    PROMPT = (_PROMPT_DIR / 'moment_1_opening.txt').read_text(encoding='utf-8').strip()