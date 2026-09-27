"""查询QMT实际持仓和账户信息"""
import sys
import json

# 添加项目路径
sys.path.insert(0, r'D:\策略\etf_trading_system')

from trading.qmt_client import QMTClient
from config.settings import Config

def main():
    print("=" * 60)
    print("QMT 持仓查询")
    print(f"交易模式: {'模拟交易' if Config.QMT_SIMULATE else '实盘交易'}")
    print(f"QMT路径: {Config.QMT_PATH}")
    print(f"账号: {Config.QMT_ACCOUNT}")
    print("=" * 60)

    client = QMTClient()
    if not client.connect():
        print("❌ QMT连接失败，请确保QMT交易端已启动")
        return

    # 查询账户信息
    print("\n--- 账户信息 ---")
    account = client.get_account_info()
    if account:
        print(f"  总资产:     {account.get('total_asset', 0):>15,.2f} 元")
        print(f"  可用资金:   {account.get('available_cash', 0):>15,.2f} 元")
        print(f"  冻结资金:   {account.get('frozen_cash', 0):>15,.2f} 元")
        print(f"  持仓市值:   {account.get('market_value', 0):>15,.2f} 元")
        print(f"  浮动盈亏:   {account.get('pnl', 0):>15,.2f} 元")
    else:
        print("  (无法获取账户信息)")

    # 查询持仓
    print("\n--- 持仓明细 ---")
    positions = client.get_positions()
    if positions:
        print(f"{'代码':<12} {'名称':<16} {'持仓量':>12} {'可用':>12} {'成本价':>10} {'现价':>10} {'市值':>14} {'浮盈':>12} {'收益率':>8}")
        print("-" * 130)
        for pos in positions:
            code = pos['stock_code']
            name = Config.ETF_POOL.get(code, code)
            print(f"{code:<12} {name:<16} {pos['volume']:>12,} {pos['available']:>12,} {pos['cost_price']:>10.3f} {pos['market_price']:>10.3f} {pos['market_value']:>14,.2f} {pos['pnl']:>12,.2f} {pos['pnl_ratio']*100:>7.2f}%")

        total_mv = sum(p['market_value'] for p in positions)
        total_pnl = sum(p['pnl'] for p in positions)
        print("-" * 130)
        print(f"{'合计':<30} {'':>12} {'':>12} {'':>10} {'':>10} {total_mv:>14,.2f} {total_pnl:>12,.2f}")
    else:
        print("  (无持仓)")

    client.disconnect()
    print("\n查询完成。")


if __name__ == '__main__':
    main()
