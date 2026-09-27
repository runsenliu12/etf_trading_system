"""
iris.findtruman.io 研报数据爬取模块
调用两个公开 API：行业研报 / 全球指数
"""
import json
import time
import requests
from pathlib import Path
from datetime import datetime
from typing import Dict, List
from loguru import logger


class ResearchScraper:
    """爬取 iris.findtruman.io 行业研报数据"""

    BASE_URL = "https://iris.findtruman.io"
    ENDPOINT_INDUSTRY = "/quantitative_api/stock-selection/industry-research-reports"
    ENDPOINT_INDICES = "/quantitative_api/stock-selection/global-indices"

    def __init__(self, save_dir: str = "logs/research"):
        self.save_dir = Path(save_dir)
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = 60
        self.max_retries = 2
        self.retry_delay = 3

    def _fetch_with_retry(self, url: str, params: dict = None) -> dict or list:
        """带重试的请求"""
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = requests.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, dict):
                    return data
                return data
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    logger.warning(f"请求失败(第{attempt + 1}次): {url}, {e}")
                    time.sleep(self.retry_delay)
        logger.error(f"请求失败(已重试{self.max_retries}次): {url}")
        return {} if isinstance(last_error, Exception) else []

    def _fetch_industry_reports(self) -> List[Dict]:
        """爬取行业研报（默认 50 条）"""
        url = f"{self.BASE_URL}{self.ENDPOINT_INDUSTRY}"
        data = self._fetch_with_retry(url)
        if isinstance(data, dict):
            records = data.get("data", data.get("list", data.get("records", [])))
            return records if isinstance(records, list) else []
        return data if isinstance(data, list) else []

    def _fetch_global_indices(self) -> Dict:
        """爬取全球指数"""
        url = f"{self.BASE_URL}{self.ENDPOINT_INDICES}"
        data = self._fetch_with_retry(url)
        return data if isinstance(data, dict) else {}

    def scrape_all(self) -> Dict:
        """爬取全部数据"""
        logger.info("开始爬取 iris.findtruman.io...")
        result = {
            "industry_reports": [],
            "global_indices": {},
            "timestamp": datetime.now().isoformat(),
        }

        # 1. 行业研报
        try:
            result["industry_reports"] = self._fetch_industry_reports()
            logger.info(f"行业研报: {len(result['industry_reports'])} 条")
        except Exception as e:
            logger.error(f"行业研报爬取失败: {e}")

        # 2. 全球指数
        try:
            result["global_indices"] = self._fetch_global_indices()
            logger.info(f"全球指数: 已获取")
        except Exception as e:
            logger.error(f"全球指数爬取失败: {e}")

        logger.info("爬取完成")
        return result

    def save_raw(self, data: Dict, label: str = "morning"):
        """保存原始爬取数据"""
        today = datetime.now().strftime("%Y%m%d")
        filename = self.save_dir / f"raw_{label}_{today}.json"
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"原始数据已保存: {filename}")
