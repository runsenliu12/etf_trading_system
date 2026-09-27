"""
公众号自动化真实性校验：检查今日是否存在"真实"交易时段决策。

- 只扫描 logs/decisions/（--test 手动测试数据已隔离到 logs/decisions_test/，
  且带 test:true 标记；load_today_decisions 也会跳过 test 记录）。
- 判定标准：
    * 存在任意 >=09:25 的真实决策           -> REAL
    * 存在真实决策但全部 <09:25（疑似预演） -> ONLY_PRE
    * 无任何真实决策                         -> NONE

输出（stdout 末行为机器可解析状态）：
    DECISION_STATUS=REAL|ONLY_PRE|NONE

公众号自动化第 0 步读取该状态：NONE / ONLY_PRE 时暂停发文，避免把
盘前测试预演当成真实复盘推送给读者。
"""
import json
import sys
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).resolve().parent.parent
DECISION_DIR = BASE / "logs" / "decisions"


def check(date_str: str = None) -> str:
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    ymd = date_str.replace("-", "")

    if not DECISION_DIR.exists():
        print(f"[INFO] 决策目录不存在: {DECISION_DIR}")
        print("DECISION_STATUS=NONE")
        return "NONE"

    real = []  # (time_str, moment)
    for f in sorted(DECISION_DIR.glob(f"moment_*_{ymd}_*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        # 跳过 --test 产生的预演数据（纵深防御，正常应已在 decisions_test/）
        if data.get("test"):
            continue
        # 文件名形如 moment_1_20260724_092500.json，末段为 HHMMSS
        parts = f.stem.split("_")
        time_part = parts[-1] if len(parts) >= 4 else ""
        hhmmss = time_part if (time_part.isdigit() and len(time_part) == 6) else ""
        real.append((hhmmss, data.get("moment", "")))

    if not real:
        print(f"[INFO] 今日 {date_str} 无真实交易决策（可能仅运行过 --test 盘前预演）")
        print("DECISION_STATUS=NONE")
        return "NONE"

    # 是否存在 >=09:25 的真实交易时段决策
    has_trading_hour = any(hhmmss and hhmmss >= "092500" for hhmmss, _ in real)
    if has_trading_hour:
        print(f"[INFO] 今日 {date_str} 存在真实交易时段(>=09:25)决策，可正常发文。")
        print("DECISION_STATUS=REAL")
        return "REAL"

    print(f"[INFO] 今日 {date_str} 仅有盘前(<09:25)真实决策，疑似预演，暂停发文。")
    print("DECISION_STATUS=ONLY_PRE")
    return "ONLY_PRE"


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else None
    check(d)
