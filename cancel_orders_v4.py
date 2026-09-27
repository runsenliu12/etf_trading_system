"""撤单 v4 - 异步撤单 + 错误队列检查（修正版）

修正点（相对于旧版）：
1. 异步撤单(cancel_order_stock_*_async) 依赖 XtQuantTrader 的 callback 对象，
   旧版 connect() 创建 trader 时没传 callback -> self.callback=None ->
   内部 self.callback.on_cancel_order_stock_async_response 抛
   'NoneType' object has no attribute ...'。
   本版在 connect() 之后显式挂上 XtQuantTraderCallback 子类实例，异步撤单即可工作。
2. queuing_order_errors_byid / queuing_cancel_errors_by_order_id 等是
   字典属性（内部错误队列），不是方法，旧版用 () 调用 -> 'dict' object is not callable。
   本版直接读取字典（不加括号），并改用回调捕获 on_order_error / on_cancel_error。
"""
import sys
import time
sys.path.insert(0, r'D:\策略\etf_trading_system')

from trading.qmt_client import QMTClient
# 注意：必须在导入 qmt_client 之后（其模块加载时会把 QMT 的 xtquant 路径加入 sys.path）
from xtquant.xttrader import XtQuantTraderCallback
from config.settings import Config


class CancelCallback(XtQuantTraderCallback):
    """收集异步响应与错误，供脚本事后读取。"""

    def __init__(self):
        self.cancel_responses = []      # on_cancel_order_stock_async_response
        self.order_errors = []          # on_order_error
        self.cancel_errors = []         # on_cancel_error

    def on_cancel_order_stock_async_response(self, response):
        # response: XtCancelOrderResponse -> 字段 order_id / error_id / error_msg / seq
        self.cancel_responses.append(response)
        err = getattr(response, 'error_id', 0)
        msg = getattr(response, 'error_msg', '')
        print(f"    [回调] 撤单响应 order_id={getattr(response,'order_id','')} "
              f"error_id={err} msg={msg}")

    def on_order_error(self, order_error):
        self.order_errors.append(order_error)
        print(f"    [回调] 下单错误: {order_error}")

    def on_cancel_error(self, cancel_error):
        self.cancel_errors.append(cancel_error)
        eid = getattr(cancel_error, 'error_id', None)
        if eid is None:
            eid = getattr(cancel_error, 'm_nErrorID', '?')
        emsg = getattr(cancel_error, 'error_msg', None)
        if emsg is None:
            emsg = getattr(cancel_error, 'm_strErrorMsg', '')
        print(f"    [回调] 撤单错误: error_id={eid} msg={emsg}")


