"""
上午确认交易决策 10:40-10:45
验证开盘判断，调整仓位
超过1000字
"""
import pathlib



class Moment2Prompt:
    """10:40上午确认Prompt - 1000字以上"""

    SEARCH_QUERIES = [
        "今日A股实时涨跌 领涨领跌板块 10:30",
        "北向资金今日实时净流入流出 最新数据",
        "今日主力资金净流入流出板块排名",
        "今日ETF涨跌幅排名 成交量排名",
        "今日涨停跌停家数 市场赚钱效应",
        "今日成交量与昨日同比 放量缩量",
        "今日盘中异动 快速拉升 突然跳水",
        "近3天行业持续性 今日是否延续",
        "今日亚太股市 港股表现 对A股影响",
        "盘中突发消息 政策 公告 A股"
    ]

    _PROMPT_DIR = pathlib.Path(__file__).resolve().parent
    PROMPT = (_PROMPT_DIR / 'moment_2_morning.txt').read_text(encoding='utf-8').strip()