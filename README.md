# ETF AI 自动交易系统

> 用 AI（DeepSeek）自动买卖 ETF 的小程序。每天固定的 4 个时间点，它会自动读行情、读研报、问 AI 该买什么卖什么，然后自动下单。默认**模拟交易（假钱，100 万）**，确认稳妥后再切真钱。

---

## 一、这系统是什么？（大白话）

把四个角色连起来：

| 角色 | 是什么 | 在本系统里 |
|------|--------|-----------|
| 研报 | 券商出的行业分析报告 | 开盘前自动备好，不用你搜 |
| QMT 行情 | 实时价格（"眼睛"） | 程序自动拉 |
| AI（DeepSeek） | 分析+决策（"大脑"） | 默认用官方 `deepseek-reasoner` 推理模型 |
| QMT 下单 | 真正提交订单（"手"） | 通过国金证券 miniQMT 完成 |

一句话：**你开着 miniQMT，运行 `main.py`，它就在 09:25 / 10:30 / 12:50 / 14:30 自动帮你操作。**

---

## 二、适合谁 / 不适合谁

✅ 适合：想用 AI 辅助交易、愿意花 1~2 小时配置环境、能接受先用模拟盘练手的人。
❌ 不适合：想"装好就能自动赚钱"的人（任何策略都有亏的可能）；手机用户（只能在 Windows 跑）；不想开户的人。

---

## 三、环境准备清单

| 需要 | 说明 | 备注 |
|------|------|------|
| Python 3.11 | 运行程序的发动机 | **必须 3.11**，3.12+ 会报错（xtquant 不支持）。本机用 `D:\software\anaconda3\python.exe` |
| DeepSeek API Key | 调 AI 的钥匙 | platform.deepseek.com 注册充值，充 10 元够测很久 |
| 国金证券 QMT | 程序和券商之间的桥 | 需开户并开通 miniQMT 权限 |
| QMT 资金账号 | 登录用 | 当前测试用模拟账号 888888 |
| 本项目代码 | 就是这套系统 | 在 `D:\策略\etf_trading_system\` |

---

## 四、五步上手（极简版）

完整图文教程见 👉 **[运行教程.html](运行教程.html)**（从"ETF 是什么"讲起的小白版）。

1. **装 Python 3.11**（推荐 Anaconda，自带 3.11）。
2. **装依赖**：
   ```bash
   cd D:\策略\etf_trading_system
   D:\software\anaconda3\python.exe -m pip install -r requirements.txt
   ```
3. **拿 DeepSeek Key** 填进 `.env` 的 `DEEPSEEK_API_KEY`。
4. **开 miniQMT**（`XtMiniQmt.exe`）并登录，确认 `.env` 的 `QMT_PATH` 指向 `userdata_mini` 目录。
5. **运行**：
   ```bash
   D:\software\anaconda3\python.exe main.py
   ```
   想立刻试一次不等待定时：`D:\software\anaconda3\python.exe main.py --test moment_1`

---

## 五、四个核心能力

1. **M1 集合竞价限价单** — 每天第一笔（09:25）正好在竞价时段，柜台只收限价单。系统自动把买卖转成限价单挂入开盘竞价，避免废单。
2. **策略净值 / 收益评估** — 从真实成交重建资产曲线，对比沪深300ETF 基准：
   ```bash
   D:\software\anaconda3\python.exe strategy/evaluator.py          # 评估全部历史
   D:\software\anaconda3\python.exe strategy/evaluator.py --days 30 # 近 30 天
   ```
   产物在 `logs/eval/`（`index.html` 仪表盘 + csv + json）。
3. **findtruman 开盘前预爬** — 每天 08:30（盘前）/ 12:30（午后）自动调用 findtruman 结构化研报接口生成当日研报，保证开盘就有情报，不依赖外部定时器。
4. **研报 → ETF 映射** — 自动把研报里的行业词（半导体/券商/军工…）翻译成 `ETF_POOL` 里具体的 ETF 代码并标注情绪，追加进喂给 AI 的研报，帮 AI 更精准下单。
5. **多轮 prompt 传递** — AI 每次被调用都是「失忆」的。系统自动从磁盘读取今天所有更早时刻的真实执行结果（成交/挂单/废单/跳过），拼成「前序执行回顾」注入每个后续时刻的 prompt：10:30 看 09:25、12:50 看前两次、14:30 看前三次，**重启或分段运行也不丢上下文**。

---

## 六、常用命令速查

| 目的 | 命令 |
|------|------|
| 启动系统（定时模式） | `D:\software\anaconda3\python.exe main.py` |
| 手动测试某时刻 | `... main.py --test moment_1`（或 `moment_3` / `all`） |
| QMT 通道一键自检（排错首选） | `D:\software\anaconda3\python.exe diagnose_qmt.py` |
| 净值评估（全部历史） | `... strategy/evaluator.py` |
| 净值评估（近 30 天） | `... strategy/evaluator.py --days 30` |
| 净值评估（演示数据） | `... strategy/evaluator.py --mode demo` |

---

## 七、常见问题

- **启动报「QMT连接失败」？** 确认开的是极简版 `XtMiniQmt.exe`（不是完整版）、已登录、`.env` 的 `QMT_PATH` 指向 `userdata_mini`。
- **报「402 Insufficient Balance」？** DeepSeek 余额不足，去充值。
- **一直「等待交易时刻...」？** 正常，只在交易日 09:25/10:30/12:50/14:30 触发；用 `--test` 立刻试。
- **显示「废单」？** 多半是 QMT 交易账号没在客户端登录（行情连接≠交易连接），先跑 `diagnose_qmt.py` 看第②步账户能否查到。
- **模拟还是实盘？** `config/settings.py` 里 `QMT_SIMULATE=True` 是模拟（假钱）。先用模拟账号测试，确认 AI 决策合理再改 `False` 上实盘。

---

## 八、目录速览

```
main.py              主程序入口
diagnose_qmt.py      QMT 通道自检（排错）
config/settings.py   系统配置（ETF池/风控/时刻）
config/etf_mapping.py 行业词→ETF 映射规则
ai/deepseek_client.py 调用 DeepSeek（三种后端可切换）
trading/qmt_client.py 连 QMT、拉行情、下单
strategy/executor.py  解析 AI 决策并下单（含 M1 限价单）
strategy/evaluator.py 净值/收益评估
search/               研报获取与映射（findtruman 优先 + 预爬）
prompts/              四个时刻的 Prompt 模板
utils/                日志/日报/决策状态
logs/                 运行产物（决策/研报/prompts/报表/eval/alerts）
```

> ⚠️ 以上内容由 AI 基于公开信息整理生成，仅供参考，不构成任何投资建议或个股推荐。投资有风险，决策需谨慎。
