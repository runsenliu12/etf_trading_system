"""
收盘后汇总4个时刻决策JSON → Excel日报
"""

import json
from pathlib import Path
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from loguru import logger
from config.settings import Config

FONT_TITLE = Font(name='Arial', size=14, bold=True, color='1F4E79')
FONT_HEADER = Font(name='Arial', size=11, bold=True, color='FFFFFF')
FILL_HEADER = PatternFill('solid', fgColor='1F4E79')
FILL_MOMENT = PatternFill('solid', fgColor='D6E4F0')
FILL_BUY = PatternFill('solid', fgColor='E2EFDA')
FILL_SELL = PatternFill('solid', fgColor='FCE4D6')
FILL_EMPTY = PatternFill('solid', fgColor='F2F2F2')
# 盈亏着色：中国习惯 涨=红 跌=绿（赚了=红，亏了=绿）
FILL_PROFIT = PatternFill('solid', fgColor='FFC7CE')  # 盈利 浅红
FILL_LOSS = PatternFill('solid', fgColor='C6EFCE')    # 亏损 浅绿
FONT_PROFIT = Font(name='Arial', size=11, bold=True, color='C00000')  # 红
FONT_LOSS = Font(name='Arial', size=11, bold=True, color='008000')    # 绿
ALIGN_CENTER = Alignment(horizontal='center', vertical='center', wrap_text=True)
ALIGN_LEFT = Alignment(horizontal='left', vertical='center', wrap_text=True)
THIN_BORDER = Border(
    left=Side(style='thin'), right=Side(style='thin'),
    top=Side(style='thin'), bottom=Side(style='thin'))

# ETF 代码 → 名称兜底映射（处理 AI 在部分时刻漏填 etf_name 的情况）
# 键同时支持带后缀（518880.SH）与不带后缀（518880）两种写法
ETF_NAME_MAP = {
    '518880': '黄金ETF',
    '518880.SH': '黄金ETF',
    '518800': '黄金ETF',
    '518800.SH': '黄金ETF',
    '512800': '银行ETF',
    '512800.SH': '银行ETF',
    '515220': '煤炭ETF',
    '515220.SH': '煤炭ETF',
    '159516': '半导体设备ETF',
    '159516.SZ': '半导体设备ETF',
    '512480': '半导体ETF',
    '512480.SH': '半导体ETF',
    '588200': '科创芯片ETF',
    '588200.SH': '科创芯片ETF',
    '512690': '酒ETF',
    '512690.SH': '酒ETF',
    '159819': '人工智能ETF',
    '159819.SZ': '人工智能ETF',
    '510300': '沪深300ETF',
    '510300.SH': '沪深300ETF',
    '510500': '中证500ETF',
    '510500.SH': '中证500ETF',
    '512100': '中证1000ETF',
    '512100.SH': '中证1000ETF',
}


def _normalize_code(code: str) -> str:
    """统一代码格式：大写、去首尾空格"""
    return str(code).strip().upper() if code else ''


def resolve_etf_name(code: str, raw_name: str = '', dynamic_map: dict = None) -> str:
    """
    解析 ETF 名称，优先级：
    1. AI 返回的 raw_name（非空则直接用）
    2. 同一日其他时刻已收集的动态映射（处理同代码在不同时刻 name 不一致）
    3. 内置兜底映射（支持带/不带交易所后缀）
    4. 实在没有，返回“未知ETF(code)"
    """
    name = (raw_name or '').strip()
    if name:
        return name

    code = _normalize_code(code)
    if not code:
        return ''

    # 优先查权威表 ETF_POOL（持仓池，与系统交易标的一致，37只全覆盖）
    if code in Config.ETF_POOL:
        return Config.ETF_POOL[code]
    bare = code.split('.')[0]
    if bare in Config.ETF_POOL:
        return Config.ETF_POOL[bare]

    if dynamic_map and code in dynamic_map:
        return dynamic_map[code]
    if code in ETF_NAME_MAP:
        return ETF_NAME_MAP[code]

    # 去掉 .SH/.SZ/.BJ 后缀再查一次
    if dynamic_map and bare in dynamic_map:
        return dynamic_map[bare]
    if bare in ETF_NAME_MAP:
        return ETF_NAME_MAP[bare]

    return f'未知ETF({code})'


