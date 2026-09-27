"""
尾盘最终交易决策 14:40-14:45
锁定今日利润，决定过夜仓位
超过1000字
"""
import pathlib



class Moment4Prompt:
    """14:40尾盘最终决策Prompt - 1000字以上"""

    SEARCH_QUERIES = [
        "A股尾盘异动 14:30后走势 最后15分钟",
        "今日北向资金最终净流入流出统计",
        "今日主力资金最终流入流出板块排名",
        "今日ETF最终涨跌幅排名 全天统计",
        "今日涨停跌停最终统计 炸板率 市场情绪",
        "今日成交量最终数据 与昨日对比 放量缩量",
        "近5天行业走势回顾 资金流向趋势变化",
        "盘后可能发布消息 政策预告 A股盘后",
        "明日A股预测 机构观点 明日走势预判",
        "隔夜美股期货 欧股开盘 外盘最新环境",
        "今日龙虎榜预告 主力资金进出统计",
        "明日A股解禁 新股申购 资金面压力"
    ]

    _PROMPT_DIR = pathlib.Path(__file__).resolve().parent
    PROMPT = (_PROMPT_DIR / 'moment_4_closing.txt').read_text(encoding='utf-8').strip()