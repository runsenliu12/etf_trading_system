"""
研报 → ETF 映射扫描器
======================
把一份研报文本（联网搜索 + iris 结构化研报）扫描一遍，
提取其中涉及的行业/主题关键词，映射到 ETF_POOL 中可交易的 ETF，
并附带「提及次数 / 情绪倾向」，生成给模型看的映射小节。

用法：
  from search.research_mapping import scan_research_for_etfs, build_mapping_markdown
  mapping = scan_research_for_etfs(research_text)
  md = build_mapping_markdown(mapping)
"""
from config.etf_mapping import RESEARCH_ETF_RULES, SENTIMENT_LEXICON
from config.settings import Config


def _line_sentiment(line: str) -> int:
    """返回一行文本的情绪：+1 正面 / -1 负面 / 0 中性（按正负词计数取净符号）"""
    pos = sum(1 for w in SENTIMENT_LEXICON['pos'] if w in line)
    neg = sum(1 for w in SENTIMENT_LEXICON['neg'] if w in line)
    if pos > neg:
        return 1
    if neg > pos:
        return -1
    return 0


def scan_research_for_etfs(text: str) -> dict:
    """扫描研报文本，返回 {etf_code: {...}} 映射结果。"""
    if not text:
        return {}

    results = {}
    for line in text.splitlines():
        for keywords, codes, sector in RESEARCH_ETF_RULES:
            if not codes:
                continue  # 池内无直接标的（如石油）跳过
            if any(kw in line for kw in keywords):
                sent = _line_sentiment(line)
                for code in codes:
                    if code not in results:
                        results[code] = {
                            'code': code,
                            'name': Config.ETF_POOL.get(code, code),
                            'sectors': set(),
                            'matched_lines': 0,
                            'pos': 0,
                            'neg': 0,
                            'neu': 0,
                        }
                    r = results[code]
                    r['sectors'].add(sector)
                    r['matched_lines'] += 1
                    if sent > 0:
                        r['pos'] += 1
                    elif sent < 0:
                        r['neg'] += 1
                    else:
                        r['neu'] += 1

    # 收尾：计算情绪标签与净分
    for r in results.values():
        r['sectors'] = sorted(r['sectors'])
        r['score'] = r['pos'] - r['neg']
        if r['pos'] > r['neg']:
            r['sentiment'] = '正面'
        elif r['neg'] > r['pos']:
            r['sentiment'] = '负面'
        else:
            r['sentiment'] = '中性'

    return results


def build_mapping_markdown(mapping: dict) -> str:
    """把映射结果渲染为 Markdown 小节，注入 prompt / 研报文件。"""
    if not mapping:
        return ""

    # 排序：净分降序 → 提及次数降序
    rows = sorted(
        mapping.values(),
        key=lambda x: (x['score'], x['matched_lines']),
        reverse=True,
    )

    lines = [
        "## 研报→ETF 自动映射（供决策参考）",
        "",
        "> 以下由研报行业线索自动映射到本池可交易 ETF。情绪按关联研报行正负词计数得出；"
        "同一 ETF 可能对应多个板块。",
        "",
        "| ETF代码 | 名称 | 关联板块 | 提及 | 正面/负面 | 情绪 |",
        "|---------|------|---------|:----:|:--------:|:----:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['code']} | {r['name']} | {','.join(r['sectors'])} | "
            f"{r['matched_lines']} | {r['pos']}/{r['neg']} | {r['sentiment']} |"
        )
    lines.append("")
    return "\n".join(lines)


if __name__ == '__main__':
    # 简单自测：扫描一份示例研报
    sample = """
    | 半导体 | 电子行业周报：长鑫科技即将登陆科创板，谷歌再上调Capex指引 | 持有 |
    | 有色金属 | 铜库存去化，看好钨钼稀土涨价 | 买入 |
    | 银行 | 银行经营格局稳定有助风格延续 | 看好 |
    | 游戏/传媒 | 关注国产模型多模态进展及线下文娱消费 | 持有 |
    """
    m = scan_research_for_etfs(sample)
    print(build_mapping_markdown(m))