def build_dynamic_name_map(decisions: dict) -> dict:
    """扫描当天所有时刻决策，收集 AI 返回过的 etf_name，作为动态补全映射"""
    name_map = {}
    for label, data in decisions.items():
        dec = data.get('decision', {})
        for key in ('buy_orders', 'sell_orders', 'holdings_decision', 'sellable_positions'):
            for item in dec.get(key, []):
                code = _normalize_code(item.get('etf_code', ''))
                name = (item.get('etf_name', '') or '').strip()
                if code and name:
                    name_map[code] = name
                    # 同时记录去掉后缀的键
                    bare = code.split('.')[0]
                    name_map[bare] = name
        # 主力追踪里的 ranked_etfs
        for r in dec.get('main_force_tracking', {}).get('ranked_etfs', []):
            code = _normalize_code(r.get('etf_code', ''))
            name = (r.get('etf_name', '') or '').strip()
            if code and name:
                name_map[code] = name
                name_map[code.split('.')[0]] = name
    return name_map


def build_hold_days_map(decisions: dict) -> dict:
    """扫描当天所有时刻买入指令，收集模型建议持仓天数(max_hold_days)，做前向补齐映射。

    AI 经常在后续时刻漏填 max_hold_days，同一代码只要有一个时刻填了就沿用。
    键同时支持带后缀与不带后缀两种写法。
    """
    hold_map = {}
    for label, data in decisions.items():
        dec = data.get('decision', {})
        for item in dec.get('buy_orders', []):
            code = _normalize_code(item.get('etf_code', ''))
            hd = item.get('max_hold_days')
            if code and hd is not None:
                hold_map[code] = hd
                hold_map[code.split('.')[0]] = hd
    return hold_map


def set_cell(ws, row, col, value, font=None, fill=None, alignment=None):
    cell = ws.cell(row=row, column=col, value=value)
    if font: cell.font = font
    if fill: cell.fill = fill
    if alignment: cell.alignment = alignment
    cell.border = THIN_BORDER
    return cell


def set_headers(ws, row, headers, col_start=1):
    for i, h in enumerate(headers):
        set_cell(ws, row, col_start + i, h, FONT_HEADER, FILL_HEADER, ALIGN_CENTER)


def load_today_decisions(log_dir: Path) -> dict:
    """加载今天4个时刻的真实决策JSON（自动跳过 test 标记的盘前预演数据）"""
    today = datetime.now().strftime('%Y%m%d')
    decisions = {}
    for f in sorted(log_dir.glob(f'moment_*_{today}_*.json')):
        with open(f, 'r', encoding='utf-8') as fp:
            data = json.load(fp)
        # 跳过 --test 手动测试产生的预演数据，避免污染日报与公众号自动化
        if data.get('test'):
            continue
        moments = {
            'moment_1': '09:25 集合竞价',
            'moment_2': '10:30 上午确认',
            'moment_3': '12:50 午盘转折',
            'moment_4': '14:30 尾盘最终',
        }
        m = data.get('moment', '')
        label = moments.get(m, m)
        decisions[label] = data
    return decisions


