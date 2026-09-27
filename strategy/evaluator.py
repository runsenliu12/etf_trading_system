"""
策略净值 / 收益评估模块
=======================
从 logs/decisions/*.json 的真实成交记录（execution_result 中 executed=True 的订单）
重建组合，逐日按收盘价标记（mark-to-market），输出：

  logs/eval/equity.csv     每日净值（现金+持仓市值）、收益率、回撤、基准对比
  logs/eval/trades.csv     实际成交明细（FIFO 已实现盈亏）
  logs/eval/summary.json   累计收益/年化/最大回撤/夏普/胜率/基准对比 等指标
  logs/eval/index.html     可视化仪表盘（ECharts）

价格来源（--mode）：
  auto  ：优先 QMT 实时历史 → 失败用缓存 prices_cache.csv → 再失败用 demo 合成（明确标注）
  qmt   ：必须连上 QMT
  cache ：读 logs/eval/prices_cache.csv（列: date,code,close）
  demo  ：合成确定性随机游走（仅用于离线验证管线，结果无实盘意义）

真实成交判定：仅取 execution_result 中 executed==True 且 volume>0 的订单；
废单 / 未成交 / --test 演练日志一律排除。

运行：
  python strategy/evaluator.py                 # 默认 auto，评估全部历史
  python strategy/evaluator.py --days 30       # 仅近30天
  python strategy/evaluator.py --mode demo     # 强制 demo（离线验证）
  python strategy/evaluator.py --out logs/eval # 自定义输出目录
"""
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# 项目根目录加入 sys.path，保证 config / trading 可被 import
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loguru import logger

# 基准 ETF（沪深300ETF，不在交易池内，仅作对比）
BENCHMARK_CODE = '510300.SH'
BENCHMARK_NAME = '沪深300ETF'

EVAL_DIR = ROOT / 'logs' / 'eval'


# ===========================================================================
# 1. 从决策日志收集真实成交
# ===========================================================================
def collect_trades_from_logs(days: int = None, logs_dir: Path = None) -> list:
    """扫描 logs/decisions/*.json，提取真实成交（executed 且 volume>0，排除 --test）。"""
    logs_dir = logs_dir or (ROOT / 'logs' / 'decisions')
    if not logs_dir.exists():
        logger.warning(f"决策日志目录不存在: {logs_dir}")
        return []

    trades = []
    files = sorted(logs_dir.glob('*.json'))
    cutoff = None
    if days:
        cutoff = (datetime.now() - timedelta(days=days)).date()

    for f in files:
        try:
            data = json.loads(f.read_text(encoding='utf-8'))
        except Exception as e:
            logger.debug(f"跳过无法解析的日志 {f.name}: {e}")
            continue

        if data.get('test'):
            continue  # 演练数据不计入真实绩效

        ts_raw = data.get('timestamp', '')
        try:
            ts = datetime.fromisoformat(ts_raw)
        except Exception:
            continue
        if cutoff and ts.date() < cutoff:
            continue

        moment = data.get('moment', '')
        res = data.get('execution_result', {})
        for side_key, side_label in (('buy_orders', 'buy'), ('sell_orders', 'sell')):
            for o in res.get(side_key, []) or []:
                if not o.get('executed'):
                    continue
                vol = o.get('volume', 0)
                price = o.get('price', 0)
                if not vol or vol <= 0 or not price:
                    continue
                trades.append({
                    'datetime': ts,
                    'date': ts.date(),
                    'moment': moment,
                    'code': o.get('etf_code', ''),
                    'name': o.get('etf_name', o.get('etf_code', '')),
                    'side': side_label,
                    'price': float(price),
                    'volume': int(vol),
                    'amount': float(price) * int(vol),
                    'order_type': o.get('order_type', ''),
                    'status': o.get('status', ''),
                })

    trades.sort(key=lambda x: x['datetime'])
    logger.info(f"从决策日志收集到 {len(trades)} 笔真实成交")
    return trades


# ===========================================================================
# 2. 价格来源
# ===========================================================================
def _load_prices_qmt(codes: set, start: str, end: str) -> dict:
    """连 QMT 拉取每个代码的日线收盘价。失败抛异常由调用方降级。"""
    from trading.qmt_client import QMTClient
    qmt = QMTClient()
    if not qmt.connect():
        raise RuntimeError("QMT 连接失败")
    prices = {}
    for code in codes:
        hist = qmt.get_etf_history(code, days=250)
        if hist:
            ser = pd.Series(
                {h['date']: h['close'] for h in hist}
            ).sort_index()
            # 裁剪到区间
            ser = ser[(ser.index >= start) & (ser.index <= end)]
            if len(ser):
                prices[code] = ser
    # 基准
    bhist = qmt.get_etf_history(BENCHMARK_CODE, days=250)
    if bhist:
        bs = pd.Series({h['date']: h['close'] for h in bhist}).sort_index()
        bs = bs[(bs.index >= start) & (bs.index <= end)]
        if len(bs):
            prices['__benchmark__'] = bs
    qmt.disconnect()
    return prices


