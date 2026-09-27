"""
交易执行器
解析AI返回的JSON并执行交易

支持两种下单方式：
- market（市价单）：立即按最新价成交
- limit（限价单）：挂单等待价格触发后成交

支持自动拆单：当单笔委托金额超过 MAX_SINGLE_ORDER_AMOUNT（默认100万）时，
自动拆成多笔子委托连续下发，避免柜台 120156 拒绝。

AI 通过 order_type 字段指定下单方式：
- "market" -> 立即市价买入/卖出
- "limit"  -> 限价挂单，buy_price/sell_price 为挂单价格
"""
import re
import time
import json
import datetime
import subprocess
from pathlib import Path
from typing import Dict, List, Optional
from loguru import logger
from config.settings import Config
from trading.qmt_client import QMTClient


class TradeExecutor:
    """交易执行器"""

    # 拆单间隔（秒），避免连续下单冲击柜台
    SPLIT_ORDER_INTERVAL = 0.5

    def __init__(self, qmt_client: QMTClient):
        self.qmt = qmt_client
        # 当日有效交易笔数（成交+在途），仅作展示用；硬上限由 _count_active_orders_today()
        # 以 QMT 当日委托实时核算（废单/撤单不计入），天然跨时刻/跨进程一致。
        self.daily_trade_count = self._load_daily_count()

    # ================================================================
    # 当日交易笔数持久化（跨进程共享，落实每日封顶）
    # ================================================================

    def _daily_count_path(self) -> Path:
        """当日交易笔数落盘路径（按日期分文件，跨日自动清零）"""
        return Path('logs') / f"daily_trades_{datetime.date.today():%Y%m%d}.json"

    def _load_daily_count(self) -> int:
        """启动时读取当日已提交笔数；文件不存在/损坏则返回 0"""
        try:
            p = self._daily_count_path()
            if p.exists():
                data = json.loads(p.read_text(encoding='utf-8'))
                return int(data.get('count', 0))
        except Exception as e:
            logger.warning(f"读取当日交易笔数失败: {e}")
        return 0

    def _save_daily_count(self):
        """每提交一笔委托后回写当日计数（仅作展示/日报用，硬上限改由 QMT 当日委托实时核算）"""
        try:
            p = self._daily_count_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(
                {'date': str(datetime.date.today()), 'count': self.daily_trade_count},
                ensure_ascii=False), encoding='utf-8')
        except Exception as e:
            logger.warning(f"持久化当日交易笔数失败: {e}")

    def _count_active_orders_today(self) -> int:
        """统计今日「有效交易」笔数 = 已成交 + 在途未撤的委托。

        用于 MAX_DAILY_TRADES 硬上限。只数真实成交与在途委托，
        废单(柜台拒绝)与已撤单不计入 —— 避免挂单/废单尝试吃光每日额度
        （7/28 即因 2 笔挂单 + 1 笔废单吃掉 3 笔额度导致全天冻结）。
        以 QMT 当日委托为唯一真值源，天然跨时刻/跨进程一致。
        """
        try:
            orders = self.qmt.query_today_orders()
            return len([o for o in orders
                        if o.get('is_filled') or not o.get('is_terminal')])
        except Exception as e:
            logger.warning(f"统计今日有效交易笔数失败: {e}")
            return 0

    def execute(self, ai_decision: Dict, moment: str = None) -> Dict:
        """执行AI返回的交易决策

        :param moment: 当前时刻标识（'moment_1' 等）。当为 moment_1 或处于集合竞价
                       窗口时，所有订单强制转为限价单参与开盘竞价（市价单会被柜台拒绝）。
        """
        if not ai_decision:
            logger.warning("AI决策为空，跳过执行")
            return {'executed': False, 'reason': 'AI决策为空'}

        # M1 集合竞价：强制限价单（A股 09:15-09:25 柜台只收限价单）
        is_call_auction = (moment == 'moment_1') or self.qmt.is_call_auction_window()
        if is_call_auction:
            logger.info("⏱️ 当前为集合竞价时刻，所有订单将转为限价单参与开盘竞价")

        # ================================================================
        # 下单前：先撤掉所有未成交委托，避免重复下单和资金冻结
        # ================================================================
        cancelled = self.qmt.cancel_pending_orders()
        if cancelled > 0:
            logger.info(f"下单前已撤掉 {cancelled} 笔未成交委托")

        # ================================================================
        # 交易时段校验：非交易时段不下单
        # ================================================================
        if not self.qmt.is_trading_hours():
            logger.warning("当前非A股交易时段，跳过所有交易指令")
            return {
                'executed': False,
                'reason': '非交易时段，所有指令已跳过',
                'cancelled_pending': cancelled,
            }

        executed = {
            'sell_orders': [],
            'buy_orders': [],
            'pending_orders': [],   # 限价挂单（未即时成交）
            'skipped': [],          # 被跳过的指令及原因
            'errors': []
        }

        # 日交易笔数上限校验（MAX_DAILY_TRADES）
        # 口径：只数「真实成交 + 在途未撤」的有效委托，废单/已撤单不占额度。
        # 即一天最多 MAX_DAILY_TRADES 笔有效交易；挂单失败/柜台拒单不会吃光额度。
        # active 以 QMT 当日委托实时核算，跨时刻/跨进程一致；placed 防本时刻超发。
        active = self._count_active_orders_today()
        self.daily_trade_count = active  # 仅用于日志/日报展示
        if active >= Config.MAX_DAILY_TRADES:
            logger.warning(f"⚠️ 当日有效交易(成交+在途)已达 {Config.MAX_DAILY_TRADES} 笔，"
                           f"全部指令跳过（当日不再新增交易）")
            for o in ai_decision.get('sell_orders', []):
                executed['skipped'].append({
                    'etf_code': o.get('etf_code', ''),
                    'reason': f'当日有效交易笔数已达上限({Config.MAX_DAILY_TRADES})'
                })
            for o in ai_decision.get('buy_orders', []):
                executed['skipped'].append({
                    'etf_code': o.get('etf_code', ''),
                    'reason': f'当日有效交易笔数已达上限({Config.MAX_DAILY_TRADES})'
                })
            return executed

        placed = 0  # 本次 execute 内已提交的有效委托数（用于在途计数，防单时刻超发）

        # 1. 先执行卖出
        sell_orders = ai_decision.get('sell_orders', [])
        for order in sell_orders:
            if active + placed >= Config.MAX_DAILY_TRADES:
                executed['skipped'].append({
                    'etf_code': order.get('etf_code', ''),
                    'reason': f'当日有效交易笔数已达上限({Config.MAX_DAILY_TRADES})'
                })
                continue
            result = self._execute_sell(order, is_call_auction)
            if result:
                placed += 1
                if result.get('order_type') == 'limit':
                    executed['pending_orders'].append(result)
                else:
                    executed['sell_orders'].append(result)

        # 2. 再执行买入
        buy_orders = ai_decision.get('buy_orders', [])
        for order in buy_orders:
            if active + placed >= Config.MAX_DAILY_TRADES:
                executed['skipped'].append({
                    'etf_code': order.get('etf_code', ''),
                    'reason': f'当日有效交易笔数已达上限({Config.MAX_DAILY_TRADES})'
                })
                continue
            result = self._execute_buy(order, is_call_auction)
            if result:
                placed += 1
                if result.get('order_type') == 'limit':
                    executed['pending_orders'].append(result)
                else:
                    executed['buy_orders'].append(result)

        executed['executed'] = (
            len(executed['sell_orders']) + len(executed['buy_orders']) > 0
        )
        executed['has_pending'] = len(executed['pending_orders']) > 0
        # 回写当日有效交易笔数（仅展示/日报用；硬上限由 QMT 实时核算，见 _count_active_orders_today）
        self.daily_trade_count = self._count_active_orders_today()
        self._save_daily_count()
        logger.info(
            f"执行完成: 卖出{len(executed['sell_orders'])}笔, "
            f"买入{len(executed['buy_orders'])}笔, "
            f"挂单{len(executed['pending_orders'])}笔, "
            f"跳过{len(executed['skipped'])}笔, "
            f"当日有效交易{self.daily_trade_count}/{Config.MAX_DAILY_TRADES}"
        )

        return executed

    # ================================================================
    # 拆单辅助方法
    # ================================================================

    @staticmethod
    def _split_volume(total_volume: int, price: float,
                      max_amount: float = None) -> List[int]:
        """将大单拆成每笔金额不超过 max_amount 的子单列表

        :param total_volume: 总股数
        :param price: 参考单价
        :param max_amount: 单笔金额上限（默认取 Config.MAX_SINGLE_ORDER_AMOUNT）
        :return: 子单股数列表，每笔 >= 100 且金额 <= max_amount
        """
        if max_amount is None:
            max_amount = Config.MAX_SINGLE_ORDER_AMOUNT
        if price <= 0 or total_volume <= 0:
            return []
        max_vol = int(max_amount / price / 100) * 100
        if max_vol < 100:
            # 单价极高，100股就超限 → 只能按原量一笔提交（柜台可能拒绝）
            return [total_volume] if total_volume >= 100 else []
        sub_volumes = []
        remaining = total_volume
        while remaining > 0:
            chunk = min(remaining, max_vol)
            chunk = int(chunk / 100) * 100
            if chunk >= 100:
                sub_volumes.append(chunk)
            remaining -= chunk
        return sub_volumes

    def _place_and_confirm(self, etf_code: str, action: str,
                           price: float, volume: int,
                           order_type: str) -> Optional[Dict]:
        """下单一子单并确认成交（市价/限价通用）

        :return: {'order_id', 'filled', 'traded_volume', 'status'} 或 None（提交失败）
        """
        if action == 'buy':
            if order_type == 'limit':
                order_id = self.qmt.buy_etf_limit(etf_code, price, volume)
            else:
                order_id = self.qmt.buy_etf(etf_code, price, volume)
        else:  # sell
            if order_type == 'limit':
                order_id = self.qmt.sell_etf_limit(etf_code, price, volume)
            else:
                order_id = self.qmt.sell_etf(etf_code, price, volume)

        if not order_id:
            return None

        confirm = self.qmt.confirm_order(int(order_id))
        return {
            'order_id': order_id,
            'filled': confirm['filled'],
            'traded_volume': confirm['traded_volume'],
            'status': confirm['status'],
        }

    def _execute_split_order(self, etf_code: str, action: str,
                             price: float, total_volume: int,
                             order_type: str = 'market') -> Optional[Dict]:
        """执行可能需要拆单的即时订单（市价/已触发限价）

        自动将大单拆成多笔 <= MAX_SINGLE_ORDER_AMOUNT 的子单连续下发，
        每笔独立确认成交，最终汇总返回。
        """
        sub_volumes = self._split_volume(total_volume, price)
        if not sub_volumes:
            logger.warning(f"  -> 拆单失败: {etf_code} vol={total_volume} price={price}")
            return None

        if len(sub_volumes) > 1:
            logger.info(f"  -> 拆单: {etf_code} {total_volume}股 -> {len(sub_volumes)}笔 "
                        f"({', '.join(str(v) for v in sub_volumes)})")

        total_traded = 0
        order_ids = []
        sub_results = []
        all_filled = True

        for i, sub_vol in enumerate(sub_volumes):
            result = self._place_and_confirm(etf_code, action, price, sub_vol, order_type)
            if result:
                order_ids.append(result['order_id'])
                total_traded += result['traded_volume']
                sub_results.append(result)
                if not result['filled']:
                    all_filled = False
                logger.info(f"  -> 子单{i+1}/{len(sub_volumes)}: {sub_vol}股 "
                            f"成交{result['traded_volume']}股 状态={result['status']}")
                # 废单 = 柜台硬拒绝，继续刷只会触发柜台报警且注定失败，立即停止本订单后续子单
                if '废单' in result['status']:
                    logger.error(
                        f"  ⛔ 检测到废单(柜台未记录): {etf_code} 序号{result['order_id']}。"
                        f"停止该订单后续子单提交（硬拒单重试无效，请检查 QMT 账号登录 / 柜台连接状态）"
                    )
                    self._alert(
                        "废单(柜台未记录)",
                        f"序号{result['order_id']} 被柜台拒绝，"
                        f"请检查 QMT 交易账号登录 / 柜台连接状态",
                        etf_code
                    )
                    break
            else:
                all_filled = False
                logger.error(f"  -> 子单{i+1}/{len(sub_volumes)}: 提交失败 {etf_code} {sub_vol}股")
                sub_results.append({'order_id': None, 'filled': False,
                                    'traded_volume': 0, 'status': '提交失败'})
                # 本地提交都失败，继续无意义，停止
                break

            if i < len(sub_volumes) - 1:
                time.sleep(self.SPLIT_ORDER_INTERVAL)

        status = ('全部成交' if all_filled and total_traded == total_volume
                  else '部分成交' if total_traded > 0 else '未成交')

        return {
            'etf_code': etf_code,
            'volume': total_traded,
            'price': price,
            'order_id': ','.join(str(o) for o in order_ids) if order_ids else '',
            'action': action,
            'order_type': order_type,
            'status': status,
            'executed': total_traded > 0,
            'sub_orders': sub_results,
        }

    def _execute_split_limit_pending(self, etf_code: str, action: str,
                                      limit_price: float,
                                      total_volume: int) -> Optional[Dict]:
        """拆单挂限价单（不等待成交确认，直接挂出多笔）

        用于"当前价未达目标价"的限价挂单场景。
        """
        sub_volumes = self._split_volume(total_volume, limit_price)
        if not sub_volumes:
            return None

        if len(sub_volumes) > 1:
            logger.info(f"  -> 拆单挂单: {etf_code} {total_volume}股 -> {len(sub_volumes)}笔 @{limit_price:.3f}")

        order_ids = []
        total_placed = 0
        for i, sub_vol in enumerate(sub_volumes):
            if action == 'buy':
                oid = self.qmt.buy_etf_limit(etf_code, limit_price, sub_vol)
            else:
                oid = self.qmt.sell_etf_limit(etf_code, limit_price, sub_vol)
            if oid:
                order_ids.append(oid)
                total_placed += sub_vol
                logger.info(f"  -> 子单{i+1}/{len(sub_volumes)}: {sub_vol}股 @{limit_price:.3f} 已挂单")
            else:
                logger.error(f"  -> 子单{i+1}/{len(sub_volumes)}: 挂单失败 {etf_code} {sub_vol}股")
            if i < len(sub_volumes) - 1:
                time.sleep(self.SPLIT_ORDER_INTERVAL)

        if not order_ids:
            return None

        return {
            'etf_code': etf_code,
            'volume': total_placed,
            'price': limit_price,
            'order_id': ','.join(str(o) for o in order_ids),
            'action': action,
            'order_type': 'limit',
            'status': '挂单中',
            'executed': False,
        }

    # ================================================================
    # 卖出逻辑
    # ================================================================

    def _execute_sell(self, order: Dict, is_call_auction: bool = False) -> Optional[Dict]:
        """执行卖出 - 支持 market / limit 两种下单方式，支持自动拆单

        is_call_auction=True 时（M1 集合竞价）：无论模型给的是 market 还是 limit，
        一律以「限价单」形式挂入开盘竞价，价格取模型给的 sell_price；若未给则取
        参考价（现价/昨收）略下浮 0.2% 以争取开盘成交。市价单在竞价窗口会被柜台拒绝。
        """
        etf_code = order.get('etf_code', '')
        action = order.get('action', '')
        sell_ratio = order.get('sell_ratio', 1)
        order_type = order.get('order_type', '')  # market / limit
        sell_price = order.get('sell_price', 0)

        logger.info(f"卖出判断: {etf_code} action='{action}' order_type='{order_type}'"
                    f"{' [竞价限价]' if is_call_auction else ''}")

        # --- 跳过条件 ---
        skip_keywords = ['持有', '不卖', '不动', '观望', '继续持', '过夜']
        if any(kw in action for kw in skip_keywords):
            if '清仓' not in action and '减仓' not in action:
                logger.info(f"  -> 跳过: action含持有/观望关键词")
                return None

        # --- 判断是否是卖出动作 ---
        sell_keywords = ['卖', '清仓', '减仓', '出局', '止损']
        if not any(kw in action for kw in sell_keywords):
            logger.info(f"  -> 跳过: action不含卖出关键词")
            return None

        # --- 获取持仓 ---
        positions = self.qmt.get_positions()
        position = next((p for p in positions if p['stock_code'] == etf_code), None)

        if not position:
            logger.warning(f"  -> 没有{etf_code}持仓")
            return None

        # --- 计算卖出数量 ---
        ratio = self._parse_ratio(sell_ratio)
        volume = int(position['available'] * ratio / 100) * 100
        if volume < 100:
            logger.warning(f"  -> 可卖数量不足: available={position['available']}, ratio={ratio}%")
            return None

        # --- 获取当前行情 ---
        etf_data = self.qmt.get_etf_data(etf_code)
        current_price = etf_data['last_price'] if etf_data else 0

        # ================================================================
        # 集合竞价路径：强制限价单参与开盘竞价
        # ================================================================
        if is_call_auction:
            limit_price = self._resolve_call_auction_limit_price(
                'sell', sell_price, current_price, etf_code)
            logger.info(f"  -> 竞价限价卖出: {etf_code} {volume}股 @{limit_price:.3f}（挂单等待开盘撮合）")
            return self._execute_split_limit_pending(
                etf_code, 'sell', limit_price, volume)

        # ================================================================
        # 下单逻辑：根据 order_type 决定市价还是限价（均支持自动拆单）
        # ================================================================

        if order_type == 'limit' and sell_price and sell_price > 0:
            # --- 限价卖出 ---
            if current_price > 0 and current_price >= sell_price:
                # 当前价已达到或超过目标价，直接市价卖出（可能拆单）
                logger.info(f"  -> 限价单已触发: 当前{current_price:.3f} >= 目标{sell_price:.3f}，市价卖出")
                return self._execute_split_order(
                    etf_code, 'sell', current_price, volume, 'market')
            else:
                # 当前价未达到目标价，挂限价单（可能拆单）
                logger.info(f"  -> 限价挂单: 当前{current_price:.3f} < 目标{sell_price:.3f}，挂单等待反弹")
                return self._execute_split_limit_pending(
                    etf_code, 'sell', sell_price, volume)
        else:
            # --- 市价卖出（默认） ---
            # 兼容旧格式：action含"反弹/拉高"但没有order_type字段的，也按限价处理
            if ('反弹' in action or '拉高' in action) and sell_price and sell_price > 0:
                if current_price > 0 and current_price >= sell_price:
                    logger.info(f"  -> 兼容模式-条件单触发: 当前{current_price:.3f} >= 目标{sell_price:.3f}")
                    # 落到下面的市价卖出逻辑
                else:
                    logger.info(f"  -> 兼容模式-限价挂单: 当前{current_price:.3f} < 目标{sell_price:.3f}")
                    return self._execute_split_limit_pending(
                        etf_code, 'sell', sell_price, volume)

            return self._execute_split_order(
                etf_code, 'sell', current_price, volume, 'market')

    # ================================================================
    # 买入逻辑
    # ================================================================

    def _execute_buy(self, order: Dict, is_call_auction: bool = False) -> Optional[Dict]:
        """执行买入 - 支持 market / limit 两种下单方式，支持自动拆单

        is_call_auction=True 时（M1 集合竞价）：无论模型给的是 market 还是 limit，
        一律以「限价单」形式挂入开盘竞价，价格取模型给的 buy_price；若未给则取
        参考价（现价/昨收）略上浮 0.2% 以争取开盘成交。市价单在竞价窗口会被柜台拒绝。
        """
        etf_code = order.get('etf_code', '')
        action = order.get('action', '')
        position_ratio = order.get('position_ratio', 0)
        order_type = order.get('order_type', '')  # market / limit
        buy_price = order.get('buy_price', 0)
        stop_loss = order.get('stop_loss', 0)

        logger.info(f"买入判断: {etf_code} action='{action}' order_type='{order_type}'"
                    f"{' [竞价限价]' if is_call_auction else ''}")

        # --- 跳过条件 ---
        skip_keywords = ['不买', '观望', '不动', '等待']
        if any(kw in action for kw in skip_keywords):
            logger.info(f"  -> 跳过: action含不买/观望关键词")
            return None

        # --- 判断是否是买入动作 ---
        buy_keywords = ['买入', '买', '加仓']
        if not any(kw in action for kw in buy_keywords):
            logger.info(f"  -> 跳过: action不含买入关键词")
            return None

        # --- 止损位校验：止损必须低于买入价 ---
        if stop_loss and buy_price and stop_loss >= buy_price:
            logger.warning(
                f"  -> 止损位异常: stop_loss={stop_loss} >= buy_price={buy_price}，"
                f"自动修正为止损=买入价*0.97"
            )
            stop_loss = round(buy_price * 0.97, 3)
            order['stop_loss'] = stop_loss

        # --- 获取行情 ---
        etf_data = self.qmt.get_etf_data(etf_code)
        if not etf_data:
            logger.warning(f"  -> 无法获取{etf_code}行情")
            return None

        current_price = etf_data['last_price']

        # --- 计算买入数量（含总仓位 / 单只累计硬校验） ---
        account = self.qmt.get_account_info()
        if not account:
            logger.warning(f"  -> 无法获取账户信息，跳过买入")
            return None
        available_cash = account.get('available_cash', 0)
        total_asset = account.get('total_asset', 0)

        # 防御：账户查询异常兜底（total_asset / 可用资金读取异常）
        cap = Config.SIM_INITIAL_CAPITAL if Config.QMT_SIMULATE else Config.INITIAL_CAPITAL
        if total_asset <= 0:
            total_asset = cap
        if available_cash <= 0:
            logger.warning(f"  -> 可用资金不足: {available_cash}")
            return None
        if available_cash > cap * 3:
            logger.warning(
                f"  -> 可用资金异常偏大({available_cash:.0f} > 初始资金{cap}的3倍)，"
                f"疑似账户查询异常，降级为初始资金以防数量错乱"
            )
            available_cash = cap

        ratio = self._parse_ratio(position_ratio)
        # 单笔比例上限（不超过单只上限）
        ratio = min(ratio, Config.MAX_SINGLE_POSITION * 100)
        plan_amount = available_cash * ratio / 100  # 计划买入金额（基于可用资金）

        # --- 单只累计校验：已有持仓 + 本次买入 不得超单只上限 ---
        positions = self.qmt.get_positions()
        cur_pos_value = 0
        for p in positions:
            if p['stock_code'] == etf_code:
                cur_pos_value = p.get('market_value', 0)
                break
        total_mkt = sum(p.get('market_value', 0) for p in positions)

        single_cap = total_asset * Config.MAX_SINGLE_POSITION
        if cur_pos_value + plan_amount > single_cap:
            allowed = single_cap - cur_pos_value
            if allowed <= 0:
                logger.warning(
                    f"  -> 单只已达上限: {etf_code} 现持{cur_pos_value:.0f}元 >= "
                    f"单只上限{single_cap:.0f}元，拒绝加仓"
                )
                self._alert("单只仓位已达上限",
                            f"现有持仓已占满单只上限，拒绝加仓", etf_code)
                return None
            plan_amount = min(plan_amount, allowed)
            logger.info(f"  -> 单只累计超上限，缩量: {etf_code} "
                         f"计划{available_cash * min(ratio, Config.MAX_SINGLE_POSITION * 100) / 100:.0f}"
                         f"→{plan_amount:.0f}元")

        # --- 总仓位校验：当前总持仓 + 本次买入 不得超总仓位上限 ---
        total_cap = total_asset * Config.MAX_TOTAL_POSITION
        if total_mkt + plan_amount > total_cap:
            allowed = total_cap - total_mkt
            if allowed <= 0:
                logger.warning(
                    f"  -> 总仓位已达上限: 现持{total_mkt:.0f}元 >= "
                    f"总上限{total_cap:.0f}元，拒绝新买入"
                )
                self._alert("总仓位已达上限",
                            f"当前总持仓{total_mkt:.0f}元已达上限{total_cap:.0f}元，拒绝新买入",
                            etf_code)
                return None
            plan_amount = min(plan_amount, allowed)
            logger.info(f"  -> 总仓位超上限，缩量: "
                         f"计划{available_cash * min(ratio, Config.MAX_SINGLE_POSITION * 100) / 100:.0f}"
                         f"→{plan_amount:.0f}元")

        # 计算数量：限价单用挂单价，市价单用现价
        ref_price = buy_price if (order_type == 'limit' and buy_price > 0) else current_price
        if ref_price <= 0:
            logger.warning(f"  -> 参考价无效: {ref_price}")
            return None
        volume = int(plan_amount / ref_price / 100) * 100

        if volume < 100:
            logger.warning(
                f"  -> 买入数量不足100股: plan_amount={plan_amount:.0f}, "
                f"price={ref_price}, ratio={ratio}%"
            )
            return None

        # ================================================================
        # 集合竞价路径：强制限价单参与开盘竞价
        # ================================================================
        if is_call_auction:
            limit_price = self._resolve_call_auction_limit_price(
                'buy', buy_price, current_price, etf_code)
            logger.info(f"  -> 竞价限价买入: {etf_code} {volume}股 @{limit_price:.3f}（挂单等待开盘撮合）")
            return self._execute_split_limit_pending(
                etf_code, 'buy', limit_price, volume)

        # ================================================================
        # 下单逻辑：根据 order_type 决定市价还是限价（均支持自动拆单）
        # ================================================================

        if order_type == 'limit' and buy_price and buy_price > 0:
            # --- 限价买入 ---
            if current_price <= buy_price:
                # 当前价已回调到目标价或更低，直接市价买入（可能拆单）
                logger.info(f"  -> 限价单已触发: 当前{current_price:.3f} <= 目标{buy_price:.3f}，市价买入")
                return self._execute_split_order(
                    etf_code, 'buy', current_price, volume, 'market')
            else:
                # 当前价高于目标价，挂限价单等待回调（可能拆单）
                logger.info(f"  -> 限价挂单: 当前{current_price:.3f} > 目标{buy_price:.3f}，挂单等待回调")
                return self._execute_split_limit_pending(
                    etf_code, 'buy', buy_price, volume)
        else:
            # --- 市价买入（默认） ---
            # 兼容旧格式：action含"回调/等"但没有order_type字段的，按限价处理
            if ('回调' in action or '等' in action) and buy_price and buy_price > 0:
                if not buy_price or buy_price <= 0:
                    buy_price = self._extract_price_from_action(action)

                if buy_price and buy_price > 0:
                    if current_price <= buy_price:
                        logger.info(f"  -> 兼容模式-条件单触发: 当前{current_price:.3f} <= 目标{buy_price:.3f}")
                        # 落到下面的市价买入逻辑
                    else:
                        logger.info(f"  -> 兼容模式-限价挂单: 当前{current_price:.3f} > 目标{buy_price:.3f}")
                        # 重新用挂单价计算数量
                        volume = int(max_amount / buy_price / 100) * 100
                        if volume < 100:
                            logger.warning(f"  -> 限价单数量不足100股")
                            return None
                        return self._execute_split_limit_pending(
                            etf_code, 'buy', buy_price, volume)

            return self._execute_split_order(
                etf_code, 'buy', current_price, volume, 'market')

    # ================================================================
    # 即时通知（废单 / 风控拦截）
    # ================================================================

    def _alert(self, title: str, message: str, etf_code: str = ''):
        """交易异常即时通知：日志 ERROR + 告警文件 + Windows 弹窗(best-effort)

        用途：废单 / 风控拦截发生时，用户即使没在盯日志也能立刻发现。
        """
        logger.error(f"⚠️ 交易警报 [{title}] {etf_code} {message}")
        # 1) 写告警文件，便于事后汇总查看
        try:
            alert_dir = Path('logs/alerts')
            alert_dir.mkdir(parents=True, exist_ok=True)
            alert_file = alert_dir / f"{datetime.datetime.now().strftime('%Y-%m-%d')}.md"
            ts = datetime.datetime.now().strftime('%H:%M:%S')
            with open(alert_file, 'a', encoding='utf-8') as f:
                f.write(f"- `{ts}` **{title}** {etf_code} {message}\n")
        except Exception as e:
            logger.warning(f"写入告警文件失败: {e}")
        # 2) Windows 弹窗（best-effort，无桌面/无权限则静默忽略）
        self._toast(title, f"{etf_code} {message}" if etf_code else message)

    @staticmethod
    def _toast(title: str, message: str):
        """Windows 弹窗通知（依赖 .NET MessageBox，无需第三方库；失败静默忽略）"""
        try:
            ps = (
                f'powershell -NoProfile -Command '
                f'"Add-Type -AssemblyName System.Windows.Forms; '
                f'[System.Windows.Forms.MessageBox]::Show(\''
                f'{message.replace(chr(39), chr(39) + chr(39))}\', \'{title}\')"'
            )
            subprocess.run(ps, shell=True, timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    # ================================================================
    # 辅助方法
    # ================================================================

    @staticmethod
    def _parse_ratio(ratio_value) -> float:
        """解析仓位比例，统一输出百分比数值。

        兼容输入：
        - 0.25 / "0.25"  -> 25   （小数比例，×100）
        - 25   / "25"    -> 25   （已是百分比）
        - "100%"        -> 100
        注意：整数 1 视为 100%（不是 1%），否则仓位会被放大 100 倍。
        """
        if isinstance(ratio_value, str):
            ratio_value = ratio_value.replace('%', '').strip()
            try:
                ratio_value = float(ratio_value)
            except ValueError:
                return 100.0
        if not isinstance(ratio_value, (int, float)):
            return 100.0
        # <=1 视为小数比例，×100 成百分比；>1 视为已是百分比
        return ratio_value * 100 if ratio_value <= 1 else float(ratio_value)

    @staticmethod
    def _extract_price_from_action(action: str) -> float:
        """从 action 文本中提取价格，如 '回调到1.060买入' -> 1.060"""
        match = re.search(r'(\d+\.?\d*)', action)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                pass
        return 0

    def _resolve_call_auction_limit_price(self, side: str, model_price: float,
                                          current_price: float,
                                          etf_code: str) -> float:
        """为集合竞价计算一个合法且尽量能成交的限价。

        - 优先用模型给的 buy_price/sell_price（模型已结合开盘价设定）；
        - 模型未给价格时，用现价（或昨收兜底）做微小偏移以争取开盘撮合：
          buy 上浮 0.2%，sell 下浮 0.2%；
        - 限价必须落在昨收 ±10% 的竞价范围内，否则柜台会拒单，故做截断。
        返回四舍五入至 3 位小数的价格。
        """
        yclose = self.qmt.get_yesterday_close(etf_code) if hasattr(self.qmt, 'get_yesterday_close') else 0
        yclose = yclose or 0.0

        # 参考价：现价优先，昨收兜底
        ref = current_price if (current_price and current_price > 0) else (yclose or 0)
        if ref <= 0:
            # 实在拿不到任何价格，回退到模型价；仍无效则报 0（调用方会因 ref_price<=0 放弃）
            return round(model_price, 3) if model_price and model_price > 0 else 0.0

        if model_price and model_price > 0:
            price = float(model_price)
        else:
            tick = 1.002 if side == 'buy' else 0.998
            price = ref * tick

        # 截断到昨收 ±10% 竞价范围（无昨收时不截断）
        if yclose > 0:
            lo, hi = yclose * 0.90, yclose * 1.10
            price = max(lo, min(hi, price))

        return round(price, 3)