def sheet_overview(wb, decisions, today_str):
    """Sheet 1: 今日概览"""
    name_map = build_dynamic_name_map(decisions)
    ws = wb.create_sheet('今日概览')
    ws.column_dimensions['A'].width = 16
    ws.column_dimensions['B'].width = 60

    set_cell(ws, 1, 1, f'ETF交易日报 - {today_str}', FONT_TITLE, alignment=ALIGN_LEFT)
    ws.merge_cells('A1:B1')

    row = 3
    set_headers(ws, row, ['项目', '内容'])

    moment_labels = ['09:25 集合竞价', '10:30 上午确认', '12:50 午盘转折', '14:30 尾盘最终']
    total_buy = total_sell = 0
    all_reminders = []
    mf_tops = []

    for label in moment_labels:
        if label not in decisions:
            continue
        dec = decisions[label]['decision']
        row += 1
        set_cell(ws, row, 1, label, FONT_HEADER, FILL_MOMENT, ALIGN_CENTER)
        set_cell(ws, row, 2, '', fill=FILL_MOMENT)

        # 市场判断
        ov = dec.get('market_overview') or dec.get('morning_verdict') or dec.get('today_final_verdict') or {}
        row += 1
        set_cell(ws, row, 1, '市场判断', alignment=ALIGN_CENTER)
        judgment = ov.get('today_judgment') or ov.get('market_type') or ''
        score = ''
        if isinstance(ov.get('tradable_score'), (int, float)):
            score = f' (可操作评分: {ov["tradable_score"]})'
        elif isinstance(ov.get('score'), (int, float)):
            score = f' (评分: {ov["score"]})'
        set_cell(ws, row, 2, str(judgment)[:100] + score, alignment=ALIGN_LEFT)

        # 主力追踪
        mf = dec.get('main_force_tracking')
        if mf:
            row += 1
            set_cell(ws, row, 1, '主力方向', alignment=ALIGN_CENTER)
            top = mf.get('top_etf_name', mf.get('top_etf', ''))
            evidence = mf.get('main_force_evidence', '')[:80]
            set_cell(ws, row, 2, f'#{top} | {evidence}', alignment=ALIGN_LEFT)
            mf_tops.append(top)

            ranked = mf.get('ranked_etfs', [])
            for r in ranked:
                code = r.get('etf_code', '')
                row += 1
                set_cell(ws, row, 1, f'  主力排名{r.get("rank","")}', alignment=ALIGN_CENTER)
                set_cell(ws, row, 2,
                    f'{code} {resolve_etf_name(code, r.get("etf_name", ""), name_map)} '
                    f'评分:{r.get("main_force_score","")} | {r.get("reason","")[:60]}',
                    alignment=ALIGN_LEFT)

        # 买入
        buys = dec.get('buy_orders', [])
        opps = dec.get('new_positions_afternoon', {}).get('opportunities', [])
        tail = dec.get('tail_new_position', {})
        over = dec.get('overnight_position_plan', {}).get('hold_etfs', [])
        all_buys = buys + opps
        if tail.get('has_opportunity'):
            all_buys.append(tail)
        for b in all_buys:
            code = b.get('etf_code', '')
            if not code: continue
            row += 1
            set_cell(ws, row, 1, '📈 买入', fill=FILL_BUY, alignment=ALIGN_CENTER)
            set_cell(ws, row, 2,
                f'{code} {resolve_etf_name(code, b.get("etf_name", ""), name_map)} | '
                f'{b.get("action","")} | 仓位:{b.get("position_ratio","")} | 止损:{b.get("stop_loss","")} | {b.get("reason","")[:80]}',
                fill=FILL_BUY, alignment=ALIGN_LEFT)
            total_buy += 1

        # 卖出
        sells = dec.get('sell_orders', []) + dec.get('holdings_decision', []) + dec.get('sellable_positions', [])
        for s in sells:
            code = s.get('etf_code', '')
            if not code: continue
            act = s.get('action', '')
            if '持有' in str(act) or '不动' in str(act): continue
            row += 1
            set_cell(ws, row, 1, '📉 卖出', fill=FILL_SELL, alignment=ALIGN_CENTER)
            set_cell(ws, row, 2,
                f'{code} {resolve_etf_name(code, s.get("etf_name", ""), name_map)} | '
                f'{act} | 比例:{s.get("sell_ratio","")} | {s.get("reason","")[:80]}',
                fill=FILL_SELL, alignment=ALIGN_LEFT)
            total_sell += 1

        # 过夜
        for o in over:
            code = o.get('etf_code', '')
            if not code: continue
            row += 1
            set_cell(ws, row, 1, '🌙 过夜', alignment=ALIGN_CENTER)
            set_cell(ws, row, 2,
                f'{code} {resolve_etf_name(code, o.get("etf_name", ""), name_map)} | '
                f'仓位:{o.get("hold_ratio","")} | {o.get("reason","")[:80]}',
                alignment=ALIGN_LEFT)

        # 关键提醒
        key = dec.get('key_reminder', '')
        if key:
            all_reminders.append(f'[{label}] {key}')

        row += 1

    # 汇总
    row += 2
    set_cell(ws, row, 1, '📊 今日统计', FONT_TITLE, alignment=ALIGN_LEFT)
    ws.merge_cells(f'A{row}:B{row}')
    row += 1
    set_cell(ws, row, 1, '总买入指令', alignment=ALIGN_CENTER)
    set_cell(ws, row, 2, f'{total_buy} 笔', alignment=ALIGN_LEFT)
    row += 1
    set_cell(ws, row, 1, '总卖出指令', alignment=ALIGN_CENTER)
    set_cell(ws, row, 2, f'{total_sell} 笔', alignment=ALIGN_LEFT)
    row += 1
    set_cell(ws, row, 1, '主力集中方向', alignment=ALIGN_CENTER)
    set_cell(ws, row, 2, ' → '.join(set(mf_tops)), alignment=ALIGN_LEFT)
    row += 2
    for r in all_reminders:
        row += 1
        set_cell(ws, row, 1, '关键提醒', alignment=ALIGN_CENTER)
        set_cell(ws, row, 2, r, alignment=ALIGN_LEFT)


