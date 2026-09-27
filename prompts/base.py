"""
Prompt公共部分
"""
import pathlib

from typing import Dict, List


class TradingPrompts:
    """交易Prompt管理"""

    # 系统角色
    _PROMPT_DIR = pathlib.Path(__file__).resolve().parent
    SYSTEM_PROMPT = (_PROMPT_DIR / 'system_prompt.txt').read_text(encoding='utf-8').strip()

    @staticmethod
    def get_search_queries(moment: str) -> List[str]:
        """获取搜索查询"""
        from prompts.moment_1_opening import Moment1Prompt
        from prompts.moment_2_morning import Moment2Prompt
        from prompts.moment_3_noon import Moment3Prompt
        from prompts.moment_4_closing import Moment4Prompt

        mapping = {
            'moment_1': Moment1Prompt.SEARCH_QUERIES,
            'moment_2': Moment2Prompt.SEARCH_QUERIES,
            'moment_3': Moment3Prompt.SEARCH_QUERIES,
            'moment_4': Moment4Prompt.SEARCH_QUERIES,
        }
        return mapping.get(moment, Moment1Prompt.SEARCH_QUERIES)

    @staticmethod
    def get_prompt(moment: str) -> str:
        """获取Prompt模板"""
        from prompts.moment_1_opening import Moment1Prompt
        from prompts.moment_2_morning import Moment2Prompt
        from prompts.moment_3_noon import Moment3Prompt
        from prompts.moment_4_closing import Moment4Prompt

        mapping = {
            'moment_1': Moment1Prompt.PROMPT,
            'moment_2': Moment2Prompt.PROMPT,
            'moment_3': Moment3Prompt.PROMPT,
            'moment_4': Moment4Prompt.PROMPT,
        }
        return mapping.get(moment, Moment1Prompt.PROMPT)