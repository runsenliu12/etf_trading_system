"""
QMT交易客户端
"""
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

# 添加 xtquant 所在路径（QMT 安装目录下的 site-packages）
_XTQUANT_PATHS = [
    r'D:\国金证券QMT交易端\bin.x64\Lib\site-packages',
    r'D:\国金QMT交易端模拟\bin.x64\Lib\site-packages',
]
for _p in _XTQUANT_PATHS:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from xtquant import xtdata, xttrader
from xtquant.xttype import StockAccount
from xtquant.xtconstant import STOCK_BUY, STOCK_SELL, LATEST_PRICE, FIX_PRICE
from loguru import logger
from config.settings import Config

# 委托状态码映射
ORDER_STATUS_MAP = {
    48: '未知', 49: '待报', 50: '待报', 51: '已报',
    52: '已报待撤', 53: '部成待撤', 54: '部撤', 55: '已撤',
    56: '部分成交', 57: '全部成交', 58: '废单',
}

# 可撤单状态（未成交或部分成交）
CANCELABLE_STATUS = {48, 49, 50, 51, 52, 53, 56}

# 已终结状态（不可撤）
TERMINAL_STATUS = {54, 55, 57, 58}


class QMTClient:
    """QMT交易客户端"""

    def __init__(self):
        self.trader = None
        self.account = None
        self.connected = False

    def connect(self) -> bool:
        """连接QMT"""
        try:
            session_id = int(time.time())
            self.trader = xttrader.XtQuantTrader(
                str(Config.QMT_PATH), session_id
            )
            self.account = StockAccount(Config.QMT_ACCOUNT)
            self.trader.start()
            result = self.trader.connect()
            if result == 0:
                self.connected = True
                self.trader.subscribe(self.account)
                logger.info("QMT连接成功")
                return True
            else:
                logger.error(f"QMT连接失败: {result}")
                return False
        except Exception as e:
            logger.error(f"QMT连接异常: {e}")
            return False

    def disconnect(self):
        """断开连接"""
        if self.trader:
            self.trader.stop()
            self.connected = False
            logger.info("QMT已断开")

    def get_account_info(self) -> Dict:
        """获取账户信息

        兼容新旧版 xtquant 的 XtAsset 属性名：
          新版本: cash / frozen_cash / market_value / total_asset
          旧版本: m_dAvailable / m_dFrozenCash / m_dMarketValue / m_dTotalAsset / m_dPnl
        """
        if not self.connected:
            return {}
        try:
            asset = self.trader.query_stock_asset(self.account)
            if asset:
                # 优先用新属性名，旧版本回退到 m_dXxx
                available = getattr(asset, 'cash', None)
                if available is None:
                    available = getattr(asset, 'm_dAvailable', 0)
                frozen = getattr(asset, 'frozen_cash', None)
                if frozen is None:
                    frozen = getattr(asset, 'm_dFrozenCash', 0)
                market_value = getattr(asset, 'market_value', None)
                if market_value is None:
                    market_value = getattr(asset, 'm_dMarketValue', 0)
                total = getattr(asset, 'total_asset', None)
                if total is None:
                    total = getattr(asset, 'm_dTotalAsset', 0)
                pnl = getattr(asset, 'pnl', None)
                if pnl is None:
                    pnl = getattr(asset, 'm_dPnl', 0)
                return {
                    'available_cash': float(available or 0),
                    'frozen_cash': float(frozen or 0),
                    'market_value': float(market_value or 0),
                    'total_asset': float(total or 0),
                    'pnl': float(pnl or 0)
                }
        except Exception as e:
            logger.error(f"获取账户信息失败: {e}")
        return {}

    def get_positions(self) -> List[Dict]:
        """获取持仓"""
        if not self.connected:
            return []
        positions = []
        try:
            records = self.trader.query_stock_positions(self.account)
            for pos in records:
                if pos.volume > 0:
                    volume = pos.volume
                    cost_price = pos.open_price
                    market_value = pos.market_value
                    market_price = market_value / volume if volume else 0
                    # XtPosition 无 pnl / pnl_ratio 字段，手动计算（浮动盈亏）
                    pnl = getattr(pos, 'pnl', None)
                    if pnl is None:
                        pnl = market_value - cost_price * volume
                    pnl_ratio = getattr(pos, 'pnl_ratio', None)
                    if pnl_ratio is None:
                        pnl_ratio = (market_price / cost_price - 1) if cost_price else 0
                    positions.append({
                        'stock_code': pos.stock_code,
                        'volume': volume,
                        'available': pos.can_use_volume,
                        'cost_price': cost_price,
                        'market_price': market_price,
                        'market_value': market_value,
                        'pnl': float(pnl or 0),
                        'pnl_ratio': float(pnl_ratio or 0)
                    })
        except Exception as e:
            logger.error(f"获取持仓失败: {e}")
        return positions

    def get_etf_data(self, stock_code: str) -> Optional[Dict]:
        """获取ETF实时数据"""
        try:
            data = xtdata.get_full_tick([stock_code])
            if data and stock_code in data:
                tick = data[stock_code]
                last_price = tick.get('lastPrice', 0)
                last_close = tick.get('lastClose', 1)
                return {
                    'last_price': last_price,
                    'open': tick.get('open', 0),
                    'high': tick.get('high', 0),
                    'low': tick.get('low', 0),
                    'volume': tick.get('volume', 0),
                    'amount': tick.get('amount', 0),
                    'last_close': last_close,
                    'change_pct': (last_price / last_close - 1) * 100 if last_close else 0
                }
        except Exception as e:
            logger.error(f"获取{stock_code}数据失败: {e}")
        return None

    def get_all_etf_data(self) -> Dict[str, Dict]:
        """获取所有ETF数据"""
        all_data = {}
        for code in Config.ETF_POOL:
            data = self.get_etf_data(code)
            if data:
                all_data[code] = data
        return all_data

    def buy_etf(self, stock_code: str, price: float, volume: int) -> Optional[str]:
        """买入ETF（市价单，按最新价成交）"""
        if not self.connected:
            return None
        if not self.is_trading_hours():
            logger.warning(f"非交易时段，拒绝下单: 买入 {stock_code}")
            return None
        try:
            volume = int(volume / 100) * 100
            if volume < 100:
                return None
            order_id = self.trader.order_stock(
                self.account, stock_code, STOCK_BUY, volume,
                LATEST_PRICE, -1, 'AI买入', ''
            )
            if order_id and order_id > 0:
                logger.info(f"市价买入已提交: {stock_code} {volume}股 序号:{order_id}")
                return str(order_id)
            else:
                logger.error(f"市价买入被拒: {stock_code} order_stock返回={order_id}")
                return None
        except Exception as e:
            logger.error(f"市价买入失败: {e}")
            return None

    def buy_etf_limit(self, stock_code: str, limit_price: float, volume: int) -> Optional[str]:
        """买入ETF（限价单，挂单等待回调到指定价格成交）"""
        if not self.connected:
            return None
        if not self.is_trading_hours():
            logger.warning(f"非交易时段，拒绝下单: 限价买入 {stock_code}")
            return None
        try:
            volume = int(volume / 100) * 100
            if volume < 100:
                return None
            order_id = self.trader.order_stock(
                self.account, stock_code, STOCK_BUY, volume,
                FIX_PRICE, limit_price, 'AI限价买入', ''
            )
            if order_id and order_id > 0:
                logger.info(f"限价买入已提交: {stock_code} {volume}股 @{limit_price:.3f} 序号:{order_id}")
                return str(order_id)
            else:
                logger.error(f"限价买入被拒: {stock_code} order_stock返回={order_id}")
                return None
        except Exception as e:
            logger.error(f"限价买入失败: {e}")
            return None

    def sell_etf(self, stock_code: str, price: float, volume: int) -> Optional[str]:
        """卖出ETF（市价单，按最新价成交）"""
        if not self.connected:
            return None
        if not self.is_trading_hours():
            logger.warning(f"非交易时段，拒绝下单: 卖出 {stock_code}")
            return None
        try:
            volume = int(volume / 100) * 100
            if volume < 100:
                return None
            order_id = self.trader.order_stock(
                self.account, stock_code, STOCK_SELL, volume,
                LATEST_PRICE, -1, 'AI卖出', ''
            )
            if order_id and order_id > 0:
                logger.info(f"市价卖出已提交: {stock_code} {volume}股 序号:{order_id}")
                return str(order_id)
            else:
                logger.error(f"市价卖出被拒: {stock_code} order_stock返回={order_id}")
                return None
        except Exception as e:
            logger.error(f"市价卖出失败: {e}")
            return None

    def sell_etf_limit(self, stock_code: str, limit_price: float, volume: int) -> Optional[str]:
        """卖出ETF（限价单，挂单等待反弹到指定价格成交）"""
        if not self.connected:
            return None
        if not self.is_trading_hours():
            logger.warning(f"非交易时段，拒绝下单: 限价卖出 {stock_code}")
            return None
        try:
            volume = int(volume / 100) * 100
            if volume < 100:
                return None
            order_id = self.trader.order_stock(
                self.account, stock_code, STOCK_SELL, volume,
                FIX_PRICE, limit_price, 'AI限价卖出', ''
            )
            if order_id and order_id > 0:
                logger.info(f"限价卖出已提交: {stock_code} {volume}股 @{limit_price:.3f} 序号:{order_id}")
                return str(order_id)
            else:
                logger.error(f"限价卖出被拒: {stock_code} order_stock返回={order_id}")
                return None
        except Exception as e:
            logger.error(f"限价卖出失败: {e}")
            return None

    def get_yesterday_close(self, stock_code: str) -> float:
        """获取昨日收盘价"""
        try:
            data = xtdata.get_full_tick([stock_code])
            if data and stock_code in data:
                return data[stock_code].get('lastClose', 0)
        except:
            pass
        return 0

    # ================================================================
    # 交易安全机制：交易时段校验 / 撤单 / 成交确认
    # ================================================================

    @staticmethod
    def is_trading_hours() -> bool:
        """检查当前是否在A股交易时段

        交易时段：
        - 集合竞价：09:15 - 09:25
        - 上午连续竞价：09:30 - 11:30
        - 下午连续竞价：13:00 - 15:00

        周末和法定节假日不算（简化判断，仅排除周末）
        """
        now = datetime.now()
        # 周末排除
        if now.weekday() >= 5:
            return False
        current_time = now.hour * 100 + now.minute
        # 09:15-09:25 集合竞价，09:30-11:30 上午，13:00-15:00 下午
        if 915 <= current_time <= 925:
            return True
        if 930 <= current_time <= 1130:
            return True
        if 1300 <= current_time <= 1500:
            return True
        return False

    @staticmethod
    def is_call_auction_window() -> bool:
        """当前是否处于集合竞价窗口（09:15-09:25，周末除外）

        该窗口内柜台只接受限价单，市价单会被拒绝或异常。
        用于 M1 时刻强制把订单转为限价单参与开盘竞价。
        """
        now = datetime.now()
        if now.weekday() >= 5:
            return False
        current_time = now.hour * 100 + now.minute
        return 915 <= current_time <= 925

    @staticmethod
    def _get_market_code(stock_code: str) -> int:
        """从证券代码推断市场代码：0=上海(.SH)，1=深圳(.SZ)"""
        return 0 if '.SH' in stock_code else 1

    def query_today_orders(self) -> List[Dict]:
        """查询当日所有委托，返回结构化列表"""
        if not self.connected:
            return []
        try:
            orders = self.trader.query_stock_orders(self.account)
            result = []
            for order in orders:
                code = getattr(order, 'stock_code', '') or getattr(order, 'm_strStockCode', '')
                order_seq = getattr(order, 'order_seq', 0) or getattr(order, 'm_nOrderSeq', 0)
                order_type = getattr(order, 'order_type', 0) or getattr(order, 'm_nOrderType', 0)
                status_num = getattr(order, 'order_status', 0) or getattr(order, 'm_nOrderStatus', 0)
                volume = getattr(order, 'order_volume', 0) or getattr(order, 'm_nOrderVolume', 0)
                traded = getattr(order, 'traded_volume', 0) or getattr(order, 'm_nTradedVolume', 0)
                osysid = getattr(order, 'order_sysid', '') or getattr(order, 'm_strOrderSysID', '')
                result.append({
                    'stock_code': code,
                    'order_seq': int(order_seq) if order_seq else 0,
                    'direction': 'buy' if order_type == 23 else ('sell' if order_type == 24 else str(order_type)),
                    'status': status_num,
                    'status_name': ORDER_STATUS_MAP.get(status_num, f'未知({status_num})'),
                    'order_volume': volume,
                    'traded_volume': traded,
                    'order_sysid': str(osysid),
                    'is_filled': status_num in (56, 57),   # 部分成交 / 全部成交
                    'is_cancelable': status_num in CANCELABLE_STATUS,
                    'is_terminal': status_num in TERMINAL_STATUS,
                })
            return result
        except Exception as e:
            logger.error(f"查询当日委托失败: {e}")
            return []

    def cancel_pending_orders(self, stock_code: str = None) -> int:
        """撤掉未成交的委托单

        :param stock_code: 指定证券代码则只撤该代码的未成交单，None则撤全部
        :return: 成功撤单的数量
        """
        if not self.connected:
            return 0

        orders = self.query_today_orders()
        if not orders:
            return 0

        # 筛选可撤单（未成交、未终结）
        to_cancel = [
            o for o in orders
            if o['is_cancelable'] and o['traded_volume'] == 0
            and (stock_code is None or o['stock_code'] == stock_code)
        ]

        if not to_cancel:
            logger.info("无待撤委托")
            return 0

        logger.info(f"准备撤单 {len(to_cancel)} 笔" + (f" (代码:{stock_code})" if stock_code else ""))
        # 幽灵单错误码：柜台里根本没有这笔单的记录（上一会话残留的"待报"垃圾记录），
        # 撤单必然失败，无需重试，避免刷爆 QMT 的"撤单失败"紧急报警。
        #   -61 : 异步撤单回调里的 COUNTER 250001 [订单表记录不存在]
        #   -1  : 同步撤单返回 [-1] + [COUNTER][250001][订单表记录不存在]
        GHOST_ERROR_CODES = (-61, -1)
        success = 0
        for order in to_cancel:
            code = order['stock_code']
            osysid = order['order_sysid']
            market = self._get_market_code(code)
            try:
                result = self.trader.cancel_order_stock_sysid(
                    self.account, market, osysid
                )
                if result == 0:
                    logger.info(f"  撤单请求已提交: {code} sysid={osysid}")
                    success += 1
                elif result in GHOST_ERROR_CODES:
                    logger.warning(
                        f"  撤单跳过(幽灵单): {code} sysid={osysid} 柜台无此单记录(result={result})，"
                        f"请重启 XtMiniQmt.exe 清理上一会话残留"
                    )
                else:
                    logger.warning(f"  撤单失败: {code} sysid={osysid} result={result}")
            except Exception as e:
                logger.error(f"  撤单异常: {code} sysid={osysid} {e}")
            time.sleep(0.3)

        # 等待QMT处理撤单
        if success > 0:
            time.sleep(2)
            logger.info(f"撤单完成: 提交{len(to_cancel)}笔, 成功{success}笔")

        return success

    def confirm_order(self, order_seq: int, timeout: int = 10) -> Dict:
        """确认订单成交状态

        下单后轮询查询，区分"提交成功"和"真正成交"。

        :param order_seq: order_stock() 返回的序号
        :param timeout: 最大等待秒数
        :return: {'status': str, 'traded_volume': int, 'order_volume': int, 'filled': bool}
        """
        if not self.connected:
            return {'status': '未连接', 'traded_volume': 0, 'order_volume': 0, 'filled': False}

        deadline = time.time() + timeout
        last_status = None
        while time.time() < deadline:
            orders = self.query_today_orders()
            # 通过 order_seq 精确匹配订单（避免错配历史成交单）
            matched = [o for o in orders if o.get('order_seq') == order_seq]

            if matched:
                order = matched[0]
                status_num = order['status']
                last_status = status_num

                if status_num in (57, 56):  # 全部成交 / 部分成交
                    logger.info(
                        f"  订单确认: {order['stock_code']} "
                        f"成交{order['traded_volume']}/{order['order_volume']}股 "
                        f"状态={order['status_name']}"
                    )
                    return {
                        'status': order['status_name'],
                        'traded_volume': order['traded_volume'],
                        'order_volume': order['order_volume'],
                        'filled': order['traded_volume'] > 0,
                    }
                if status_num == 58:  # 废单（柜台拒绝，如120156金额超限）
                    logger.error(
                        f"  订单废单: {order['stock_code']} "
                        f"seq={order_seq} 状态={order['status_name']}"
                    )
                    return {
                        'status': order['status_name'],
                        'traded_volume': 0,
                        'order_volume': order['order_volume'],
                        'filled': False,
                    }
                if status_num == 55:  # 已撤
                    logger.warning(
                        f"  订单已撤: {order['stock_code']} "
                        f"seq={order_seq} 状态={order['status_name']}"
                    )
                    return {
                        'status': order['status_name'],
                        'traded_volume': order['traded_volume'],
                        'order_volume': order['order_volume'],
                        'filled': False,
                    }
                # 其他状态（待报/已报/部成待撤等），继续轮询
            # 若匹配不到该订单，可能是废单被柜台删除，继续轮询直到超时
            time.sleep(1)

        # 超时仍未找到该订单（大概率是废单，柜台未记录；或网络延迟）
        status_name = '废单(柜台未记录)' if last_status is None else ORDER_STATUS_MAP.get(last_status, '未知')
        logger.error(f"  订单确认超时({timeout}s): seq={order_seq} 状态={status_name}")
        return {
            'status': status_name,
            'traded_volume': 0,
            'order_volume': 0,
            'filled': False,
        }

    # ================================================================
    # 历史数据 & 市场分析方法（用 xtdata 替代联网搜索）
    # ================================================================

    # 主要市场指数代码
    MARKET_INDICES = {
        '000001.SH': '上证指数',
        '399001.SZ': '深证成指',
        '399006.SZ': '创业板指',
        '000300.SH': '沪深300',
        '000905.SH': '中证500',
        '000852.SH': '中证1000',
    }

    def _ensure_history_data(self, code: str, days: int = 10):
        """确保本地有历史数据缓存"""
        try:
            end = datetime.now().strftime('%Y%m%d')
            start = (datetime.now() - timedelta(days=days + 15)).strftime('%Y%m%d')
            xtdata.download_history_data(code, '1d', start, end)
        except Exception as e:
            logger.debug(f"下载{code}历史数据: {e}")

    def get_etf_history(self, code: str, days: int = 5) -> Optional[List[Dict]]:
        """获取ETF近N天日K线数据"""
        try:
            self._ensure_history_data(code, days)
            end = datetime.now().strftime('%Y%m%d')
            start = (datetime.now() - timedelta(days=days + 15)).strftime('%Y%m%d')
            result = xtdata.get_market_data_ex([], [code], period='1d',
                                                start_time=start, end_time=end)
            if result and code in result:
                df = result[code]
                if df is not None and len(df) > 0:
                    records = []
                    for idx, row in df.tail(days + 1).iterrows():
                        records.append({
                            'date': idx.strftime('%Y-%m-%d') if hasattr(idx, 'strftime') else str(idx),
                            'open': float(row['open']),
                            'high': float(row['high']),
                            'low': float(row['low']),
                            'close': float(row['close']),
                            'volume': float(row['volume']),
                            'amount': float(row.get('amount', 0)),
                        })
                    return records
        except Exception as e:
            logger.debug(f"获取{code}历史数据失败: {e}")
        return None

    def get_multi_day_performance(self, days: int = 5) -> List[Dict]:
        """
        获取所有ETF近N天涨跌幅排名
        返回按涨幅降序排列的列表
        """
        results = []
        for code in Config.ETF_POOL:
            hist = self.get_etf_history(code, days)
            if hist and len(hist) >= 2:
                name = Config.ETF_POOL.get(code, code)
                sector = Config.get_sector_by_code(code)
                latest_close = hist[-1]['close']
                # 计算近1天、3天、5天涨跌幅
                perf = {
                    'code': code,
                    'name': name,
                    'sector': sector,
                    'close': latest_close,
                    'change_1d': 0,
                    'change_3d': 0,
                    'change_5d': 0,
                }
                # 近1天涨幅（昨天收盘 vs 前天收盘）
                if len(hist) >= 2:
                    prev_close = hist[-2]['close']
                    perf['change_1d'] = (latest_close / prev_close - 1) * 100 if prev_close else 0
                # 近3天涨幅
                if len(hist) >= 4:
                    base_close = hist[-4]['close']
                    perf['change_3d'] = (latest_close / base_close - 1) * 100 if base_close else 0
                # 近5天涨幅
                if len(hist) >= 6:
                    base_close = hist[-6]['close']
                    perf['change_5d'] = (latest_close / base_close - 1) * 100 if base_close else 0
                results.append(perf)

        # 按近1天涨幅降序排列
        results.sort(key=lambda x: x['change_1d'], reverse=True)
        return results

    def get_sector_ranking(self, days: int = 3) -> List[Dict]:
        """
        按板块汇总ETF近N天平均涨跌幅，返回板块排名
        """
        etf_perf = self.get_multi_day_performance(days)
        sector_data = {}
        for etf in etf_perf:
            sector = etf['sector']
            if sector not in sector_data:
                sector_data[sector] = {
                    'sector': sector,
                    'etfs': [],
                    'avg_change_1d': 0,
                    'avg_change_3d': 0,
                    'avg_change_5d': 0,
                    'count': 0,
                }
            sector_data[sector]['etfs'].append(etf['name'])
            sector_data[sector]['count'] += 1

        # 计算板块平均涨跌幅
        perf_key = {1: 'change_1d', 3: 'change_3d', 5: 'change_5d'}
        for sector_info in sector_data.values():
            sector_etfs = [e for e in etf_perf if e['sector'] == sector_info['sector']]
            if sector_etfs:
                sector_info['avg_change_1d'] = sum(e['change_1d'] for e in sector_etfs) / len(sector_etfs)
                sector_info['avg_change_3d'] = sum(e['change_3d'] for e in sector_etfs) / len(sector_etfs)
                sector_info['avg_change_5d'] = sum(e['change_5d'] for e in sector_etfs) / len(sector_etfs)

        # 按近1天平均涨幅降序
        ranking = sorted(sector_data.values(), key=lambda x: x['avg_change_1d'], reverse=True)
        return ranking

    def get_index_data(self) -> Dict[str, Dict]:
        """获取主要市场指数实时数据"""
        results = {}
        for code, name in self.MARKET_INDICES.items():
            try:
                data = xtdata.get_full_tick([code])
                if data and code in data:
                    tick = data[code]
                    last_price = tick.get('lastPrice', 0)
                    last_close = tick.get('lastClose', 1)
                    results[code] = {
                        'name': name,
                        'price': last_price,
                        'change_pct': (last_price / last_close - 1) * 100 if last_close else 0,
                        'volume': tick.get('volume', 0),
                        'amount': tick.get('amount', 0),
                    }
            except Exception as e:
                logger.debug(f"获取指数{code}失败: {e}")
        return results

    def get_cross_border_data(self) -> Dict[str, Dict]:
        """
        获取跨境ETF数据（作为外盘代理）
        纳指ETF/标普500ETF/中概互联ETF/恒生科技ETF
        """
        cross_border_codes = {
            '513100.SH': '纳指ETF',
            '513500.SH': '标普500ETF',
            '513050.SH': '中概互联ETF',
            '513330.SH': '恒生科技ETF',
            '159920.SZ': '恒生ETF',
        }
        results = {}
        for code, name in cross_border_codes.items():
            data = self.get_etf_data(code)
            if data:
                # 获取近5天走势判断外盘趋势
                hist = self.get_etf_history(code, 5)
                trend_5d = 0
                if hist and len(hist) >= 2:
                    trend_5d = (data['last_price'] / hist[0]['close'] - 1) * 100 if hist[0]['close'] else 0
                results[code] = {
                    'name': name,
                    'price': data['last_price'],
                    'change_pct': data['change_pct'],
                    'trend_5d': trend_5d,
                    'volume': data['volume'],
                }
        return results