def _load_prices_cache(cache_path: Path) -> dict:
    if not cache_path.exists():
        raise RuntimeError(f"价格缓存不存在: {cache_path}")
    df = pd.read_csv(cache_path)
    df['date'] = df['date'].astype(str)
    prices = {}
    for code, grp in df.groupby('code'):
        ser = pd.Series(grp['close'].values, index=grp['date'].values).sort_index()
        prices[code] = ser
    return prices


def _load_prices_demo(codes: set, dates: list, trades: list) -> dict:
    """合成确定性随机游走（仅离线验证管线用，结果无实盘意义）。"""
    import numpy as np
    rng_master = np.random.default_rng(20260727)
    prices = {}
    # 每个代码起始价取首笔成交价，否则给默认值
    first_price = {}
    for t in trades:
        first_price.setdefault(t['code'], t['price'])
    for code in codes:
        seed = abs(hash(code)) % 100000
        rng = np.random.default_rng(seed)
        start_p = float(first_price.get(code, rng.uniform(0.8, 5.0)))
        rets = rng.normal(0.0005, 0.015, size=len(dates))
        closes = start_p * np.cumprod(1 + rets)
        prices[code] = pd.Series(closes, index=dates)
    # 基准
    rng = np.random.default_rng(510300)
    bstart = 4.0
    rets = rng.normal(0.0003, 0.012, size=len(dates))
    prices['__benchmark__'] = pd.Series(bstart * np.cumprod(1 + rets), index=dates)
    return prices


def load_prices(trades: list, mode: str, start: str, end: str,
                cache_path: Path = None) -> tuple:
    """返回 (prices_dict, actual_mode)。prices_dict 含每个 code 的 Series + '__benchmark__'。"""
    codes = {t['code'] for t in trades}
    codes.add(BENCHMARK_CODE)
    dates = list(pd.bdate_range(start, end).astype(str))

    if mode == 'qmt':
        return _load_prices_qmt(codes, start, end), 'qmt'
    if mode == 'cache':
        p = _load_prices_cache(cache_path or (EVAL_DIR / 'prices_cache.csv'))
        return p, 'cache'
    if mode == 'demo':
        return _load_prices_demo(codes, dates, trades), 'demo'

    # auto：依次尝试
    try:
        return _load_prices_qmt(codes, start, end), 'qmt'
    except Exception as e:
        logger.warning(f"auto: QMT 失败({e})，尝试缓存...")
    try:
        return _load_prices_cache(cache_path or (EVAL_DIR / 'prices_cache.csv')), 'cache'
    except Exception as e:
        logger.warning(f"auto: 缓存失败({e})，降级 demo 合成")
    return _load_prices_demo(codes, dates, trades), 'demo'


