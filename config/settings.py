"""
系统配置文件 - settings.py
ETF T+1 自动交易系统
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


class Config:
    """系统配置"""

    # ============================================================
    # LLM 提供商开关（一个变量切换三个模型）
    #   LLM_PROVIDER='deepseek' → DeepSeek 官方接口，模型=DEEPSEEK_OFFICIAL_MODEL（默认 deepseek-reasoner / R1）
    #   LLM_PROVIDER='aliyun'   → 阿里云百炼，模型=deepseek-v4-flash（保留，支持 enable_search 联网搜索）
    #   LLM_PROVIDER='tencent'  → 腾讯云，    模型=deepseek-v3（保留）
    # 切换方式：设置环境变量 LLM_PROVIDER，或直接改下方默认值。
    # 注意：三种后端的实现代码均在 deepseek_client.py / 本文件中保留，不删除。
    # ============================================================
    LLM_PROVIDER = os.getenv('LLM_PROVIDER', 'deepseek')

    # -------------------- DeepSeek 官方接口（默认） --------------------
    # 文档: https://api.deepseek.com  |  密钥环境变量 DEEPSEEK_API_KEY (sk-...)
    DEEPSEEK_OFFICIAL_API_KEY = os.getenv('DEEPSEEK_API_KEY', 'sk-your-deepseek-key')
    DEEPSEEK_OFFICIAL_BASE_URL = "https://api.deepseek.com"
    DEEPSEEK_OFFICIAL_MODEL = "deepseek-reasoner"  # 默认=R1推理模型; 也可改 "deepseek-chat"(V3)。LLM_PROVIDER='deepseek' 即走此模型
    # 注意：deepseek-reasoner(R1) 不支持 temperature 等采样参数，客户端已对 R1 自动省略。

    # -------------------- 阿里云百炼（保留，勿删） --------------------
    # 支持 enable_search 联网搜索；密钥环境变量 DASHSCOPE_API_KEY（兼容旧 DEEPSEEK_API_KEY）
    ALIYUN_API_KEY = os.getenv('DASHSCOPE_API_KEY', os.getenv('DEEPSEEK_API_KEY', 'sk-your-aliyun-key'))
    ALIYUN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ALIYUN_MODEL = "deepseek-v4-flash"
    ALIYUN_ENABLE_SEARCH = True  # 仅 aliyun 后端支持 enable_search（chat_search 使用）

    # -------------------- 腾讯云（保留，勿删） --------------------
    # 密钥环境变量 TENCENT_DEEPSEEK_API_KEY
    TENCENT_API_KEY = os.getenv('TENCENT_DEEPSEEK_API_KEY', 'sk-your-tencent-key')
    TENCENT_BASE_URL = "https://api.lkeap.cloud.tencent.com/v1"
    TENCENT_MODEL = "deepseek-v3"

    # 向后兼容别名：旧代码仍引用 Config.DEEPSEEK_* / ENABLE_SEARCH 时取 aliyun 值
    DEEPSEEK_API_KEY = ALIYUN_API_KEY
    DEEPSEEK_BASE_URL = ALIYUN_BASE_URL
    DEEPSEEK_MODEL = ALIYUN_MODEL
    DEEPSEEK_TEMPERATURE = 0.3
    DEEPSEEK_MAX_TOKENS = 8000
    ENABLE_SEARCH = ALIYUN_ENABLE_SEARCH

    # ============================================================
    # QMT 配置
    # ============================================================
    QMT_PATH = Path(os.getenv('QMT_PATH', r'D:\国金QMT交易端模拟\userdata_mini'))
    QMT_ACCOUNT = os.getenv('QMT_ACCOUNT', '888888')  # 模拟账号
    QMT_SIMULATE = True  # True=模拟交易, False=实盘
    QMT_SESSION_ID = int(os.getenv('QMT_SESSION_ID', '123456'))

    # ============================================================
    # 资金与风控参数
    # ============================================================
    INITIAL_CAPITAL = 100000  # 初始资金（实盘）
    SIM_INITIAL_CAPITAL = 1000000  # 模拟初始资金100万

    MAX_TOTAL_POSITION = 0.65  # 最大总仓位 65%
    MAX_SINGLE_POSITION = 0.50  # 单只ETF最大仓位 50%
    MAX_OVERNIGHT_POSITION = 0.6  # 最大过夜仓位60%

    STOP_LOSS_PCT = -0.08  # 止损线 -8%
    TAKE_PROFIT_PCT = 0.08  # 止盈线 +8%
    TRAILING_STOP_PCT = -0.08  # 移动止盈回撤 -8%

    MAX_DAILY_TRADES = 3  # 每日最大交易笔数
    MAX_DAILY_LOSS = -0.03  # 每日最大亏损 -3%（触发熔断）
    MAX_SINGLE_ORDER_AMOUNT = 1_000_000  # 单笔委托金额上限（元），QMT柜台限制，超限自动拆单

    # ============================================================
    # ETF池（37个行业主题，每赛道1-2只代表）
    # 规则：去掉全部宽基指数，保留黄金ETF+黄金股ETF，覆盖37个独立赛道
    # ============================================================
    ETF_POOL = {
        # ==================== 金融（2只） ====================
        '159692.SZ': '证券ETF东财',        # 东财 证券公司30（替换512880）
        '512800.SH': '银行ETF',            # 华宝 中证银行

        # ==================== 消费（4只） ====================
        '512690.SH': '酒ETF',              # 鹏华 中证酒（白酒核心）
        '159928.SZ': '消费ETF',            # 汇添富 中证主要消费
        '159825.SZ': '农业ETF',            # 华夏 中证农业主题
        '159766.SZ': '旅游ETF',            # 富国 中证旅游主题

        # ==================== 科技（7只） ====================
        '512480.SH': '半导体ETF',          # 国联安 中证半导体
        '159599.SZ': '芯片ETF东财',        # 东财 中证芯片产业（替换原芯片ETF）
        '159516.SZ': '半导体设备ETF',      # 国泰 中证半导体材料设备
        '159819.SZ': '人工智能ETF',        # 易方达 中证人工智能
        '516510.SH': '云计算ETF',          # 易方达 中证云计算与大数据
        '562500.SH': '机器人ETF',          # 华夏 中证机器人
        '159869.SZ': '游戏ETF',            # 华夏 中证动漫游戏
        '515880.SH': '通信ETF',            # 国泰 中证通信（含AI算力）

        # ==================== 新能源（3只） ====================
        '515030.SH': '新能源汽车ETF',      # 华夏 中证新能源汽车
        '515790.SH': '光伏ETF',            # 华泰柏瑞 中证光伏产业
        '159160.SZ': '电池ETF东财',        # 东财 中证电池主题

        # ==================== 医药（3只） ====================
        '512010.SH': '医药ETF',            # 华宝 中证医药
        '512170.SH': '医疗ETF',            # 华宝 中证医疗
        '159622.SZ': '创新药ETF东财',      # 东财 沪港深创新药

        # ==================== 军工（1只） ====================
        '512660.SH': '军工ETF',            # 国泰 中证军工

        # ==================== 传媒（1只） ====================
        '512980.SH': '传媒ETF',            # 广发 中证传媒

        # ==================== 周期资源（4只） ====================
        '515220.SH': '煤炭ETF',            # 国泰 中证煤炭
        '512400.SH': '有色金属ETF',        # 南方 中证有色金属
        '515210.SH': '钢铁ETF',            # 国泰 中证钢铁
        '159870.SZ': '化工ETF',            # 鹏华 中证细分化工

        # ==================== 电力（1只） ====================
        '561560.SH': '电力ETF',            # 华泰柏瑞 中证全指电力

        # ==================== 基建地产（2只） ====================
        '512200.SH': '房地产ETF',          # 南方 中证房地产
        '516970.SH': '基建ETF',            # 广发 中证基建

        # ==================== 交通运输（1只） ====================
        '516910.SH': '物流ETF',            # 华泰柏瑞 中证物流

        # ==================== 环保（1只） ====================
        '159861.SZ': '环保ETF',            # 广发 中证环保

        # ==================== 跨境（5只） ====================
        '513050.SH': '中概互联ETF',        # 易方达 中证海外互联
        '520530.SH': '港股通科技ETF东财',  # 东财 中证港股通科技（替换恒生科技）
        '159792.SZ': '港股通互联网ETF',    # 富国 中证港股通互联网
        '513120.SH': '港股创新药ETF',      # 广发 中证香港创新药
        '520600.SH': '港股汽车ETF',        # 广发 中证港股通汽车

        # ==================== 债券（1只） ====================
        '511380.SH': '可转债ETF',          # 博时 中证可转债

        # ==================== 商品（3只） ====================
        '518880.SH': '黄金ETF',            # 华安 黄金（实物黄金）
        '517520.SH': '黄金股ETF',          # 永赢 中证沪深港黄金产业股票
        '159980.SZ': '有色ETF',            # 大成 有色金属期货
    }

    # ============================================================
    # ETF板块分类（15个板块，37只，与ETF_POOL严格一致）
    # ============================================================
    ETF_SECTORS = {
        '金融':           ['159692.SZ', '512800.SH'],
        '消费':           ['512690.SH', '159928.SZ', '159825.SZ', '159766.SZ'],
        '科技':           ['512480.SH', '159599.SZ', '159516.SZ', '159819.SZ', '516510.SH',
                          '562500.SH', '159869.SZ', '515880.SH'],
        '新能源':         ['515030.SH', '515790.SH', '159160.SZ'],
        '医药':           ['512010.SH', '512170.SH', '159622.SZ'],
        '军工':           ['512660.SH'],
        '传媒':           ['512980.SH'],
        '周期资源':       ['515220.SH', '512400.SH', '515210.SH', '159870.SZ'],
        '电力':           ['561560.SH'],
        '基建地产':       ['512200.SH', '516970.SH'],
        '交通运输':       ['516910.SH'],
        '环保':           ['159861.SZ'],
        '跨境':           ['513050.SH', '520530.SH', '159792.SZ',
                           '513120.SH', '520600.SH'],
        '债券':           ['511380.SH'],
        '商品':           ['518880.SH', '517520.SH', '159980.SZ'],
    }

    # ============================================================
    # 四个关键时刻配置
    # ============================================================
    # enabled=False 的时刻不会被调度（不会自动交易），但 --test moment_x 仍可手动触发调测。
    # 默认四个时刻全部启用：开盘竞价(09:25) + 上午确认(10:30) + 午盘转折(12:50) + 尾盘(14:30)。
    # 盘中干预窗口(10:30/12:50)用于盘中暴跌时及时止损/调仓；交易频率由 MAX_DAILY_TRADES(=3 真实成交)
    # 与 prompts/system_prompt.txt 的【换手纪律】共同约束，而非靠关闭决策时刻。
    TRADING_MOMENTS = {
        'moment_1': {
            'time': '09:25',
            'end_time': '09:30',
            'name': '集合竞价决策',
            'description': '开盘价确定，5分钟内给出买卖指令',
            'max_decision_seconds': 300,
            'enabled': True
        },
        'moment_2': {
            'time': '10:30',
            'end_time': '10:45',
            'name': '上午确认决策',
            'description': '验证开盘判断，调整仓位',
            'max_decision_seconds': 300,
            'enabled': True
        },
        'moment_3': {
            'time': '12:50',
            'end_time': '13:00',
            'name': '午盘转折决策',
            'description': '决定下午策略，防范午后跳水',
            'max_decision_seconds': 600,
            'enabled': True
        },
        'moment_4': {
            'time': '14:30',
            'end_time': '14:45',
            'name': '尾盘最终决策',
            'description': '锁定利润，决定过夜仓位',
            'max_decision_seconds': 300,
            'enabled': True
        },
    }

    # ============================================================
    # 搜索配置（已简化：纯 QMT 行情驱动，不依赖外部爬虫）
    # ============================================================
    SEARCH_TIMEOUT = 60  # 保留兼容（不再使用）

    # ============================================================
    # 日志配置
    # ============================================================
    LOG_LEVEL = "INFO"  # DEBUG/INFO/WARNING/ERROR
    LOG_FILE = "logs/trading.log"
    LOG_ROTATION = "10 MB"  # 日志轮转大小
    LOG_RETENTION = "30 days"  # 日志保留天数
    LOG_FORMAT_CONSOLE = "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan> | <level>{message}</level>"
    LOG_FORMAT_FILE = "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name} | {message}"

    # ============================================================
    # 系统配置
    # ============================================================
    TIMEZONE = "Asia/Shanghai"  # 时区
    TRADING_DAYS = [0, 1, 2, 3, 4]  # 交易日（周一到周五）
    HOLIDAY_CHECK = True  # 是否检查节假日
    AUTO_START = True  # 是否自动启动
    HEARTBEAT_INTERVAL = 60  # 心跳间隔（秒）
    MONITOR_INTERVAL_MIN = 30  # 盘中实时监控间隔（分钟）：检查持仓止损/止盈回撤并预警

    # ============================================================
    # 辅助方法
    # ============================================================
    @classmethod
    def get_etf_by_sector(cls, sector: str) -> dict:
        """获取某个板块的所有ETF"""
        codes = cls.ETF_SECTORS.get(sector, [])
        return {code: cls.ETF_POOL[code] for code in codes if code in cls.ETF_POOL}

    @classmethod
    def get_sector_by_code(cls, code: str) -> str:
        """根据ETF代码获取所属板块"""
        for sector, codes in cls.ETF_SECTORS.items():
            if code in codes:
                return sector
        return '未知'

    @classmethod
    def get_all_sectors(cls) -> list:
        """获取所有板块名称"""
        return list(cls.ETF_SECTORS.keys())

    @classmethod
    def get_etf_count(cls) -> int:
        """获取ETF总数"""
        return len(cls.ETF_POOL)

    @classmethod
    def get_sector_count(cls) -> int:
        """获取板块总数"""
        return len(cls.ETF_SECTORS)

    @classmethod
    def display_config(cls):
        """打印配置摘要"""
        print("=" * 60)
        print("ETF T+1 自动交易系统 - 配置摘要")
        print("=" * 60)
        print(f"LLM 提供商: {cls.LLM_PROVIDER}")
        print(f"DeepSeek模型: {cls.DEEPSEEK_MODEL}")
        print(f"交易模式: {'模拟交易' if cls.QMT_SIMULATE else '实盘交易'}")
        print(f"初始资金: {cls.SIM_INITIAL_CAPITAL if cls.QMT_SIMULATE else cls.INITIAL_CAPITAL:,}元")
        print(f"ETF总数: {cls.get_etf_count()}只")
        print(f"板块总数: {cls.get_sector_count()}个")
        print(f"最大仓位: {cls.MAX_TOTAL_POSITION * 100}%")
        print(f"单只上限: {cls.MAX_SINGLE_POSITION * 100}%")
        print(f"过夜上限: {cls.MAX_OVERNIGHT_POSITION * 100}%")
        print(f"止损线: {cls.STOP_LOSS_PCT * 100}%")
        print(f"止盈线: {cls.TAKE_PROFIT_PCT * 100}%")
        print(f"关键时刻: {', '.join([m['time'] for m in cls.TRADING_MOMENTS.values()])}")
        print("=" * 60)


# ============================================================
# 运行配置检查
# ============================================================
if __name__ == '__main__':
    Config.display_config()
    print("\n板块明细:")
    for sector in Config.get_all_sectors():
        etfs = Config.get_etf_by_sector(sector)
        print(f"  {sector}: {len(etfs)}只 - {', '.join(etfs.values())}")