def sheet_orders(wb, decisions):
    """Sheet 2: 交易指令汇总——所有买卖指令一览表"""
    name_map = build_dynamic_name_map(decisions)
    hold_map = build_hold_days_map(decisions)
    ws = wb.create_sheet('交易指令汇总')
    headers = ['时刻', '类型', 'ETF代码', '名称', '操作', '下单方式', '价格', '仓位/比例', '止损', '止盈', '建议持仓天数', '理由']
    widths = [16, 8, 14, 14, 18, 10, 10, 10, 10, 10, 12, 46]
    for i, w in enumerate(widths):
        ws.column_dimensions[get_column_letter(i + 1)].width = w
    set_headers(ws, 1, headers)

    moment_labels = ['09:25 集合竞价', '10:30 上午确认', '12:50 午盘转折', '14:30 尾盘最终']
    row = 2
    for label in moment_labels:
        if label not in decisions:
            continue
        dec = decisions[label]['decision']

        # 卖出
        sells = dec.get('sell_orders', []) + dec.get('holdings_decision', []) + dec.get('sellable_positions', [])
        for s in sells:
            code = s.get('etf_code', '')
            if not code: continue
            act = str(s.get('action', ''))
            if '持有' in act or '不动' in act: continue
            set_cell(ws, row, 1, label, alignment=ALIGN_CENTER)
            set_cell(ws, row, 2, '卖出', fill=FILL_SELL, alignment=ALIGN_CENTER)
            set_cell(ws, row, 3, code, alignment=ALIGN_CENTER)
            set_cell(ws, row, 4, resolve_etf_name(code, s.get('etf_name', ''), name_map), alignment=ALIGN_CENTER)
            set_cell(ws, row, 5, act[:15], alignment=ALIGN_LEFT)
            set_cell(ws, row, 6, s.get('order_type', ''), alignment=ALIGN_CENTER)
            set_cell(ws, row, 7, s.get('sell_price', ''), alignment=ALIGN_CENTER)
            set_cell(ws, row, 8, s.get('sell_ratio', ''), alignment=ALIGN_CENTER)
            set_cell(ws, row, 9, '', alignment=ALIGN_CENTER)
            set_cell(ws, row, 10, '', alignment=ALIGN_CENTER)
            hd = hold_map.get(_normalize_code(code), '')
            set_cell(ws, row, 11, f'{hd}天' if hd != '' else '', alignment=ALIGN_CENTER)
            set_cell(ws, row, 12, str(s.get('reason', ''))[:60], alignment=ALIGN_LEFT)
            row += 1

        # 买入
        buys = dec.get('buy_orders', [])
        opps = dec.get('new_positions_afternoon', {}).get('opportunities', [])
        tail = dec.get('tail_new_position', {})
        all_buys = buys + opps
        if tail.get('has_opportunity'):
            all_buys.append(tail)
        for b in all_buys:
            code = b.get('etf_code', '')
            if not code: continue
            set_cell(ws, row, 1, label, alignment=ALIGN_CENTER)
            set_cell(ws, row, 2, '买入', fill=FILL_BUY, alignment=ALIGN_CENTER)
            set_cell(ws, row, 3, code, alignment=ALIGN_CENTER)
            set_cell(ws, row, 4, resolve_etf_name(code, b.get('etf_name', ''), name_map), alignment=ALIGN_CENTER)
            act = str(b.get('action', b.get('buy_timing', '')))
            set_cell(ws, row, 5, act[:15], alignment=ALIGN_LEFT)
            set_cell(ws, row, 6, b.get('order_type', ''), alignment=ALIGN_CENTER)
            set_cell(ws, row, 7, b.get('buy_price', b.get('entry_price', '')), alignment=ALIGN_CENTER)
            set_cell(ws, row, 8, b.get('position_ratio', ''), alignment=ALIGN_CENTER)
            set_cell(ws, row, 9, b.get('stop_loss', ''), alignment=ALIGN_CENTER)
            set_cell(ws, row, 10, b.get('take_profit', ''), alignment=ALIGN_CENTER)
            hd = b.get('max_hold_days', hold_map.get(_normalize_code(code), ''))
            set_cell(ws, row, 11, f'{hd}天' if hd != '' else '', alignment=ALIGN_CENTER)
            set_cell(ws, row, 12, str(b.get('reason', ''))[:60], alignment=ALIGN_LEFT)
            row += 1

    ws.auto_filter.ref = f'A1:{get_column_letter(12)}{row - 1}'