# ===========================================================================
# 3. 净值重建
# ===========================================================================
def build_equity(trades: list, prices: dict, initial_capital: float,
                 start: str, end: str) -> pd.DataFrame:
    dates = list(pd.bdate_range(start, end).astype(str))
    # 前向填充每个代码收盘价
    ff = {}  # code -> 最近已知收盘价
    bench_ff = None

    cash = initial_capital
    positions = {}      # code -> volume
    lots = {}           # code -> list[(volume, price)] FIFO 买盘
    realized = 0.0
    rounds = {'wins': 0, 'losses': 0, 'total': 0}

    rows = []
    # 按日期归组交易
    trades_by_date = {}
    for t in trades:
        trades_by_date.setdefault(str(t['date']), []).append(t)

    for d in dates:
        # 当日交易
        for t in trades_by_date.get(d, []):
            code = t['code']
            if t['side'] == 'buy':
                cash -= t['amount']
                positions[code] = positions.get(code, 0) + t['volume']
                lots.setdefault(code, []).append((t['volume'], t['price']))
            else:  # sell
                cash += t['amount']
                remaining = t['volume']
                while remaining > 0 and lots.get(code):
                    lv, lp = lots[code][0]
                    take = min(lv, remaining)
                    realized += (t['price'] - lp) * take
                    rounds['total'] += 1
                    if t['price'] > lp:
                        rounds['wins'] += 1
                    else:
                        rounds['losses'] += 1
                    lv -= take
                    remaining -= take
                    if lv <= 0:
                        lots[code].pop(0)
                    else:
                        lots[code][0] = (lv, lp)
                positions[code] = max(0, positions.get(code, 0) - t['volume'])

        # 更新前向填充价
        for code in set(list(prices.keys()) + list(positions.keys())):
            if code == '__benchmark__':
                continue
            if code in prices and d in prices[code].index:
                ff[code] = float(prices[code][d])
        if '__benchmark__' in prices and d in prices['__benchmark__'].index:
            bench_ff = float(prices['__benchmark__'][d])

        # 标记组合
        pos_value = 0.0
        for code, vol in positions.items():
            if vol > 0 and code in ff and ff[code]:
                pos_value += vol * ff[code]

        total = cash + pos_value

        # 基准累计（相对首日）
        bench_close = bench_ff
        rows.append({
            'date': d,
            'cash': round(cash, 2),
            'position_value': round(pos_value, 2),
            'total_asset': round(total, 2),
            'benchmark_close': bench_close,
            '_realized': realized,
            '_rounds': rounds['total'],
            '_wins': rounds['wins'],
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # 收益率 / 回撤
    df['daily_return'] = df['total_asset'].pct_change().fillna(0.0)
    base = df['total_asset'].iloc[0]
    df['cum_return'] = df['total_asset'] / base - 1.0
    if df['benchmark_close'].notna().any():
        b0 = df['benchmark_close'].dropna().iloc[0]
        df['benchmark_cum_return'] = df['benchmark_close'] / b0 - 1.0
    else:
        df['benchmark_cum_return'] = 0.0
    # 回撤
    running_max = df['total_asset'].cummax()
    df['drawdown'] = df['total_asset'] / running_max - 1.0
    return df


# ===========================================================================
# 4. 指标
# ===========================================================================
def compute_metrics(equity: pd.DataFrame, trades: list, mode: str) -> dict:
    if equity.empty:
        return {'mode': mode, 'error': '无净值数据'}

    total = equity['total_asset']
    rets = equity['daily_return']
    n = len(equity)
    cum = total.iloc[-1] / total.iloc[0] - 1 if total.iloc[0] else 0
    ann = (1 + cum) ** (252.0 / max(n - 1, 1)) - 1 if n > 1 else 0
    max_dd = equity['drawdown'].min()
    sharpe = (rets.mean() / rets.std() * (252 ** 0.5)) if rets.std() and rets.std() > 0 else 0.0
    # 胜率
    wins = int(equity['_wins'].iloc[-1]) if '_wins' in equity else 0
    rounds = int(equity['_rounds'].iloc[-1]) if '_rounds' in equity else 0
    win_rate = (wins / rounds) if rounds else 0.0
    bench_cum = equity['benchmark_cum_return'].iloc[-1] if 'benchmark_cum_return' in equity else 0.0

    return {
        'mode': mode,
        'eval_start': equity['date'].iloc[0],
        'eval_end': equity['date'].iloc[-1],
        'trading_days': n,
        'initial_capital': round(float(total.iloc[0]), 2),
        'final_asset': round(float(total.iloc[-1]), 2),
        'cum_return': round(float(cum), 4),
        'annualized_return': round(float(ann), 4),
        'max_drawdown': round(float(max_dd), 4),
        'sharpe': round(float(sharpe), 3),
        'win_rate': round(float(win_rate), 4),
        'rounds': rounds,
        'wins': wins,
        'realized_pnl': round(float(equity['_realized'].iloc[-1]), 2) if '_realized' in equity else 0.0,
        'n_buy_orders': sum(1 for t in trades if t['side'] == 'buy'),
        'n_sell_orders': sum(1 for t in trades if t['side'] == 'sell'),
        'benchmark_cum_return': round(float(bench_cum), 4),
        'excess_return': round(float(cum - bench_cum), 4),
    }


# ===========================================================================
# 5. 导出 & 仪表盘
# ===========================================================================
def export_results(equity: pd.DataFrame, trades: list, summary: dict,
                   out_dir: Path, mode: str):
    out_dir.mkdir(parents=True, exist_ok=True)

    # equity.csv
    eq_out = equity.drop(columns=[c for c in ['_realized', '_rounds', '_wins'] if c in equity.columns])
    eq_out.to_csv(out_dir / 'equity.csv', index=False, encoding='utf-8-sig')

    # trades.csv
    tdf = pd.DataFrame([{
        'datetime': t['datetime'].strftime('%Y-%m-%d %H:%M:%S'),
        'moment': t['moment'],
        'code': t['code'],
        'name': t['name'],
        'side': t['side'],
        'price': t['price'],
        'volume': t['volume'],
        'amount': t['amount'],
        'order_type': t['order_type'],
        'status': t['status'],
    } for t in trades])
    tdf.to_csv(out_dir / 'trades.csv', index=False, encoding='utf-8-sig')

    # summary.json
    summary['generated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    summary['note'] = (
        'demo 模式下价格为合成随机游走，仅验证管线，不代表真实收益；'
        '真实评估请用 --mode qmt（需 QMT 已登录）。'
        if mode == 'demo' else ''
    )
    (out_dir / 'summary.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')

    # 仪表盘
    render_dashboard(equity, trades, summary, out_dir / 'index.html')
    logger.info(f"评估产物已写入: {out_dir}")
    return out_dir


def render_dashboard(equity: pd.DataFrame, trades: list, summary: dict, out_path: Path):
    """生成自包含 HTML 仪表盘（ECharts CDN）。"""
    dates = equity['date'].tolist()
    total = equity['total_asset'].round(2).tolist()
    bench = (equity['benchmark_cum_return'] * 100).round(2).tolist()
    cum = (equity['cum_return'] * 100).round(2).tolist()
    dd = (equity['drawdown'] * 100).round(2).tolist()

    kpis = [
        ('累计收益', f"{summary.get('cum_return', 0)*100:.2f}%"),
        ('年化收益', f"{summary.get('annualized_return', 0)*100:.2f}%"),
        ('最大回撤', f"{summary.get('max_drawdown', 0)*100:.2f}%"),
        ('夏普比率', f"{summary.get('sharpe', 0):.2f}"),
        ('胜率', f"{summary.get('win_rate', 0)*100:.1f}%"),
        ('超额收益', f"{summary.get('excess_return', 0)*100:.2f}%"),
        ('成交笔数', f"{summary.get('n_buy_orders', 0)+summary.get('n_sell_orders', 0)}"),
        ('评估区间', f"{summary.get('eval_start','-')}~{summary.get('eval_end','-')}"),
    ]
    kpi_html = ''.join(
        f'<div class="kpi"><div class="kpi-label">{l}</div>'
        f'<div class="kpi-value">{v}</div></div>' for l, v in kpis
    )

    trade_rows = ''.join(
        f"<tr><td>{t['datetime'].strftime('%m-%d %H:%M')}</td><td>{t['code']}</td>"
        f"<td>{'买入' if t['side']=='buy' else '卖出'}</td>"
        f"<td>{t['price']:.3f}</td><td>{t['volume']}</td><td>{t['amount']:.0f}</td></tr>"
        for t in trades[:200]
    ) or '<tr><td colspan="6">暂无真实成交记录</td></tr>'

    mode_tag = summary.get('mode', '')
    note = ('<p class="warn">⚠️ 当前为 demo 模式：价格为合成随机游走，仅用于验证管线，'
            '不代表任何真实收益。真实评估请用 <code>--mode qmt</code>（需 QMT 已登录）。</p>'
            if mode_tag == 'demo' else '')

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ETF 策略净值评估</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
  body{{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;background:#f5f7fa;margin:0;color:#1f2937;}}
  .wrap{{max-width:1100px;margin:0 auto;padding:24px;}}
  h1{{font-size:22px;margin:0 0 4px;}}
  .sub{{color:#6b7280;font-size:13px;margin-bottom:20px;}}
  .kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px;}}
  .kpi{{background:#fff;border-radius:12px;padding:14px 16px;box-shadow:0 1px 3px rgba(0,0,0,.08);}}
  .kpi-label{{color:#6b7280;font-size:12px;}}
  .kpi-value{{font-size:22px;font-weight:700;margin-top:6px;}}
  .card{{background:#fff;border-radius:12px;padding:16px;box-shadow:0 1px 3px rgba(0,0,0,.08);margin-bottom:20px;}}
  .chart{{height:340px;}}
  table{{width:100%;border-collapse:collapse;font-size:13px;}}
  th,td{{padding:8px 10px;border-bottom:1px solid #eee;text-align:left;}}
  th{{color:#6b7280;font-weight:600;}}
  .warn{{background:#fff7ed;border:1px solid #fed7aa;color:#9a3412;padding:10px 14px;border-radius:8px;font-size:13px;}}
  .disclaimer{{color:#9ca3af;font-size:12px;margin-top:24px;line-height:1.6;}}
</style></head>
<body><div class="wrap">
<h1>ETF T+1 策略净值评估</h1>
<div class="sub">数据源：logs/decisions 真实成交 · 价格模式：{mode_tag} · 生成于 {summary.get('generated_at','')}</div>
{note}
<div class="kpis">{kpi_html}</div>
<div class="card"><div id="equity" class="chart"></div></div>
<div class="card"><div id="drawdown" class="chart"></div></div>
<div class="card"><h3 style="margin-top:0">成交明细（前200笔）</h3>
<table><thead><tr><th>时间</th><th>代码</th><th>方向</th><th>价格</th><th>数量</th><th>金额</th></tr></thead>
<tbody>{trade_rows}</tbody></table></div>
<div class="disclaimer">⚠️ 以上内容由 AI 基于系统决策日志与行情数据整理生成，仅供参考，不构成任何投资建议或个股推荐。投资有风险，决策需谨慎。</div>
</div>
<script>
const dates={json.dumps(dates)};
const total={json.dumps(total)};
const bench={json.dumps(bench)};
const cum={json.dumps(cum)};
const dd={json.dumps(dd)};
const eChart=echarts.init(document.getElementById('equity'));
eChart.setOption({{
  title:{{text:'策略净值 vs 沪深300基准',left:'center'}},
  tooltip:{{trigger:'axis'}},
  legend:{{data:['策略累计收益%','基准累计收益%'],bottom:0}},
  xAxis:{{type:'category',data:dates}},
  yAxis:{{type:'value',axisLabel:{{formatter:'{{value}}%'}}}},
  series:[
    {{name:'策略累计收益%',type:'line',data:cum,smooth:true,showSymbol:false,lineStyle:{{width:2}}}},
    {{name:'基准累计收益%',type:'line',data:bench,smooth:true,showSymbol:false,lineStyle:{{width:2,type:'dashed'}}}}
  ]
}});
const dChart=echarts.init(document.getElementById('drawdown'));
dChart.setOption({{
  title:{{text:'策略回撤',left:'center'}},
  tooltip:{{trigger:'axis'}},
  xAxis:{{type:'category',data:dates}},
  yAxis:{{type:'value',axisLabel:{{formatter:'{{value}}%'}},max:0}},
  series:[{{name:'回撤%',type:'line',data:dd,areaStyle:{{color:'rgba(239,68,68,.15)'}},lineStyle:{{color:'#ef4444'}},showSymbol:false}}]
}});
window.addEventListener('resize',()=>{{eChart.resize();dChart.resize();}});
</script></body></html>"""
    out_path.write_text(html, encoding='utf-8')


# ===========================================================================
# 6. 主入口
# ===========================================================================
def main():
    ap = argparse.ArgumentParser(description='ETF 策略净值/收益评估')
    ap.add_argument('--days', type=int, default=None, help='仅评估近 N 天')
    ap.add_argument('--mode', choices=['auto', 'qmt', 'cache', 'demo'], default='auto')
    ap.add_argument('--out', default=str(EVAL_DIR), help='输出目录')
    ap.add_argument('--capital', type=float, default=None, help='初始资金（默认读 Config）')
    args = ap.parse_args()

    out_dir = Path(args.out)
    trades = collect_trades_from_logs(days=args.days)
    if not trades:
        logger.warning("无真实成交记录，仍生成空净值（仅初始资金）。")
    # 初始资金
    if args.capital:
        initial = args.capital
    else:
        try:
            from config.settings import Config
            initial = Config.SIM_INITIAL_CAPITAL if Config.QMT_SIMULATE else Config.INITIAL_CAPITAL
        except Exception:
            initial = 1_000_000

    # 区间
    if trades:
        start = str(min(t['date'] for t in trades))
    else:
        start = str((datetime.now() - timedelta(days=30)).date())
    end = str(datetime.now().date())

    prices, actual_mode = load_prices(trades, args.mode, start, end)
    logger.info(f"价格模式: {actual_mode} | 区间: {start} ~ {end}")

    equity = build_equity(trades, prices, initial, start, end)
    summary = compute_metrics(equity, trades, actual_mode)
    export_results(equity, trades, summary, out_dir, actual_mode)

    # 控制台摘要
    print("\n" + "=" * 50)
    print("策略净值评估结果")
    print("=" * 50)
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print("=" * 50)
    print(f"产物目录: {out_dir}")


if __name__ == '__main__':
    main()