def main():
    print("=" * 60)
    print("QMT 异步撤单 + 错误检查（修正版）")
    print("=" * 60)

    client = QMTClient()
    if not client.connect():
        print("❌ QMT连接失败")
        return

    # 关键修正 1：注册异步回调对象（connect 默认不传 callback）
    cb = CancelCallback()
    client.trader.callback = cb
    print("✓ 已注册异步回调对象")

    status_map = {
        48: '未知', 49: '待报', 50: '待报', 51: '已报',
        52: '已报待撤', 53: '部成待撤', 54: '部撤', 55: '已撤',
        56: '部分成交', 57: '全部成交', 58: '废单',
    }

    try:
        orders = client.trader.query_stock_orders(client.account)
        if not orders:
            print("无委托记录")
            client.disconnect()
            return

        # 提取可撤单字段
        targets = []
        for order in orders:
            code = getattr(order, 'stock_code', '') or getattr(order, 'm_strStockCode', '')
            name = Config.ETF_POOL.get(code, code)
            osysid = getattr(order, 'order_sysid', '') or getattr(order, 'm_strOrderSysID', '')
            oid = getattr(order, 'order_id', '') or getattr(order, 'm_nOrderID', '')
            market = 0 if '.SH' in code else 1
            targets.append((code, name, market, str(osysid), str(oid)))

        # 尝试异步撤单（按柜台合同编号 sysid）
        print("\n--- 异步撤单 cancel_order_stock_sysid_async ---")
        for i, (code, name, market, osysid, oid) in enumerate(targets):
            try:
                seq = client.trader.cancel_order_stock_sysid_async(
                    client.account, market, osysid
                )
                print(f"  [{i+1}] {code} {name} market={market} sysid={osysid} -> async seq={seq}")
            except Exception as e:
                print(f"  [{i+1}] {code} {name} -> 异常: {e}")
            time.sleep(0.3)

        # 同时尝试按 order_id 异步撤单（注意 order_id 必须是 int，不能是字符串）
        print("\n--- 异步撤单 cancel_order_stock_async (按 order_id) ---")
        for i, (code, name, market, osysid, oid) in enumerate(targets):
            # 仅当 order_id 是有效正整数时才尝试（待报/残留单可能为 0 或空）
            try:
                oid_int = int(oid)
            except (ValueError, TypeError):
                oid_int = 0
            if oid_int <= 0:
                print(f"  [{i+1}] {code} {name} -> 跳过: order_id 无效({oid!r})")
                continue
            try:
                seq = client.trader.cancel_order_stock_async(client.account, oid_int)
                print(f"  [{i+1}] {code} {name} order_id={oid_int} -> async seq={seq}")
            except Exception as e:
                print(f"  [{i+1}] {code} {name} -> 异常: {e}")
            time.sleep(0.2)

        # 等待异步响应回传
        print("\n等待 8 秒收集异步回调...")
        time.sleep(8)

        # 关键修正 2：错误队列是字典属性，直接读取（不要加括号）
        print("\n--- 撤单错误队列（字典属性，直接读）---")
        try:
            cancel_err_by_id = client.trader.queuing_cancel_errors_by_order_id
            print(f"  by_order_id: {cancel_err_by_id}")
        except Exception as e:
            print(f"  by_order_id 异常: {e}")

        try:
            cancel_err_by_sysid = client.trader.queuing_cancel_errors_by_order_sys_id
            print(f"  by_sys_id: {cancel_err_by_sysid}")
        except Exception as e:
            print(f"  by_sys_id 异常: {e}")

        print("\n--- 下单错误队列（字典属性，直接读）---")
        try:
            order_err_by_id = client.trader.queuing_order_errors_byid
            print(f"  by_id: {order_err_by_id}")
        except Exception as e:
            print(f"  by_id 异常: {e}")

        try:
            order_err_by_seq = client.trader.queuing_order_errors_byseq
            print(f"  by_seq: {order_err_by_seq}")
        except Exception as e:
            print(f"  by_seq 异常: {e}")

        # 汇总回调收集到的异步响应 / 错误
        print(f"\n--- 回调汇总 ---")
        print(f"  异步撤单响应数: {len(cb.cancel_responses)}")
        print(f"  下单错误回调数: {len(cb.order_errors)}")
        print(f"  撤单错误回调数: {len(cb.cancel_errors)}")

        # 最终委托状态
        print("\n--- 最终委托状态 ---")
        orders2 = client.trader.query_stock_orders(client.account)
        if orders2:
            for i, order in enumerate(orders2):
                code = getattr(order, 'stock_code', '') or getattr(order, 'm_strStockCode', '')
                name = Config.ETF_POOL.get(code, code)
                status_num = getattr(order, 'order_status', 0) or getattr(order, 'm_nOrderStatus', 0)
                status = status_map.get(status_num, f'未知({status_num})')
                volume = getattr(order, 'order_volume', 0) or getattr(order, 'm_nOrderVolume', 0)
                traded = getattr(order, 'traded_volume', 0) or getattr(order, 'm_nTradedVolume', 0)
                print(f"  [{i+1}] {code} {name} 状态={status} 委托={volume:,} 成交={traded:,}")
        else:
            print("  ✅ 无委托 - 全部撤销!")

        asset = client.get_account_info()
        if asset:
            print(f"\n  可用: {asset['available_cash']:,.2f}  冻结: {asset['frozen_cash']:,.2f}")

    except Exception as e:
        print(f"操作失败: {e}")
        import traceback
        traceback.print_exc()

    client.disconnect()


if __name__ == '__main__':
    main()