def sheet_research(wb, today_str):
    """Sheet 3: 研报搜索结果（09:00 + 12:45 两次搜索）"""
    ws = wb.create_sheet('研报搜索结果')
    ws.column_dimensions['A'].width = 12
    ws.column_dimensions['B'].width = 90

    set_cell(ws, 1, 1, f'研报搜索结果 - {today_str}', FONT_TITLE, alignment=ALIGN_LEFT)
    ws.merge_cells('A1:B1')

    research_dir = Path('logs/research')
    row = 3
    for label, title in [('morning', '09:00 盘前搜索'), ('afternoon', '12:45 午后搜索')]:
        set_cell(ws, row, 1, title, FONT_HEADER, FILL_MOMENT, ALIGN_CENTER)
        set_cell(ws, row, 2, '', fill=FILL_MOMENT)
        row += 1

        # 尝试加载当天研报文件
        src = research_dir / f'research_{label}_{today_str}.md'
        if src.exists():
            with open(src, 'r', encoding='utf-8') as f:
                content = f.read()
            set_cell(ws, row, 1, '搜索结果', alignment=ALIGN_CENTER)
            set_cell(ws, row, 2, content[:8000], alignment=ALIGN_LEFT, font=Font(name='Arial', size=10))
            ws.row_dimensions[row].height = 300
            row += 1
        else:
            set_cell(ws, row, 1, '状态', alignment=ALIGN_CENTER)
            set_cell(ws, row, 2, '未找到搜索结果文件（可能搜索未执行或失败）', alignment=ALIGN_LEFT)
            row += 1
        row += 1


