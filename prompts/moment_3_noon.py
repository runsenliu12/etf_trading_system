"""
午盘转折交易决策 12:50-13:00
决定下午持仓策略，防范午后跳水
超过1000字
"""
import pathlib



class Moment3Prompt:
    """12:50午盘转折Prompt - 1000字以上"""

    SEARCH_QUERIES = [
        "A股午间公告 上市公司午间发布重大消息",
        "午间重大新闻 政策 国际事件 影响A股",
        "亚太股市午间表现 港股午盘涨跌 日股韩股",
        "北向资金上午净流入流出最终数据",
        "上午主力资金流入流出板块排名",
        "上午ETF涨跌幅排名 成交量排名",
        "上午涨停跌停统计 炸板率 市场赚钱效应",
        "上午成交量与昨日同时段对比 放量缩量",
        "近5天行业走势回顾 今天上午是否延续趋势",
        "A股午后走势规律 历史统计 变盘概率分析"
    ]

    _PROMPT_DIR = pathlib.Path(__file__).resolve().parent
    PROMPT = (_PROMPT_DIR / 'moment_3_noon.txt').read_text(encoding='utf-8').strip()