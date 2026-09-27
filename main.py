"""
ETF T+1 自动交易系统 主程序
基于DeepSeek V4 + QMT
四个关键时刻决策
"""
import os
import time
import json
import schedule
from pathlib import Path
from datetime import datetime
from typing import Dict

from config.settings import Config
from prompts.base import TradingPrompts
from search.market_data import MarketDataCollector
from search.research_provider import ensure_research_with_mapping, precrawl_research
from ai.deepseek_client import DeepSeekClient
from trading.qmt_client import QMTClient
from strategy.executor import TradeExecutor
from utils.report_generator import generate_daily_report
from utils.logger import setup_logger

logger = setup_logger('MainSystem')

# 决策时刻的先后顺序（用于累计"前序执行回顾"，确保后续时刻能看到前面所有时刻的真实成交）。
# 仅包含 settings.TRADING_MOMENTS 中 enabled=True 的时刻，关闭的时刻自动从序列中剔除，
# 因此前序回顾也会正确跳过被关闭的时刻（如 14:30 只会看到 09:25）。
MOMENT_ORDER = [m for m in ['moment_1', 'moment_2', 'moment_3', 'moment_4']
                if Config.TRADING_MOMENTS.get(m, {}).get('enabled', True)]

# 时刻 -> 定时触发时间（仅 enabled 的时刻会被调度）
MOMENT_SCHEDULE = {
    'moment_1': '09:25',
    'moment_2': '10:30',
    'moment_3': '12:50',
    'moment_4': '14:30',
}


