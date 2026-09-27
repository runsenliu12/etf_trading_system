"""查询QMT今日委托记录 - 修正属性名"""
import sys
sys.path.insert(0, r'D:\策略\etf_trading_system')

from trading.qmt_client import QMTClient
from config.settings import Config

def main():
    print("=" * 60)
    print("QMT 今日委托查询")
    print("=" * 60)

    client = QMTClient()
    if not client.connect():
        print("❌ QMT连接失败")
        return

    try:
        orders = client.trader.query_stock_orders(client.account)
        if orders:
            print(f"\n共 {len(orders)} 条委托记录：\n")

            status_map = {
                48: '未知', 49: '待报', 50: '待报', 51: '已报',
                52: '已报待撤', 53: '部成待撤', 54: '部撤', 55: '已撤',
                56: '部分成交', 57: '全部成交', 58: '废单',
            }

            print(f"{'序号':<4} {'代码':<12} {'名称':<16} {'方向':<6} {'价格':>8} {'委托量':>12} {'已成交':>10} {'状态':<10} {'状态信息'}")
            print("-" * 100)
            for i, order in enumerate(orders):
                code = getattr(order, 'stock_code', '') or getattr(order, 'm_strStockCode', '')
                name = Config.ETF_POOL.get(code, code)
                order_type = getattr(order, 'order_type', 0) or getattr(order, 'm_nOrderType', 0)
                direction = '买入' if order_type == 23 else ('卖出' if order_type == 24 else str(order_type))
                price = getattr(order, 'price', 0) or getattr(order, 'm_dPrice', 0)
                volume = getattr(order, 'order_volume', 0) or getattr(order, 'm_nOrderVolume', 0)
                traded = getattr(order, 'traded_volume', 0) or getattr(order, 'm_nTradedVolume', 0)
                status_num = getattr(order, 'order_status', 0) or getattr(order, 'm_nOrderStatus', 0)
                status = status_map.get(status_num, f'未知({status_num})')
                status_msg = getattr(order, 'status_msg', '') or getattr(order, 'm_strStatusMsg', '')
                order_id = getattr(order, 'order_id', '') or getattr(order, 'm_nOrderID', '')
                
                print(f"{i+1:<4} {code:<12} {name:<16} {direction:<6} {price:>8.3f} {volume:>12,} {traded:>10,} {status:<10} {status_msg}")
            
            print("-" * 100)
        else:
            print("\n今日无委托记录")
    except Exception as e:
        print(f"查询委托失败: {e}")
        import traceback
        traceback.print_exc()

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
