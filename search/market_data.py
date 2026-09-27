"""
市场数据收集器
用 QMT 的 xtdata 替代 DuckDuckGo 联网搜索，直接从行情接口获取：
- ETF 近 N 天涨跌幅排名
- 板块轮动分析
- 主要指数行情
- 跨境 ETF（外盘代理）
- 实时盘面快照
"""
from typing import Dict, List, Optional
from datetime import datetime
from loguru import logger
from config.settings import Config


class MarketDataCollector:
    """用 QMT 行情数据生成市场分析报告，替代联网搜索"""

    def __init__(self, qmt_client):
        """
        Args:
            qmt_client: QMTClient 实例（必须已连接）
        """
        self.qmt = qmt_client

    def collect(self, moment: str) -> str:
        """
        收集市场数据并格式化为文本，直接替代 search_results 的内容

        Args:
            moment: 决策时刻 moment_1~4

        Returns:
            格式化的市场分析报告文本
        """
        sections = []

        # 1. 主要指数行情
        sections.append(self._format_indices())

        # 2. ETF 当日涨跌排名（Top10 / Bottom10）
        sections.append(self._format_etf_ranking())

        # 3. 板块轮动分析
        sections.append(self._format_sector_rotation())

        # 4. 跨境ETF（外盘代理）
        sections.append(self._format_cross_border())

        # 5. 近 N 天 ETF 涨跌幅对比
        sections.append(self._format_multi_day_performance())

        # 6. 当前时刻的特别关注
        sections.append(self._format_moment_focus(moment))

        return '\n'.join(sections)

    def _format_indices(self) -> str:
        """格式化主要指数行情"""
        lines = [
            f"\n{'='*60}",
            f"【主要市场指数行情】",
            f"{'='*60}",
        ]
        try:
            indices = self.qmt.get_index_data()
            if indices:
                for code, data in indices.items():
                    direction = "↑" if data['change_pct'] > 0 else "↓" if data['change_pct'] < 0 else "→"
                    lines.append(
                        f"  {data['name']}({code}): {data['price']:.2f} "
                        f"{direction} {data['change_pct']:+.2f}% "
                        f"成交额 {data['amount'] / 1e8:.1f}亿"
                    )
            else:
                lines.append("  (指数数据获取中)")
        except Exception as e:
            lines.append(f"  (指数数据获取失败: {e})")
        return '\n'.join(lines)

    def _format_etf_ranking(self) -> str:
        """格式化 ETF 当日涨跌排名"""
        lines = [
            f"\n{'='*60}",
            f"【ETF 当日涨跌幅排名】",
            f"{'='*60}",
        ]
        try:
            etf_data = self.qmt.get_all_etf_data()
            if etf_data:
                # 按涨跌幅排序
                sorted_etfs = sorted(
                    etf_data.items(),
                    key=lambda x: x[1]['change_pct'],
                    reverse=True
                )

                # Top10 涨幅
                lines.append("\n  --- 涨幅前10 ---")
                for code, data in sorted_etfs[:10]:
                    name = Config.ETF_POOL.get(code, code)
                    sector = Config.get_sector_by_code(code)
                    lines.append(
                        f"  {name}({code}) [{sector}]: "
                        f"{data['last_price']:.3f} {data['change_pct']:+.2f}% "
                        f"量 {data['volume']:.0f}"
                    )

                # Bottom10 跌幅
                lines.append("\n  --- 跌幅前10 ---")
                for code, data in sorted_etfs[-10:]:
                    name = Config.ETF_POOL.get(code, code)
                    sector = Config.get_sector_by_code(code)
                    lines.append(
                        f"  {name}({code}) [{sector}]: "
                        f"{data['last_price']:.3f} {data['change_pct']:+.2f}% "
                        f"量 {data['volume']:.0f}"
                    )

                # 统计
                up_count = sum(1 for _, d in sorted_etfs if d['change_pct'] > 0)
                down_count = sum(1 for _, d in sorted_etfs if d['change_pct'] < 0)
                flat_count = len(sorted_etfs) - up_count - down_count
                avg_change = sum(d['change_pct'] for _, d in sorted_etfs) / len(sorted_etfs) if sorted_etfs else 0
                lines.append(
                    f"\n  统计: 上涨{up_count}只 下跌{down_count}只 平盘{flat_count}只 "
                    f"平均涨跌{avg_change:+.2f}%"
                )
            else:
                lines.append("  (ETF数据获取中)")
        except Exception as e:
            lines.append(f"  (ETF排名获取失败: {e})")
        return '\n'.join(lines)

    def _format_sector_rotation(self) -> str:
        """格式化板块轮动分析"""
        lines = [
            f"\n{'='*60}",
            f"【板块轮动分析（近3天）】",
            f"{'='*60}",
        ]
        try:
            ranking = self.qmt.get_sector_ranking(days=3)
            if ranking:
                lines.append(f"\n  {'板块':<10} {'近1天':>8} {'近3天':>8} {'近5天':>8} {'ETF数':>5}")
                lines.append(f"  {'-'*50}")

                for sector_info in ranking:
                    lines.append(
                        f"  {sector_info['sector']:<10} "
                        f"{sector_info['avg_change_1d']:>+7.2f}% "
                        f"{sector_info['avg_change_3d']:>+7.2f}% "
                        f"{sector_info['avg_change_5d']:>+7.2f}% "
                        f"{sector_info['count']:>5}"
                    )

                # 分析连续性
                strong_sectors = [s for s in ranking if s['avg_change_1d'] > 0 and s['avg_change_3d'] > 0]
                weak_sectors = [s for s in ranking if s['avg_change_1d'] < 0 and s['avg_change_3d'] < 0]

                lines.append(f"\n  --- 连续强势板块（1天+3天均涨）---")
                if strong_sectors:
                    for s in strong_sectors[:5]:
                        lines.append(f"  {s['sector']}: 1天{s['avg_change_1d']:+.2f}% 3天{s['avg_change_3d']:+.2f}%")
                else:
                    lines.append("  (无)")

                lines.append(f"\n  --- 连续弱势板块（1天+3天均跌）---")
                if weak_sectors:
                    for s in weak_sectors[:5]:
                        lines.append(f"  {s['sector']}: 1天{s['avg_change_1d']:+.2f}% 3天{s['avg_change_3d']:+.2f}%")
                else:
                    lines.append("  (无)")
            else:
                lines.append("  (板块数据获取中)")
        except Exception as e:
            lines.append(f"  (板块轮动分析失败: {e})")
        return '\n'.join(lines)

    def _format_cross_border(self) -> str:
        """格式化跨境ETF数据（外盘代理）"""
        lines = [
            f"\n{'='*60}",
            f"【跨境ETF行情（外盘代理参考）】",
            f"{'='*60}",
        ]
        try:
            cross_data = self.qmt.get_cross_border_data()
            if cross_data:
                lines.append("  (通过跨境ETF表现间接判断外盘走势)")
                lines.append("")
                for code, data in cross_data.items():
                    direction = "↑" if data['change_pct'] > 0 else "↓" if data['change_pct'] < 0 else "→"
                    lines.append(
                        f"  {data['name']}({code}): {data['price']:.3f} "
                        f"{direction} {data['change_pct']:+.2f}% "
                        f"(近5天 {data['trend_5d']:+.2f}%)"
                    )

                # 外盘综合判断
                nasdaq = cross_data.get('513100.SH', {})
                sp500 = cross_data.get('513500.SH', {})
                if nasdaq and sp500:
                    avg = (nasdaq.get('change_pct', 0) + sp500.get('change_pct', 0)) / 2
                    if avg > 0.5:
                        lines.append(f"\n  外盘综合: 美股上涨 (纳指+标普平均 {avg:+.2f}%)，利好A股开盘情绪")
                    elif avg < -0.5:
                        lines.append(f"\n  外盘综合: 美股下跌 (纳指+标普平均 {avg:+.2f}%)，注意A股开盘承压")
                    else:
                        lines.append(f"\n  外盘综合: 美股波动不大 (纳指+标普平均 {avg:+.2f}%)，对A股影响有限")
            else:
                lines.append("  (跨境ETF数据获取中)")
        except Exception as e:
            lines.append(f"  (跨境ETF数据获取失败: {e})")
        return '\n'.join(lines)

    def _format_multi_day_performance(self) -> str:
        """格式化近5天ETF涨跌幅对比"""
        lines = [
            f"\n{'='*60}",
            f"【近5天ETF涨跌幅对比（Top15 / Bottom15）】",
            f"{'='*60}",
        ]
        try:
            perf = self.qmt.get_multi_day_performance(days=5)
            if perf:
                lines.append(f"\n  {'名称':<12} {'代码':<12} {'板块':<8} {'近1天':>8} {'近3天':>8} {'近5天':>8}")
                lines.append(f"  {'-'*65}")

                # Top15
                lines.append("\n  --- 近5天涨幅前15 ---")
                for item in perf[:15]:
                    lines.append(
                        f"  {item['name']:<12} {item['code']:<12} {item['sector']:<8} "
                        f"{item['change_1d']:>+7.2f}% {item['change_3d']:>+7.2f}% {item['change_5d']:>+7.2f}%"
                    )

                # Bottom15
                lines.append("\n  --- 近5天跌幅前15 ---")
                for item in perf[-15:]:
                    lines.append(
                        f"  {item['name']:<12} {item['code']:<12} {item['sector']:<8} "
                        f"{item['change_1d']:>+7.2f}% {item['change_3d']:>+7.2f}% {item['change_5d']:>+7.2f}%"
                    )

                # 连续上涨/下跌统计
                up_3d = [e for e in perf if e['change_1d'] > 0 and e['change_3d'] > 0 and e['change_5d'] > 0]
                down_3d = [e for e in perf if e['change_1d'] < 0 and e['change_3d'] < 0 and e['change_5d'] < 0]
                lines.append(f"\n  连续上涨(1天+3天+5天均涨): {len(up_3d)}只")
                if up_3d:
                    for e in up_3d[:5]:
                        lines.append(f"    {e['name']}({e['code']}) 1天{e['change_1d']:+.2f}% 3天{e['change_3d']:+.2f}% 5天{e['change_5d']:+.2f}%")
                lines.append(f"\n  连续下跌(1天+3天+5天均跌): {len(down_3d)}只")
                if down_3d:
                    for e in down_3d[:5]:
                        lines.append(f"    {e['name']}({e['code']}) 1天{e['change_1d']:+.2f}% 3天{e['change_3d']:+.2f}% 5天{e['change_5d']:+.2f}%")
            else:
                lines.append("  (历史数据获取中)")
        except Exception as e:
            lines.append(f"  (多日涨跌幅获取失败: {e})")
        return '\n'.join(lines)

    def _format_moment_focus(self, moment: str) -> str:
        """根据不同时刻生成特别关注信息"""
        lines = [
            f"\n{'='*60}",
            f"【当前时刻市场快照】",
            f"{'='*60}",
        ]
        now = datetime.now()
        lines.append(f"  时间: {now.strftime('%Y-%m-%d %H:%M:%S')}")

        try:
            etf_data = self.qmt.get_all_etf_data()
            if etf_data:
                # 成交额统计
                total_amount = sum(d['amount'] for d in etf_data.values())
                total_volume = sum(d['volume'] for d in etf_data.values())
                lines.append(f"  ETF池总成交额: {total_amount / 1e8:.2f}亿元")
                lines.append(f"  ETF池总成交量: {total_volume / 1e4:.0f}万手")

                # 市场温度
                up = sum(1 for d in etf_data.values() if d['change_pct'] > 0)
                down = sum(1 for d in etf_data.values() if d['change_pct'] < 0)
                avg_chg = sum(d['change_pct'] for d in etf_data.values()) / len(etf_data) if etf_data else 0

                if avg_chg > 1:
                    temperature = "偏热"
                elif avg_chg > 0.3:
                    temperature = "偏暖"
                elif avg_chg > -0.3:
                    temperature = "中性"
                elif avg_chg > -1:
                    temperature = "偏冷"
                else:
                    temperature = "偏寒"

                lines.append(f"  市场温度: {temperature} (平均涨跌{avg_chg:+.2f}%, 上涨{up}/下跌{down})")

                # 按时刻补充
                if moment == 'moment_1':
                    lines.append("\n  [集合竞价关注点]")
                    # 高开/低开统计
                    high_open = sum(1 for d in etf_data.values() if d['change_pct'] > 0.5)
                    low_open = sum(1 for d in etf_data.values() if d['change_pct'] < -0.5)
                    lines.append(f"  高开(>0.5%): {high_open}只  低开(<-0.5%): {low_open}只")

                elif moment == 'moment_2':
                    lines.append("\n  [上午盘面确认]")
                    # 看是否有冲高回落的
                    for code, d in etf_data.items():
                        if d['high'] > 0 and d['last_price'] > 0:
                            high_pct = (d['high'] / d['last_close'] - 1) * 100 if d['last_close'] else 0
                            if high_pct - d['change_pct'] > 1 and d['change_pct'] < 0:
                                name = Config.ETF_POOL.get(code, code)
                                lines.append(f"  冲高回落: {name}({code}) 最高涨{high_pct:+.2f}% 现涨{d['change_pct']:+.2f}%")

                elif moment == 'moment_4':
                    lines.append("\n  [尾盘决策关注]")
                    # 尾盘表现
                    for code, d in etf_data.items():
                        name = Config.ETF_POOL.get(code, code)
                        if d['change_pct'] > 2:
                            lines.append(f"  强势: {name}({code}) {d['change_pct']:+.2f}%")
                        elif d['change_pct'] < -2:
                            lines.append(f"  弱势: {name}({code}) {d['change_pct']:+.2f}%")
        except Exception as e:
            lines.append(f"  (快照数据获取失败: {e})")

        return '\n'.join(lines)