class ETFTradingSystem:
    """ETF交易系统"""

    def __init__(self):
        self.qmt = QMTClient()
        self.ai = DeepSeekClient()
        # 研报来源：WorkBuddy 定时器生成文件 + iris.findtruman.io 结构化兜底（research_provider）
        self.market_data = MarketDataCollector(self.qmt)
        self.executor = TradeExecutor(self.qmt)
        self.last_decisions: Dict[str, Dict] = {}
        self._trades_today: list = []  # 今日所有成交
        self._order_feedback_today: list = []  # 今日未成交/挂单中的指令反馈（供下一时刻校验）
        self._position_open_date: Dict[str, str] = {}  # code -> 开仓日期
        self._position_open_price: Dict[str, float] = {}  # code -> 开仓均价
        # 盘中监控状态
        self._position_peak_pnl: Dict[str, float] = {}  # code -> 持仓期间峰值盈亏比（移动止盈用）
        self._monitor_alerted: set = set()  # 当日已预警过的 code（避免刷屏）

    def start(self):
        """启动系统"""
        logger.info("=" * 60)
        logger.info("ETF T+1 自动交易系统启动")
        logger.info(f"LLM 提供商: {self.ai.provider} | 模型: {self.ai.model} | 联网搜索: {'ON' if self.ai.supports_search else 'OFF'}")
        logger.info(f"ETF池数量: {len(Config.ETF_POOL)}")
        logger.info("=" * 60)

        # 连接QMT
        if not self.qmt.connect():
            logger.error("QMT连接失败，请确保 miniQMT 已启动并登录")
            return

        # 设置定时任务
        self._setup_schedules()

        enabled_times = ' | '.join(
            f"{Config.TRADING_MOMENTS[m]['time']}({Config.TRADING_MOMENTS[m]['name']})"
            for m in MOMENT_ORDER
        )
        logger.info("系统就绪，等待交易时刻...")
        logger.info(f"研报: WorkBuddy定时器 09:00/12:45 (缺失自动兜底) | 交易: {enabled_times} | 日报: 15:00")

        # 主循环
        while True:
            schedule.run_pending()
            time.sleep(30)

    def _setup_schedules(self):
        """设置定时任务"""
        # 研报搜索已由 WorkBuddy 定时器自动执行（09:00 / 12:45）
        # WorkBuddy 会联网搜索9大平台行业研报，结果保存到 logs/research/research_{label}_{date}.md
        # main.py 只负责交易决策和日报生成

        # 交易时刻：仅调度 enabled=True 的时刻（降低交易频率，避免盘中频繁轮仓）
        for day in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
            for m in MOMENT_ORDER:
                getattr(schedule.every(), day).at(MOMENT_SCHEDULE[m]).do(getattr(self, f'run_{m}'))

        # 开盘前研报预爬：08:30 预爬盘前研报，12:30 预爬午后研报。
        # 保证即使 WorkBuddy 定时器失败，M1/M3 决策时也有 iris 研报可用（不覆盖 WorkBuddy 已生成内容）。
        for day in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
            getattr(schedule.every(), day).at("08:30").do(self.run_research_precrawl, label='morning')
            getattr(schedule.every(), day).at("12:30").do(self.run_research_precrawl, label='afternoon')

        # 收盘后汇总：15:00 生成 Excel 日报
        for day in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
            getattr(schedule.every(), day).at("15:00").do(self.run_daily_report)

        # 盘中实时监控：交易时段内每 MONITOR_INTERVAL_MIN 分钟检查持仓止损/止盈回撤并预警
        schedule.every(Config.MONITOR_INTERVAL_MIN).minutes.do(self.run_monitor)

    def run_research_precrawl(self, label: str = 'morning'):
        """开盘前研报预爬定时任务（08:30 盘前 / 12:30 午后）"""
        try:
            ok = precrawl_research(label)
            logger.info(f"[研报预爬] {label} {'已完成(已有可用研报)' if ok else '未能获取，M1决策将降级运行'}")
        except Exception as e:
            logger.error(f"[研报预爬] {label} 异常: {e}", exc_info=True)

    # ================================================================
    # 研报搜索已迁移到 WorkBuddy 定时器，并由 main.py 自愈兜底
    # - 09:00 盘前研报: WorkBuddy 定时器自动搜索 → logs/research/research_morning_*.md
    # - 12:45 午后研报: WorkBuddy 定时器自动搜索 → logs/research/research_afternoon_*.md
    # - 兜底: 若某 label 文件缺失/为空/过旧，ensure_research() 会现场用
    #         iris 爬虫 + DeepSeek 联网搜索 生成同路径文件，保证不裸奔
    # 读取入口: search.research_provider.ensure_research(label, self.ai)
    # ================================================================

    def run_daily_report(self):
        """15:00 收盘后生成 Excel 日报"""
        logger.info("\n" + "=" * 60)
        logger.info("【定时任务】生成今日 Excel 日报")
        logger.info("=" * 60)
        try:
            path = generate_daily_report()
            if path:
                logger.info(f"日报已保存: {path}")
            else:
                logger.warning("无今日决策数据，跳过日报")
        except Exception as e:
            logger.error(f"日报生成失败: {e}")

    def run_monitor(self):
        """盘中实时监控：检查持仓是否破止损线 / 移动止盈回撤，触发预警。

        预警方式：日志 WARNING + 写 logs/alerts/ + Windows 弹窗（经 executor._alert）。
        注意：A股 T+1，当日买入无法当日卖出，故预警仅提示「次日开盘处理」，不下单。
        """
        try:
            # 仅交易时段内检查；非交易时段直接返回（周期触发，靠此守卫避免无效轮询）
            if not self.qmt.is_trading_hours():
                return
            positions = self.qmt.get_positions()
            if not positions:
                return

            for p in positions:
                code = p['stock_code']
                name = Config.ETF_POOL.get(code, code)
                pnl_ratio = float(p.get('pnl_ratio', 0.0))

                # 跟踪持仓期间峰值盈亏比（用于移动止盈回撤判断）
                peak = self._position_peak_pnl.get(code, pnl_ratio)
                if pnl_ratio > peak:
                    peak = pnl_ratio
                self._position_peak_pnl[code] = peak

                # 1) 硬止损线
                if pnl_ratio <= Config.STOP_LOSS_PCT:
                    self._monitor_alert(
                        code, name, pnl_ratio, '止损线',
                        f"已破止损线 {Config.STOP_LOSS_PCT * 100:.0f}%，"
                        f"T+1 今日无法卖出，建议次日开盘处理"
                    )
                    continue

                # 2) 移动止盈回撤（仅当曾盈利时）：从峰值回撤达 TRAILING_STOP_PCT 触发
                drawdown = pnl_ratio - peak  # 负数=回撤
                if peak > 0 and drawdown <= Config.TRAILING_STOP_PCT:
                    self._monitor_alert(
                        code, name, pnl_ratio, '移动止盈回撤',
                        f"从高点 {peak * 100:.1f}% 回撤至 {pnl_ratio * 100:.1f}%，"
                        f"触及移动止盈回撤线 {Config.TRAILING_STOP_PCT * 100:.0f}%，"
                        f"T+1 今日无法卖出，建议次日开盘处理"
                    )
        except Exception as e:
            logger.error(f"盘中监控异常: {e}", exc_info=True)

    def _monitor_alert(self, code: str, name: str, pnl_ratio: float,
                       breach_type: str, detail: str):
        """盘中预警：当日每 code 仅提示一次，避免刷屏。"""
        if code in self._monitor_alerted:
            return
        self._monitor_alerted.add(code)
        msg = f"{name}({code}) 当前盈亏 {pnl_ratio * 100:.2f}% — {detail}"
        logger.warning(f"⚠️ 盘中监控触发 [{breach_type}]: {msg}")
        # 复用 executor 的告警通道（日志+文件+弹窗）
        self.executor._alert(f"盘中预警·{breach_type}", msg, code)

    def run_moment_1(self):
        """9:25 集合竞价决策"""
        self._run_moment('moment_1')

    def run_moment_2(self):
        """10:40 上午确认决策"""
        self._run_moment('moment_2')

    def run_moment_3(self):
        """12:50 午盘转折决策"""
        self._run_moment('moment_3')

    def run_moment_4(self):
        """14:40 尾盘最终决策"""
        self._run_moment('moment_4')

    def _run_moment(self, moment: str, test: bool = False):
        """执行某个时刻的决策流程

        test=True 表示来自 --test 手动测试模式：产物写入 logs/decisions_test/
        并打 test 标记，避免盘前预演数据污染真实决策目录（进而影响日报与公众号自动化）。
        """
        moment_config = Config.TRADING_MOMENTS.get(moment, {})
        moment_name = moment_config.get('name', moment)

        logger.info(f"\n{'='*60}")
        logger.info(f"【{moment_name}】开始执行")
        logger.info(f"{'='*60}")

        try:
            # 1. QMT市场数据收集（主数据源）
            logger.info("步骤1: 从QMT收集市场数据...")
            market_report = self.market_data.collect(moment)
            logger.info(f"  市场数据报告已生成 ({len(market_report)}字符)")

            # 2. 加载研报搜索结果
            # 优先用 WorkBuddy 定时器生成的文件；若缺失/为空/过旧，自动兜底生成
            # （iris 爬虫 + DeepSeek 联网搜索），保证不裸奔
            # 同时自动追加「研报→ETF 映射」小节，帮助模型把行业线索对应到可交易 ETF
            label = "morning" if moment in ('moment_1', 'moment_2') else "afternoon"
            research_text, mapping_md = ensure_research_with_mapping(label, self.ai)
            if research_text:
                logger.info(f"步骤2: 已加载{label}研报搜索结果 ({len(research_text)}字符)"
                            f"{' [含ETF映射]' if mapping_md else ''}")
            else:
                logger.warning(f"步骤2: {label}研报缺失，AI 将在无研报上下文下决策")

            # 3. 获取持仓和ETF数据
            logger.info("步骤3: 获取持仓和行情数据...")
            context = self._build_context(moment, test=test)

            # 3. 构建Prompt（用 replace 替代 format，避免JSON花括号冲突）
            logger.info("步骤3: 构建Prompt...")
            prompt_template = TradingPrompts.get_prompt(moment)
            user_prompt = prompt_template
            # 将所有上下文变量逐一替换
            # 合并 QMT行情 + 研报搜索
            if research_text:
                context['search_results'] = market_report + f"\n\n{'='*60}\n【行业研报聚合】\n{'='*60}\n{research_text}"
            else:
                context['search_results'] = market_report
            for key, value in context.items():
                user_prompt = user_prompt.replace(f'{{{key}}}', str(value))

            # 4. 调用DeepSeek
            logger.info("步骤5: 调用DeepSeek V4...")
            ai_decision = self.ai.chat(
                system_prompt=TradingPrompts.SYSTEM_PROMPT,
                user_prompt=user_prompt
            )

            if not ai_decision:
                logger.error("DeepSeek返回为空")
                return

            # 保存决策
            self.last_decisions[moment] = ai_decision

            # 6. 归一化决策结构（不同时刻的 JSON 结构不同，统一为 buy_orders + sell_orders）
            normalized = self._normalize_decision(moment, ai_decision)

            # 7. 输出决策摘要
            self._print_decision_summary(moment, normalized)

            # 5. 执行交易
            logger.info("步骤5: 执行交易...")
            result = self.executor.execute(normalized, moment=moment)
            logger.info(f"交易执行结果: {result}")

            # 记录今日成交 + 更新持仓追踪
            self._record_trades(moment, normalized, result)

            # 6. 保存日志
            self._save_decision_log(moment, ai_decision, result, test=test)

        except Exception as e:
            logger.error(f"{moment_name}执行失败: {e}", exc_info=True)

    def _record_trades(self, moment: str, decision: Dict, result: Dict):
        """记录今日成交 + 更新持仓追踪"""
        today = datetime.now().strftime('%Y-%m-%d')
        buys = result.get('buy_orders', [])
        sells = result.get('sell_orders', [])

        for b in buys:
            if b.get('executed'):
                code = b.get('etf_code', '')
                self._trades_today.append({
                    'time': datetime.now().strftime('%H:%M:%S'),
                    'moment': moment,
                    'type': '买入',
                    'etf_code': code,
                    'price': b.get('price', 0),
                    'volume': b.get('volume', 0),
                    'reason': b.get('reason', ''),
                    'pnl': 0,
                })
                # 记录开仓
                if code not in self._position_open_date:
                    self._position_open_date[code] = today
                if code not in self._position_open_price:
                    self._position_open_price[code] = b.get('price', 0)
                else:
                    # 加仓：更新均价
                    old_p = self._position_open_price[code]
                    new_p = b.get('price', 0)
                    self._position_open_price[code] = (old_p + new_p) / 2
            else:
                status = b.get('status', '')
                kind = 'pending' if status == '挂单中' else 'failed'
                self._order_feedback_today.append({
                    'time': datetime.now().strftime('%H:%M:%S'),
                    'moment': moment,
                    'type': '买入' if kind == 'failed' else '买入(挂单)',
                    'etf_code': b.get('etf_code', ''),
                    'volume': b.get('volume', 0),
                    'status': status,
                    'kind': kind,
                })

        for s in sells:
            if s.get('executed'):
                code = s.get('etf_code', '')
                pnl = s.get('pnl', 0)
                self._trades_today.append({
                    'time': datetime.now().strftime('%H:%M:%S'),
                    'moment': moment,
                    'type': '卖出',
                    'etf_code': code,
                    'price': s.get('price', 0),
                    'volume': s.get('volume', 0),
                    'reason': s.get('reason', ''),
                    'pnl': pnl,
                })
            else:
                status = s.get('status', '')
                kind = 'pending' if status == '挂单中' else 'failed'
                self._order_feedback_today.append({
                    'time': datetime.now().strftime('%H:%M:%S'),
                    'moment': moment,
                    'type': '卖出' if kind == 'failed' else '卖出(挂单)',
                    'etf_code': s.get('etf_code', ''),
                    'volume': s.get('volume', 0),
                    'status': status,
                    'kind': kind,
                })

    def _build_context(self, moment: str, test: bool = False) -> Dict:
        """构建上下文数据"""
        context = {}

        # 前序时刻累计执行回顾（从磁盘读取今天所有更早时刻的『决策+执行结果』，
        # 让 AI 知道自己之前下了什么单、实际成交/挂单/未成交了什么。跨进程/重启不断档）
        context['execution_history'] = self._build_execution_history(moment, test=test)

        # 账号信息
        account = self.qmt.get_account_info()
        if account:
            cash = account.get('available_cash', 0)
            total = account.get('total_asset', 0)
            mkt_val = account.get('market_value', 0)
            pct = mkt_val / total * 100 if total > 0 else 0
            context['account_info'] = f"总资产:{total:.0f}元 可用:{cash:.0f}元 持仓市值:{mkt_val:.0f}元 仓位:{pct:.0f}%"
        else:
            context['account_info'] = '账号信息不可用'

        # 持仓（含持仓天数）
        today = datetime.now().strftime('%Y-%m-%d')
        positions = self.qmt.get_positions()
        if positions:
            pos_lines = []
            for p in positions:
                code = p['stock_code']
                open_date = self._position_open_date.get(code, '未知')
                hold_days = ''
                if open_date != '未知':
                    days = (datetime.strptime(today, '%Y-%m-%d') - datetime.strptime(open_date, '%Y-%m-%d')).days
                    hold_days = f'持仓{days}天 '
                pos_lines.append(
                    f"{p['stock_code']}: {p['volume']}股, "
                    f"{hold_days}"
                    f"成本{p['cost_price']:.3f}, "
                    f"现价{p['market_price']:.3f}, "
                    f"盈亏{p['pnl_ratio']:.2%}, "
                    f"可卖{p['available']}股"
                )
            context['current_positions'] = '\n'.join(pos_lines)
        else:
            context['current_positions'] = '空仓'
            self._position_open_date.clear()
            self._position_open_price.clear()

        # ETF数据
        etf_data = self.qmt.get_all_etf_data()
        if etf_data:
            lines = []
            for code, data in etf_data.items():
                name = Config.ETF_POOL.get(code, code)
                lines.append(
                    f"{name}({code}): "
                    f"开盘{data['open']:.3f} "
                    f"现价{data['last_price']:.3f} "
                    f"涨跌{data['change_pct']:.2f}% "
                    f"最高{data['high']:.3f} "
                    f"最低{data['low']:.3f} "
                    f"量{data['volume']:.0f}"
                )
            context['etf_open_prices'] = '\n'.join(lines)
            context['etf_since_open'] = '\n'.join(lines)
            context['etf_morning_performance'] = '\n'.join(lines)
            context['etf_daily_performance'] = '\n'.join(lines)
        else:
            context['etf_open_prices'] = '无数据'
            context['etf_since_open'] = '无数据'
            context['etf_morning_performance'] = '无数据'
            context['etf_daily_performance'] = '无数据'

        # 昨日收盘
        yesterday_lines = []
        for code in Config.ETF_POOL:
            close = self.qmt.get_yesterday_close(code)
            if close:
                name = Config.ETF_POOL.get(code, code)
                yesterday_lines.append(f"{name}({code}): {close:.3f}")
        context['yesterday_close'] = '\n'.join(yesterday_lines) if yesterday_lines else '无数据'

        # 前一时刻决策
        earlier = self._get_earlier_decision(moment)
        context['earlier_decision'] = earlier

        # 上午总结和今日总结
        context['morning_summary'] = self._get_morning_summary()
        context['today_summary'] = self._get_today_summary()
        # 今日成交 + 盈亏
        if self._trades_today or self._order_feedback_today:
            trade_lines = []
            total_pnl = 0
            for t in self._trades_today:
                pnl_str = f" 盈亏:{t['pnl']:.2f}元" if t['pnl'] != 0 else ""
                trade_lines.append(f"{t['time']} {t['type']} {t['etf_code']} {t['volume']}股 @{t['price']:.3f}{pnl_str}")
                total_pnl += t['pnl']
            for f in self._order_feedback_today:
                mark = '❌未成交' if f['kind'] == 'failed' else '🔵挂单中'
                trade_lines.append(f"{f['time']} {mark} {f['type']} {f['etf_code']} {f['volume']}股 状态:{f['status']}")
            context['today_trades'] = '\n'.join(trade_lines)
            context['today_pnl'] = f"已实现盈亏: {total_pnl:.2f}元 ({total_pnl / Config.SIM_INITIAL_CAPITAL * 100:.2f}%)"
        else:
            context['today_trades'] = '今日暂无成交'
            context['today_pnl'] = '今日暂无盈亏'

        return context

    def _normalize_decision(self, moment: str, decision: Dict) -> Dict:
        """
        归一化不同时刻的决策结构，统一为 executor 能处理的格式：
        - sell_orders: [{etf_code, action, sell_price, sell_ratio, ...}]
        - buy_orders:  [{etf_code, action, buy_price, position_ratio, ...}]
        
        moment_1/2 已经是标准格式，无需转换。
        moment_3/4 的结构不同，需要映射。
        """
        if not decision:
            return decision

        # 如果已经有 buy_orders 和 sell_orders，直接用
        if 'buy_orders' in decision or 'sell_orders' in decision:
            return decision

        # --- moment_3: 午盘转折 ---
        # holdings_decision -> sell_orders
        # new_positions_afternoon.opportunities -> buy_orders
        if moment == 'moment_3':
            sell_orders = []
            for h in decision.get('holdings_decision', []):
                sell_orders.append({
                    'etf_code': h.get('etf_code', ''),
                    'action': h.get('action', ''),
                    'order_type': h.get('order_type', 'market'),
                    'sell_ratio': h.get('sell_ratio', 1),
                    'sell_price': h.get('sell_price', 0),
                    'reason': h.get('reason', ''),
                })

            buy_orders = []
            new_pos = decision.get('new_positions_afternoon', {})
            if new_pos.get('allow_new') and new_pos.get('opportunities'):
                for opp in new_pos['opportunities']:
                    buy_orders.append({
                        'etf_code': opp.get('etf_code', ''),
                        'action': opp.get('buy_timing', '现在买入'),
                        'order_type': opp.get('order_type', 'market'),
                        'buy_price': opp.get('buy_price', 0),
                        'position_ratio': opp.get('position_ratio', 0),
                        'stop_loss': opp.get('stop_loss', 0),
                        'reason': opp.get('reason', ''),
                    })

            decision['sell_orders'] = sell_orders
            decision['buy_orders'] = buy_orders
            return decision

        # --- moment_4: 尾盘最终 ---
        # sellable_positions -> sell_orders
        # tail_new_position -> buy_orders (如果 has_opportunity)
        if moment == 'moment_4':
            sell_orders = []
            for s in decision.get('sellable_positions', []):
                sell_orders.append({
                    'etf_code': s.get('etf_code', ''),
                    'action': s.get('action', ''),
                    'order_type': s.get('order_type', 'market'),
                    'sell_ratio': s.get('sell_ratio', 1),
                    'sell_price': s.get('sell_price', 0),
                    'reason': s.get('reason', ''),
                })

            buy_orders = []
            tail = decision.get('tail_new_position', {})
            if tail.get('has_opportunity'):
                buy_orders.append({
                    'etf_code': tail.get('etf_code', ''),
                    'action': '尾盘买入',
                    'order_type': tail.get('order_type', 'market'),
                    'buy_price': tail.get('entry_price', 0),
                    'position_ratio': tail.get('position_ratio', 0),
                    'stop_loss': tail.get('stop_loss', 0),
                    'reason': tail.get('reason', ''),
                })

            decision['sell_orders'] = sell_orders
            decision['buy_orders'] = buy_orders
            return decision

        return decision

    def _get_earlier_decision(self, moment: str) -> str:
        """获取前一（已启用）时刻的决策"""
        try:
            idx = MOMENT_ORDER.index(moment)
        except ValueError:
            return '无（未知时刻）'
        if idx == 0:
            return '无（今日首个决策时刻，无前置决策记录）'
        prev = MOMENT_ORDER[idx - 1]
        if prev in self.last_decisions:
            return json.dumps(self.last_decisions[prev], ensure_ascii=False, indent=2)
        return '无'

    # ================================================================
    # 前序时刻累计执行回顾（多轮 prompt 传递的核心）
    # 每个后续时刻的 prompt 都会注入今天所有更早时刻的真实执行结果，
    # AI 因此"知道"自己 9:25 / 10:30 / 12:50 到底买了什么、成交了没有。
    # 数据来自磁盘 logs/decisions（真实）/ logs/decisions_test（--test），
    # 即使进程重启或手动分段运行也不丢上下文。
    # ================================================================

    def _build_execution_history(self, moment: str, test: bool = False) -> str:
        """读取今天所有更早时刻的『决策 + 执行结果』，拼成累计的『前序执行回顾』文本块。"""
        try:
            idx = MOMENT_ORDER.index(moment)
        except ValueError:
            return '无（未知时刻）'
        if idx == 0:
            return '无（今日首个决策时刻，无前置执行记录）'

        earlier = MOMENT_ORDER[:idx]
        log_dir = Path('logs/decisions_test' if test else 'logs/decisions')
        if not log_dir.exists():
            return '无（今日暂无前置决策记录）'

        today_str = datetime.now().strftime('%Y-%m-%d')
        records = []
        for f in sorted(log_dir.glob('*.json')):
            try:
                data = json.loads(f.read_text(encoding='utf-8'))
            except Exception:
                continue
            # 仅取「今天」且「更早时刻」的记录（避免混入历史日期的日志）
            ts = data.get('timestamp', '')
            if data.get('moment') in earlier and ts.startswith(today_str):
                records.append(data)

        if not records:
            return '无（今日暂无前置决策记录）'

        records.sort(key=lambda d: d.get('timestamp', ''))
        header = (f"以下为今日此前 {len(records)} 个时刻的真实执行结果"
                  f"（成交✅ / 挂单🔵 / 未成交❌ / 跳过⏭️），请据此修正当前决策：")
        blocks = [self._format_execution_record(r) for r in records]
        return header + '\n\n' + '\n\n'.join(blocks)

    @staticmethod
    def _format_execution_record(rec: Dict) -> str:
        """把单条决策+执行结果格式化成可读文本块。"""
        moment = rec.get('moment', '')
        label = Config.TRADING_MOMENTS.get(moment, {}).get('name', moment)
        ts = rec.get('timestamp', '')
        decision = rec.get('decision', {}) or {}
        result = rec.get('execution_result', {}) or {}

        lines = [f"【{label} | {ts}】"]

        intent = ETFTradingSystem._extract_intent(decision)
        if intent:
            lines.append(f"AI意图: {intent}")

        lines.append("实际执行:")
        parts = []
        for b in result.get('buy_orders', []):
            parts.append(ETFTradingSystem._fmt_exec_order('买入', b, filled=True))
        for s in result.get('sell_orders', []):
            parts.append(ETFTradingSystem._fmt_exec_order('卖出', s, filled=True))
        for p in result.get('pending_orders', []):
            parts.append(ETFTradingSystem._fmt_exec_order('挂单', p, pending=True))
        for sk in result.get('skipped', []):
            code = sk.get('etf_code', '')
            reason = sk.get('reason', '')
            parts.append(f"    ⏭️ 跳过 {code} — {reason}")
        for er in result.get('errors', []):
            parts.append(f"    ⚠️ 异常 {er}")
        if not parts:
            parts.append("    （无实际委托动作）")
        lines.append('\n'.join(parts))
        return '\n'.join(lines)

    @staticmethod
    def _fmt_exec_order(prefix: str, o: Dict, filled: bool = False, pending: bool = False) -> str:
        """格式化单笔委托的执行状态。"""
        code = o.get('etf_code', '')
        vol = o.get('volume', 0)
        price = o.get('price', 0)
        otype = o.get('order_type', '')
        status = o.get('status', '')
        if pending:
            mark = '🔵'
        elif filled:
            mark = '✅'
        else:
            mark = '❌'
        try:
            price_s = f"{float(price):.3f}"
        except (TypeError, ValueError):
            price_s = str(price)
        return f"    {mark} {prefix} {code} {vol}股 @{price_s} ({otype}) 状态:{status}"

    @staticmethod
    def _extract_intent(decision: Dict) -> str:
        """尽量从 AI 决策里提取一句『买入/卖出意图』简述（兼容各时刻不同结构）。"""
        if not decision:
            return ''
        buys = decision.get('buy_orders') or []
        sells = decision.get('sell_orders') or []
        # 兼容 moment_3 / moment_4 的原始结构
        if not buys and not sells:
            npa = decision.get('new_positions_afternoon', {}) or {}
            if npa.get('allow_new') and npa.get('opportunities'):
                buys = npa['opportunities']
            tnp = decision.get('tail_new_position', {}) or {}
            if tnp.get('has_opportunity'):
                buys = buys + [tnp]
            sells = decision.get('holdings_decision', []) or decision.get('sellable_positions', [])

        parts = []
        for b in buys[:5]:
            code = b.get('etf_code', '')
            if not code:
                continue
            ratio = b.get('position_ratio') or b.get('buy_timing', '')
            parts.append(f"买入{code}({ratio})" if ratio else f"买入{code}")
        for s in sells[:5]:
            code = s.get('etf_code', '')
            if code:
                parts.append(f"卖出{code}")
        if not parts:
            # 空仓/持有类意图
            if decision.get('empty_position_decision', {}).get('should_empty'):
                return '建议空仓'
            if decision.get('overnight_position_plan'):
                return '维持/调整过夜仓位'
        return '；'.join(parts)

    def _get_morning_summary(self) -> str:
        """上午数据总结（moment_1 + moment_2 的真实数据）"""
        trades = [t for t in self._trades_today if t['moment'] in ('moment_1', 'moment_2')]
        if not trades:
            return "上午暂无成交"
        lines = []
        for t in trades:
            pnl_str = f" 盈亏:{t['pnl']:.2f}" if t['pnl'] != 0 else ""
            lines.append(f"{t['time']} {t['type']} {t['etf_code']} {t['volume']}股 @{t['price']:.3f}{pnl_str}")
        return '\n'.join(lines)

    def _get_today_summary(self) -> str:
        """全天数据总结"""
        if not self._trades_today:
            return "今日暂无成交"

        buys = [t for t in self._trades_today if t['type'] == '买入']
        sells = [t for t in self._trades_today if t['type'] == '卖出']
        total_buy_pnl = sum(t['pnl'] for t in sells)
        realized_pl = sum(t['price'] * t['volume'] * 0.001 for t in sells)

        lines = [f"今日共{len(self._trades_today)}笔 (买入{len(buys)}/卖出{len(sells)}) 已实现盈亏:{total_buy_pnl:.2f}"]
        for t in self._trades_today:
            pnl_str = f" 盈亏:{t['pnl']:.2f}" if t['pnl'] != 0 else ""
            lines.append(f"  {t['time']} {t['type']} {t['etf_code']} {t['volume']}股 @{t['price']:.3f}{pnl_str}")
        return '\n'.join(lines)

    def _print_decision_summary(self, moment: str, decision: Dict):
        """打印决策摘要"""
        moment_name = Config.TRADING_MOMENTS.get(moment, {}).get('name', moment)
        logger.info(f"\n{'─'*60}")
        logger.info(f"【{moment_name} - AI决策摘要】")

        # 市场判断
        overview = decision.get('market_overview', {}) or decision.get('morning_verdict', {}) or decision.get('today_final_verdict', {})
        if overview:
            logger.info(f"市场判断: {overview.get('today_judgment', overview.get('market_type', 'N/A'))}")

        # 卖出指令
        sells = decision.get('sell_orders', [])
        if sells:
            logger.info(f"\n卖出指令({len(sells)}笔):")
            for s in sells:
                otype = s.get('order_type', 'market')
                logger.info(f"  {s.get('etf_code')} | {s.get('action')} | {otype} | 比例:{s.get('sell_ratio')} | {s.get('reason', '')[:50]}")

        # 买入指令
        buys = decision.get('buy_orders', [])
        if buys:
            logger.info(f"\n买入指令({len(buys)}笔):")
            for b in buys:
                otype = b.get('order_type', 'market')
                logger.info(f"  {b.get('etf_code')} | {b.get('action')} | {otype} | 仓位:{b.get('position_ratio')} | 止损:{b.get('stop_loss')} | {b.get('reason', '')[:50]}")

        # 空仓
        empty = decision.get('empty_position_decision', {})
        if empty and empty.get('should_empty'):
            logger.info(f"\n建议空仓: {empty.get('reason', '')[:100]}")

        # 过夜仓位
        overnight = decision.get('overnight_position_plan', {})
        if overnight:
            logger.info(f"\n过夜仓位建议: {overnight.get('target_ratio', 'N/A')}")

        # 关键提醒
        key = decision.get('key_reminder', '')
        if key:
            logger.info(f"\n关键提醒: {key}")

        logger.info(f"{'─'*60}")

    def _save_decision_log(self, moment: str, decision: Dict, result: Dict, test: bool = False):
        """保存决策日志

        test=True（--test 手动测试模式）写入 logs/decisions_test/ 并打 test 标记，
        避免盘前预演数据污染真实决策目录，进而影响日报与公众号自动化。
        """
        try:
            if test:
                log_dir = Path("logs/decisions_test")
            else:
                log_dir = Path("logs/decisions")
            log_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = log_dir / f"{moment}_{timestamp}.json"

            log_data = {
                'timestamp': datetime.now().isoformat(),
                'moment': moment,
                'moment_name': Config.TRADING_MOMENTS.get(moment, {}).get('name', ''),
                'test': test,
                'decision': decision,
                'execution_result': result
            }

            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(log_data, f, ensure_ascii=False, indent=2)

            logger.info(f"决策日志已保存({'测试' if test else '真实'}): {filename}")
        except Exception as e:
            logger.error(f"保存日志失败: {e}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='ETF T+1 自动交易系统')
    parser.add_argument('--test', type=str, nargs='?', const='moment_1',
                        choices=['moment_1', 'moment_2', 'moment_3', 'moment_4', 'all'],
                        help='手动触发测试: moment_1~4 或 all (默认 moment_1)')
    args = parser.parse_args()

    system = ETFTradingSystem()

    if args.test:
        # 手动测试模式：连接QMT后立即执行一次决策流程
        logger.info("=" * 60)
        logger.info("ETF T+1 自动交易系统 - 手动测试模式")
        logger.info(f"LLM 提供商: {system.ai.provider} | 模型: {system.ai.model} | 联网搜索: {'ON' if system.ai.supports_search else 'OFF'}")
        logger.info(f"ETF池数量: {len(Config.ETF_POOL)}")
        logger.info("=" * 60)

        if not system.qmt.connect():
            logger.error("QMT连接失败，请确保 miniQMT 已启动并登录")
            exit(1)

        logger.info("QMT连接成功，开始手动测试...\n")

        if args.test == 'all':
            # 依次跑所有已启用的时刻（手动测试：产物写入 logs/decisions_test/ 并打 test 标记）
            for m in MOMENT_ORDER:
                system._run_moment(m, test=True)
                logger.info("\n" + "=" * 60 + "\n")
        else:
            # 只跑指定时刻（手动测试）
            system._run_moment(args.test, test=True)

        logger.info("\n手动测试完成!")
    else:
        # 正常定时模式
        system.start()