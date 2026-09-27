"""
QMT 交易通道一键自检脚本
============================================================
用法：
    cd D:\策略\etf_trading_system
    D:\software\anaconda3\python.exe diagnose_qmt.py

作用：
    1. 连接 QMT（读取 .env 里的 QMT_PATH / QMT_ACCOUNT）
    2. 查询账户资金 & 持仓（任何时段都能验证"交易账号是否登录"）
    3. 交易时段内下 1 笔极小测试单（买 100 股沪深300ETF，市价）
    4. 确认成交 / 废单，给出明确结论

⚠️ 测试单必须在交易时段运行（9:30-11:30 / 13:00-15:00），
   否则市价单会被柜台拒绝。但前两步（连接+账户查询）任何时段都能跑，
   足以判断"交易账号是否已登录"——这正是废单最常见的根因。
"""
import time
from trading.qmt_client import QMTClient
from config.settings import Config

# 用流动性最好的沪深300ETF做测试单，100股够小，不影响实盘
TEST_CODE = '510300.SH'


def _header(title: str):
    print('\n' + '=' * 60)
    print(title)
    print('=' * 60)


def main():
    _header('QMT 交易通道自检')

    # ---------------------------------------------------------
    # 1. 连接
    # ---------------------------------------------------------
    print('\n[1] 连接 QMT')
    print(f'    路径 : {Config.QMT_PATH}')
    print(f'    账号 : {Config.QMT_ACCOUNT}')
    print(f'    模拟 : {Config.QMT_SIMULATE}')
    qmt = QMTClient()
    if not qmt.connect():
        print('    ❌ 连接失败！请确认 XtMiniQmt.exe 已打开且与 QMT_PATH 一致')
        return
    print('    ✅ 连接成功（行情+交易通道本地已建立）')

    # ---------------------------------------------------------
    # 2. 账户资金（任何时段都能查，用于判断交易账号是否登录）
    # ---------------------------------------------------------
    print('\n[2] 账户资金')
    acc = qmt.get_account_info()
    if not acc:
        print('    ❌ 账户查询失败！连接成功但 query_stock_asset 返回空')
        print('    → 根因：交易账号未在 QMT 客户端登录（行情连接 ≠ 交易连接）')
        print('    → 解决：打开 XtMiniQmt.exe，在客户端里登录交易账号 70049540')
        return
    print(f'    总资产  : {acc.get("total_asset", 0):,.2f}')
    print(f'    可用资金: {acc.get("available_cash", 0):,.2f}')
    print(f'    持仓市值: {acc.get("market_value", 0):,.2f}')
    if acc.get('available_cash', 0) <= 0:
        print('    ⚠️ 可用资金 <= 0，即使通道正常也无法买入')

    # ---------------------------------------------------------
    # 3. 持仓
    # ---------------------------------------------------------
    print('\n[3] 当前持仓')
    positions = qmt.get_positions()
    if positions:
        for p in positions:
            print(f'    {p["stock_code"]}: {p["volume"]}股 '
                  f'可用{p["available"]} 市值{p["market_value"]:.0f}')
    else:
        print('    (无持仓)')

    # ---------------------------------------------------------
    # 4. 交易时段检查（仅影响测试单）
    # ---------------------------------------------------------
    print('\n[4] 交易时段')
    if not qmt.is_trading_hours():
        print('    ⚠️ 当前非交易时段（9:30-11:30 / 13:00-15:00）')
        print('    前 3 步已证明"连接+账号登录"状态。')
        print('    测试单需在交易时段内重跑本脚本，才能验证柜台是否接收委托。')
        _header('诊断完成（时段外，已验证账号登录状态）')
        return
    print('    ✅ 处于交易时段，继续测试单')

    # ---------------------------------------------------------
    # 5. 测试下单（买 100 股）
    # ---------------------------------------------------------
    print('\n[5] 测试下单（买 100 股，验证柜台是否接收委托）')
    data = qmt.get_etf_data(TEST_CODE)
    if not data:
        print(f'    ❌ 无法获取 {TEST_CODE} 行情')
        return
    price = data['last_price']
    print(f'    {TEST_CODE} 当前价: {price:.3f}')
    order_id = qmt.buy_etf(TEST_CODE, price, 100)
    if not order_id:
        print('    ❌ 测试单提交失败（order_stock 返回空/被拒）')
        print('    → 连接+账号都正常却提交失败，通常是柜台通道未建立')
        print('    → 解决：在 QMT 客户端确认交易账号已登录且"交易"标签可见')
        return
    print(f'    ✅ 测试单已提交，序号: {order_id}')

    # 确认成交状态
    print('    等待成交确认...')
    confirm = qmt.confirm_order(int(order_id), timeout=10)
    status = confirm.get('status', '')
    traded = confirm.get('traded_volume', 0)
    print(f'    状态: {status}')

    if '废单' in status:
        print('    ❌ 废单（柜台未记录）！')
        print('    ┌─ 现象：订单能拿序号，但券商柜台查无此单')
        print('    ├─ 原因1：QMT 交易账号未在客户端登录（行情通道≠交易通道）')
        print('    ├─ 原因2：账号 70049540 不属于当前 QMT_PATH 对应的仿真环境')
        print('    └─ 解决：打开 XtMiniQmt.exe → 登录交易账号 → 手动下1笔单验证能成')
    elif traded > 0:
        print('    ✅ 测试单成交！交易通道完全正常，主策略可正常下单')
        # 立即清理测试单，避免留下实际持仓
        print('    清理测试持仓...')
        qmt.cancel_pending_orders(TEST_CODE)
        qmt.sell_etf(TEST_CODE, price, traded)
    else:
        print('    ⚠️ 未即时成交（可能挂单中或网络延迟），稍后可在 QMT 客户端查看')

    _header('诊断完成')


if __name__ == '__main__':
    main()
