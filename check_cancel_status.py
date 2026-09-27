"""撤单后状态复查 - 等待更长时间确认"""
import sys
import time
sys.path.insert(0, r'D:\策略\etf_trading_system')

from trading.qmt_client import QMTClient
from config.settings import Config

def main():
    print("=" * 60)
    print("QMT 撤单状态复查")
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

    # 连续查询3次，每次间隔5秒
    for round_num in range(1, 4):
        print(f"\n{'='*40}")
        print(f"第 {round_num} 次查询")
        print(f"{'='*40}")

        orders = client.trader.query_stock_orders(client.account)
        if orders:
            all_cancelled = True
            print(f"{'#':<3} {'代码':<12} {'名称':<16} {'状态':<10} {'委托量':>12} {'已成交':>10} {'状态信息'}")
            print("-" * 90)
            for i, order in enumerate(orders):
                code = getattr(order, 'stock_code', '') or getattr(order, 'm_strStockCode', '')
                name = Config.ETF_POOL.get(code, code)
                status_num = getattr(order, 'order_status', 0) or getattr(order, 'm_nOrderStatus', 0)
                status = status_map.get(status_num, f'未知({status_num})')
                volume = getattr(order, 'order_volume', 0) or getattr(order, 'm_nOrderVolume', 0)
                traded = getattr(order, 'traded_volume', 0) or getattr(order, 'm_nTradedVolume', 0)
                status_msg = getattr(order, 'status_msg', '') or getattr(order, 'm_strStatusMsg', '')
                print(f"{i+1:<3} {code:<12} {name:<16} {status:<10} {volume:>12,} {traded:>10,} {status_msg}")
                if status_num not in (54, 55, 58):  # 不是已撤/部撤/废单
                    all_cancelled = False
        else:
            print("  ✅ 无委托记录 - 全部已撤销!")
            all_cancelled = True

        # 查资金
        asset = client.get_account_info()
        if asset:
            print(f"\n  可用资金: {asset['available_cash']:>16,.2f}")
            print(f"  冻结资金: {asset['frozen_cash']:>16,.2f}")

        if all_cancelled and (not orders or asset and asset['frozen_cash'] < 100):
            print("\n✅ 撤单完成，资金已释放!")
            break

        if round_num < 3:
            print(f"\n等待5秒后再次查询...")
            time.sleep(5)

    # 最终持仓
    print("\n--- 当前持仓 ---")
    positions = client.get_positions()
    if positions:
        for pos in positions:
            code = pos['stock_code']
            name = Config.ETF_POOL.get(code, code)
            print(f"  {code} {name}: {pos['volume']:,}股 可用{pos['available']:,}股 成本{pos['cost_price']:.3f} 市值{pos['market_value']:,.2f}")
    else:
        print("  (无持仓)")

    client.disconnect()

if __name__ == '__main__':
    main()
