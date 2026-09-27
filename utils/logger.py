"""
日志工具
"""
import sys
from pathlib import Path
from loguru import logger
from config.settings import Config


def setup_logger(name: str = __name__):
    """配置日志"""
    log_path = Path(Config.LOG_FILE)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger.remove()

    logger.add(
        sys.stdout,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan> | <level>{message}</level>",
        level=Config.LOG_LEVEL,
        colorize=True
    )

    logger.add(
        Config.LOG_FILE,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name} | {message}",
        level=Config.LOG_LEVEL,
        rotation=Config.LOG_ROTATION,
        retention="30 days",
        encoding="utf-8"
    )

    return logger.bind(name=name)