def sheet_pnl_summary(wb, today_str):
    """Sheet: 账户盈亏汇总——连接QMT拉取实时持仓盈亏，按盈亏排序（涨红跌绿）

    15:30 自动化运行时 QMT 处于连接状态，可拿到真实盈亏；
    若 QMT 未启动/未连接，则优雅降级，标注“无法获取实时盈亏”，不影响其他 sheet 生成。
    """
    ws = wb.create_sheet('账户盈亏汇总')
    for col, w in zip('ABCDEFGHI', [14, 16, 12, 12, 10, 10, 14, 14, 10]):
        ws.column_dimensions[col].width = w

    set_cell(ws, 1, 1, f'账户盈亏汇总 - {today_str}', FONT_TITLE, alignment=ALIGN_LEFT)
    ws.merge_cells('A1:I1')

    # 延迟导入，避免无 QMT 环境时 import 失败
    try:
        from trading.qmt_client import QMTClient
    except Exception as e:
        set_cell(ws, 3, 1, '⚠️ 无法加载 QMT 客户端（可能未安装 xtquant）', alignment=ALIGN_LEFT)
        set_cell(ws, 4, 1, f'错误: {e}', alignment=ALIGN_LEFT)
        return

    client = QMTClient()
    if not client.connect():
        set_cell(ws, 3, 1, '⚠️ QMT 未连接，无法获取实时盈亏', alignment=ALIGN_LEFT)
        set_cell(ws, 4, 1, '请确认 QMT 交易端已启动并登录（本汇总需在交易时段由 15:30 自动化生成）', alignment=ALIGN_LEFT)
        return

    account = client.get_account_info()
    positions = client.get_positions()
    client.disconnect()

    name_map = build_dynamic_name_map(load_today_decisions(Path('logs/decisions')))
    # 合并内置名称兜底
    def _name(code):
        return resolve_etf_name(code, '', name_map)

    # 以持仓汇总为准计算浮动盈亏（QMT模拟盘 account.pnl 不可靠，常为0）
    total_pnl = sum(float(p.get('pnl', 0)) for p in positions)

    row = 3
    # 账户概览
    set_cell(ws, row, 1, '账户概览', FONT_HEADER, FILL_HEADER, ALIGN_CENTER)
    ws.merge_cells(f'A{row}:I{row}')
    row += 1
    summary = [
        ('总资产(元)', account.get('total_asset', 0)),
        ('可用资金(元)', account.get('available_cash', 0)),
        ('持仓市值(元)', account.get('market_value', 0)),
        ('浮动盈亏(元)', total_pnl),
    ]
    for label, val in summary:
        set_cell(ws, row, 1, label, alignment=ALIGN_CENTER)
        is_pnl = '盈亏' in label
        if is_pnl:
            set_cell(ws, row, 2, round(float(val), 2),
                     font=FONT_PROFIT if float(val) >= 0 else FONT_LOSS,
                     fill=FILL_PROFIT if float(val) >= 0 else FILL_LOSS,
                     alignment=ALIGN_LEFT)
        else:
            set_cell(ws, row, 2, round(float(val), 2), alignment=ALIGN_LEFT)
        row += 1

    row += 1
    # 持仓盈亏明细表头
    set_cell(ws, row, 1, '持仓盈亏明细（按浮动盈亏排序，涨红跌绿）', FONT_HEADER, FILL_HEADER, ALIGN_CENTER)
    ws.merge_cells(f'A{row}:I{row}')
    row += 1
    set_headers(ws, row, ['代码', '名称', '持仓量', '可用', '成本价', '现价', '市值(元)', '浮动盈亏(元)', '收益率'])
    row += 1

    if not positions:
        set_cell(ws, row, 1, '（当前无持仓）', alignment=ALIGN_LEFT)
        return

    # 按浮动盈亏降序排序
    positions_sorted = sorted(positions, key=lambda p: p.get('pnl', 0), reverse=True)
    total_pnl = 0.0  # 重置，供下方「合计」行累计（概览里的浮动盈亏已用持仓汇总值）
    for pos in positions_sorted:
        code = pos['stock_code']
        pnl = float(pos.get('pnl', 0))
        ratio = float(pos.get('pnl_ratio', 0)) * 100
        total_pnl += pnl
        set_cell(ws, row, 1, code, alignment=ALIGN_CENTER)
        set_cell(ws, row, 2, _name(code), alignment=ALIGN_CENTER)
        set_cell(ws, row, 3, pos['volume'], alignment=ALIGN_CENTER)
        set_cell(ws, row, 4, pos.get('available', ''), alignment=ALIGN_CENTER)
        set_cell(ws, row, 5, round(float(pos.get('cost_price', 0)), 3), alignment=ALIGN_CENTER)
        set_cell(ws, row, 6, round(float(pos.get('market_price', 0)), 3), alignment=ALIGN_CENTER)
        set_cell(ws, row, 7, round(float(pos.get('market_value', 0)), 2), alignment=ALIGN_CENTER)
        set_cell(ws, row, 8, round(pnl, 2),
                 font=FONT_PROFIT if pnl >= 0 else FONT_LOSS,
                 fill=FILL_PROFIT if pnl >= 0 else FILL_LOSS,
                 alignment=ALIGN_CENTER)
        set_cell(ws, row, 9, f'{ratio:+.2f}%',
                 font=FONT_PROFIT if pnl >= 0 else FONT_LOSS,
                 alignment=ALIGN_CENTER)
        row += 1

    # 合计行
    set_cell(ws, row, 1, '合计', FONT_HEADER, FILL_HEADER, ALIGN_CENTER)
    set_cell(ws, row, 2, '', fill=FILL_HEADER)
    set_cell(ws, row, 3, '', fill=FILL_HEADER)
    set_cell(ws, row, 4, '', fill=FILL_HEADER)
    set_cell(ws, row, 5, '', fill=FILL_HEADER)
    set_cell(ws, row, 6, '', fill=FILL_HEADER)
    set_cell(ws, row, 7, '', fill=FILL_HEADER)
    set_cell(ws, row, 8, round(total_pnl, 2),
             font=FONT_PROFIT if total_pnl >= 0 else FONT_LOSS,
             fill=FILL_PROFIT if total_pnl >= 0 else FILL_LOSS,
             alignment=ALIGN_CENTER)
    set_cell(ws, row, 9, '', fill=FILL_HEADER)


def generate_daily_report(log_dir: str = "logs/decisions", output_dir: str = "logs/reports"):
    """生成当日 Excel 日报"""
    log_path = Path(log_dir)
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    today_str = datetime.now().strftime('%Y-%m-%d')
    decisions = load_today_decisions(log_path)

    if not decisions:
        logger.warning("今日无决策记录")
        return None

    wb = Workbook()
    wb.remove(wb.active)  # 删默认sheet

    sheet_overview(wb, decisions, today_str)
    sheet_pnl_summary(wb, today_str)
    sheet_orders(wb, decisions)
    sheet_research(wb, today_str)

    filename = out_path / f'日报_{datetime.now().strftime("%Y%m%d")}.xlsx'
    wb.save(str(filename))
    logger.info(f'日报已生成: {filename} ({len(decisions)}个时刻)')
    return str(filename)


if __name__ == '__main__':
    generate_daily_report()
