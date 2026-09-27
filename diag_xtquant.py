"""诊断 xtquant API - 找到正确的撤单方法和订单属性"""
import sys
sys.path.insert(0, r'D:\策略\etf_trading_system')

from trading.qmt_client import QMTClient

def main():
    client = QMTClient()
    if not client.connect():
        print("❌ QMT连接失败")
        return

    # 1. 查 trader 对象所有方法
    print("=" * 60)
    print("XtQuantTrader 方法列表 (含cancel相关):")
    print("=" * 60)
    methods = [m for m in dir(client.trader) if not m.startswith('_')]
    for m in methods:
        if 'cancel' in m.lower() or 'order' in m.lower() or 'cancel' in m.lower():
            print(f"  {m}")

    print("\n--- 全部方法 ---")
    for m in methods:
        print(f"  {m}")

    # 2. 查 order 对象所有属性
    print("\n" + "=" * 60)
    print("Order 对象属性:")
    print("=" * 60)
    orders = client.trader.query_stock_orders(client.account)
    if orders:
        order = orders[0]
        attrs = [a for a in dir(order) if not a.startswith('_')]
        for a in attrs:
            try:
                val = getattr(order, a)
                if not callable(val):
                    print(f"  {a} = {val}")
            except:
                print(f"  {a} = <error>")

    client.disconnect()

if __name__ == '__main__':
    main()
