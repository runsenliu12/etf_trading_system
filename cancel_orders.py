"""撤单 v3 - 用原始订单序号 + sysid 双重撤单"""
import sys
import time
sys.path.insert(0, r'D:\策略\etf_trading_system')

from trading.qmt_client import QMTClient
from config.settings import Config

def main():
    print("=" * 60)
    print("QMT 撤单操作 v3 (原始序号 + sysid)")
    print("=" * 60)

    client = QMTClient()
    if not client.connect():
        print("❌ QMT连接失败")
        return

    status_map = {
        48: '未知', 49: '待报', 50: '待报', 51: '已报',
        52: '已报待撤', 53: '部成待撤', 54: '部撤', 55: '已撤',
        56: '部分成交', 57: '全部成交', 58: '废单',
    }

    # 从交易日志获取的原始订单序号
    original_seqs = [
        (1090519105, '159516.SZ', '半导体设备ETF', 4217100),
        (1090519106, '517520.SH', '黄金股ETF', 1213600),
        (1090519107, '512400.SH', '有色金属ETF', 763600),
        (1090519108, '517520.SH', '黄金股ETF', 1444200),
        (1090519109, '512400.SH', '有色金属ETF', 1081700),
        (1090519110, '515220.SH', '煤炭ETF', 937500),
        (1090519111, '512400.SH', '有色金属ETF', 551700),
        (1090519112, '512400.SH', '有色金属ETF', 937800),
    ]

    try:
        # 先查当前订单
        orders = client.trader.query_stock_orders(client.account)
        print(f"\n当前委托: {len(orders) if orders else 0} 条")
        if orders:
            for i, order in enumerate(orders):
                code = getattr(order, 'stock_code', '') or getattr(order, 'm_strStockCode', '')
                name = Config.ETF_POOL.get(code, code)
                status_num = getattr(order, 'order_status', 0) or getattr(order, 'm_nOrderStatus', 0)
                status = status_map.get(status_num, f'未知({status_num})')
                volume = getattr(order, 'order_volume', 0) or getattr(order, 'm_nOrderVolume', 0)
                osysid = getattr(order, 'order_sysid', '') or getattr(order, 'm_strOrderSysID', '')
                print(f"  [{i+1}] {code} {name} 委托量={volume:,} 状态={status} sysid={osysid}")

        # 方式1: 用原始序号撤单
        print("\n--- 方式1: cancel_order_stock(account, 原始序号) ---")
        for seq, code, name, vol in original_seqs:
            try:
                result = client.trader.cancel_order_stock(client.account, seq)
                mark = "✅" if result == 0 else ("⚠️" if result == -1 else ("NotFound" if result == -2 else "❌"))
                print(f"  {mark} seq={seq} {code} {name} {vol:,}股 -> result={result}")
            except Exception as e:
                print(f"  ❌ seq={seq} {code} {name} -> 异常: {e}")
            time.sleep(0.3)

        # 方式2: 用 sysid 撤单 (market: 0=上海, 1=深圳)
        print("\n--- 方式2: cancel_order_stock_sysid(account, market, sysid) ---")
        if orders:
            for i, order in enumerate(orders):
                code = getattr(order, 'stock_code', '') or getattr(order, 'm_strStockCode', '')
                name = Config.ETF_POOL.get(code, code)
                osysid = getattr(order, 'order_sysid', '') or getattr(order, 'm_strOrderSysID', '')
                # 判断市场: .SH=上海(0), .SZ=深圳(1)
                market = 0 if '.SH' in code else 1
                try:
                    result = client.trader.cancel_order_stock_sysid(client.account, market, str(osysid))
                    mark = "✅" if result == 0 else "⚠️"
                    print(f"  {mark} [{i+1}] {code} {name} market={market} sysid={osysid} -> result={result}")
                except Exception as e:
                    print(f"  ❌ [{i+1}] {code} {name} market={market} sysid={osysid} -> 异常: {e}")
                time.sleep(0.3)

        # 等待3秒
        print("\n等待3秒后重新查询...")
        time.sleep(3)

        # 重新查询
        print("\n--- 撤单后委托状态 ---")
        orders2 = client.trader.query_stock_orders(client.account)
        if orders2:
            print(f"{'#':<3} {'代码':<12} {'名称':<16} {'状态':<10} {'委托量':>12} {'已成交':>10} {'状态信息'}")
            print("-" * 90)
            for i, order in enumerate(orders2):
                code = getattr(order, 'stock_code', '') or getattr(order, 'm_strStockCode', '')
                name = Config.ETF_POOL.get(code, code)
                status_num = getattr(order, 'order_status', 0) or getattr(order, 'm_nOrderStatus', 0)
                status = status_map.get(status_num, f'未知({status_num})')
                volume = getattr(order, 'order_volume', 0) or getattr(order, 'm_nOrderVolume', 0)
                traded = getattr(order, 'traded_volume', 0) or getattr(order, 'm_nTradedVolume', 0)
                status_msg = getattr(order, 'status_msg', '') or getattr(order, 'm_strStatusMsg', '')
                print(f"{i+1:<3} {code:<12} {name:<16} {status:<10} {volume:>12,} {traded:>10,} {status_msg}")
        else:
            print("  ✅ 无委托记录 - 全部已撤销!")

        # 资金状态
        print("\n--- 撤单后账户资金 ---")
        asset = client.get_account_info()
        if asset:
            print(f"  总资产:   {asset['total_asset']:>16,.2f}")
            print(f"  可用资金: {asset['available_cash']:>16,.2f}")
            print(f"  冻结资金: {asset['frozen_cash']:>16,.2f}")
            print(f"  持仓市值: {asset['market_value']:>16,.2f}")

    except Exception as e:
        print(f"操作失败: {e}")
        import traceback
        traceback.print_exc()

    client.disconnect()

if __name__ == '__main__':
    main()
