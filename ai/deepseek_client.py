"""
DeepSeek 客户端 — 多提供商开关（一个变量切换三个模型）
通过 config.settings.Config.LLM_PROVIDER 切换，旧实现全部保留不删：
  - 'deepseek' : DeepSeek 官方接口 (https://api.deepseek.com)            [默认, 模型=deepseek-reasoner / R1]
  - 'aliyun'   : 阿里云百炼 (dashscope.aliyuncs.com)                      [保留, 模型=deepseek-v4-flash, 支持 enable_search 联网搜索]
  - 'tencent'  : 腾讯云 (api.lkeap.cloud.tencent.com)                    [保留, 模型=deepseek-v3]
注：deepseek-reasoner(R1) 不支持 temperature 等采样参数，chat/chat_text/chat_search 已对 R1 自动省略。
"""
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, Tuple
from openai import OpenAI
from loguru import logger
from config.settings import Config


def resolve_provider() -> Tuple[str, str, str, bool]:
    """根据 Config.LLM_PROVIDER 解析当前后端配置。

    返回 (api_key, base_url, model, supports_search)：
      - supports_search 仅 aliyun 后端为 True（百炼 enable_search 联网搜索）
      - 官方 DeepSeek / 腾讯云 无 enable_search，联网搜索降级为普通补全
    三种后端代码均保留，仅在此做路由，不删除任何实现。
    """
    provider = (Config.LLM_PROVIDER or 'deepseek').lower()
    if provider == 'aliyun':
        return (Config.ALIYUN_API_KEY, Config.ALIYUN_BASE_URL,
                Config.ALIYUN_MODEL, Config.ALIYUN_ENABLE_SEARCH)
    if provider == 'tencent':
        return (Config.TENCENT_API_KEY, Config.TENCENT_BASE_URL,
                Config.TENCENT_MODEL, False)
    # 默认 / 'deepseek'：官方接口
    return (Config.DEEPSEEK_OFFICIAL_API_KEY, Config.DEEPSEEK_OFFICIAL_BASE_URL,
            Config.DEEPSEEK_OFFICIAL_MODEL, False)


class DeepSeekClient:
    """DeepSeek API客户端（多提供商可切换：官方 / 阿里云百炼 / 腾讯云）"""

    def __init__(self):
        api_key, base_url, model, supports_search = resolve_provider()
        self.provider = (Config.LLM_PROVIDER or 'deepseek').lower()
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.supports_search = supports_search  # 仅 aliyun 后端支持 enable_search
        self.prompt_dir = Path("logs/prompts")
        self.prompt_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"DeepSeek 客户端 [provider={self.provider}] {base_url} | model={model} | search={'ON' if supports_search else 'OFF'}")

    # ================================================================
    # chat() — 交易决策专用，JSON模式，不下联网搜索
    # ================================================================
    def chat(self, system_prompt: str, user_prompt: str,
             temperature: float = 0.3, max_tokens: int = 8000) -> Optional[Dict]:
        """调用DeepSeek API（JSON模式，交易决策）"""
        try:
            logger.info(f"调用DeepSeek: model={self.model}")
            ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_prefix = f"prompt_{ts_str}"

            json_meta = {
                "timestamp": datetime.now().isoformat(),
                "model": self.model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "response": None,
                "tokens_used": None,
                "error": None,
            }

            # 保存请求 txt
            txt_path = self.prompt_dir / f"{file_prefix}.txt"
            with open(txt_path, 'w', encoding='utf-8') as f:
                f.write(f"{'='*80}\nDeepSeek 交易决策请求\n{'='*80}\n"
                        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"模型: {self.model}\n"
                        f"Temperature: {temperature} | MaxTokens: {max_tokens}\n"
                        f"Search: OFF (JSON mode)\n{'='*80}\n\n"
                        f"【System】\n{'-'*80}\n{system_prompt}\n\n"
                        f"【User】\n{'-'*80}\n{user_prompt}\n\n"
                        f"{'='*80}\n\n【Response】\n{'-'*80}\n(等待...)\n")
            logger.info(f"Prompt已保存: {txt_path}")

            # 调用 API — JSON 模式，不开搜索
            # 注意：deepseek-reasoner(R1) 不支持 temperature 参数，传了会被忽略或报错，故对 R1 省略
            create_kwargs = dict(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            if self.model != "deepseek-reasoner":
                create_kwargs["temperature"] = temperature
            response = self.client.chat.completions.create(**create_kwargs)

            content = response.choices[0].message.content
            total_tokens = response.usage.total_tokens
            logger.info(f"返回成功, tokens={total_tokens}")

            json_meta["response"] = content
            json_meta["tokens_used"] = total_tokens

            # 补写 response
            with open(txt_path, 'a', encoding='utf-8') as f:
                f.write(content + "\n")
                f.write(f"\n{'='*80}\nTokens: {total_tokens}\n")

            json_path = self.prompt_dir / f"{file_prefix}.json"
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(json_meta, f, ensure_ascii=False, indent=2)

            return self._parse_json(content)

        except Exception as e:
            logger.error(f"API调用失败: {e}")
            json_meta["error"] = str(e)
            try:
                json_path = self.prompt_dir / f"{file_prefix}.json"
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(json_meta, f, ensure_ascii=False, indent=2)
                with open(txt_path, 'a', encoding='utf-8') as f:
                    f.write(f"\n【ERROR】\n{e}\n")
            except Exception:
                pass
            return None

    # ================================================================
    # chat_search() — 联网搜索专用，流式输出，用于研报聚合/新闻搜索
    # ================================================================
    def chat_search(self, system_prompt: str, user_prompt: str,
                    temperature: float = 0.3, max_tokens: int = 4000) -> Optional[str]:
        """
        调用 DeepSeek API 并开启联网搜索（流式输出），返回纯文本。
        用于：行业研报聚合、新闻搜索等需要实时信息的场景。
        - provider=aliyun（阿里云百炼）：走 enable_search 真实联网搜索
        - provider=deepseek/tencent：官方接口无 enable_search，降级为普通补全（无实时联网）
        """
        try:
            logger.info(f"调用DeepSeek(搜索模式): model={self.model} search={'ON' if self.supports_search else 'OFF'}")
            ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_prefix = f"search_{ts_str}"

            # 保存请求
            txt_path = self.prompt_dir / f"{file_prefix}.txt"
            with open(txt_path, 'w', encoding='utf-8') as f:
                f.write(f"{'='*80}\nDeepSeek 联网搜索请求\n{'='*80}\n"
                        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"模型: {self.model} | provider={self.provider} | Search: {'ON' if self.supports_search else 'OFF'} | Stream: ON\n"
                        f"{'='*80}\n\n"
                        f"【System】\n{'-'*80}\n{system_prompt}\n\n"
                        f"【User】\n{'-'*80}\n{user_prompt}\n\n"
                        f"{'='*80}\n\n【Response】\n{'-'*80}\n")

            # 组装参数；仅 aliyun 后端附加 enable_search 联网搜索
            # 注意：deepseek-reasoner(R1) 不支持 temperature 参数，对 R1 自动省略
            stream_kwargs = dict(
                max_tokens=max_tokens,
                stream=True,
            )
            if self.model != "deepseek-reasoner":
                stream_kwargs["temperature"] = temperature
            if self.supports_search:
                stream_kwargs['extra_body'] = {"enable_search": True}

            stream = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                **stream_kwargs
            )

            full_content = []
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    full_content.append(chunk.choices[0].delta.content)

            content = "".join(full_content)
            logger.info(f"搜索返回成功, {len(content)} 字符")

            # 补写
            with open(txt_path, 'a', encoding='utf-8') as f:
                f.write(content + "\n")
                f.write(f"\n{'='*80}\n字符数: {len(content)}\n")

            return content

        except Exception as e:
            logger.error(f"搜索API调用失败: {e}")
            try:
                with open(txt_path, 'a', encoding='utf-8') as f:
                    f.write(f"\n【ERROR】\n{e}\n")
            except Exception:
                pass
            return None

    # ================================================================
    # chat_text() — 纯文本模式（保留兼容，不开搜索）
    # ================================================================
    def chat_text(self, system_prompt: str, user_prompt: str,
                  temperature: float = 0.3, max_tokens: int = 4000) -> Optional[str]:
        """纯文本模式（不开搜索，用于一般分析）"""
        try:
            logger.info(f"调用DeepSeek(文本模式): model={self.model}")
            ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_prefix = f"text_{ts_str}"

            txt_path = self.prompt_dir / f"{file_prefix}.txt"
            with open(txt_path, 'w', encoding='utf-8') as f:
                f.write(f"{'='*80}\nDeepSeek 文本模式\n{'='*80}\n"
                        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"模型: {self.model}\n{'='*80}\n\n"
                        f"【System】\n{'-'*80}\n{system_prompt}\n\n"
                        f"【User】\n{'-'*80}\n{user_prompt}\n\n"
                        f"{'='*80}\n\n【Response】\n{'-'*80}\n")

            # 注意：deepseek-reasoner(R1) 不支持 temperature 参数，对 R1 自动省略
            create_kwargs = dict(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=max_tokens,
            )
            if self.model != "deepseek-reasoner":
                create_kwargs["temperature"] = temperature
            response = self.client.chat.completions.create(**create_kwargs)

            content = response.choices[0].message.content
            logger.info(f"返回成功, tokens={response.usage.total_tokens}")

            with open(txt_path, 'a', encoding='utf-8') as f:
                f.write(content + "\n")
                f.write(f"\n{'='*80}\nTokens: {response.usage.total_tokens}\n")

            return content

        except Exception as e:
            logger.error(f"API调用失败(文本模式): {e}")
            return None

    # ================================================================
    # JSON 解析
    # ================================================================
    def _parse_json(self, content: str) -> Optional[Dict]:
        """解析JSON"""
        try:
            if '```json' in content:
                content = content.split('```json')[1].split('```')[0]
            elif '```' in content:
                content = content.split('```')[1].split('```')[0]
            return json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {e}")
            return None
