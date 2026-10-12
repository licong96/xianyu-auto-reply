"""
AI 铺货文案生成

功能：
1. 组装文案生成提示词（支持配置里的自定义模板，占位符非法时回退内置模板）
2. 调用 OpenAI 兼容接口批量生成商品标题与描述
3. 解析并清洗返回的 JSON，字段长度与素材库接口保持一致

说明：拆分自 ai_listing_runner，便于单文件控制在 500 行以内。
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List

import httpx
from loguru import logger

from common.models.ai_listing_config import AiListingConfig
from common.services.ai_text_client import (
    AiCallError,
    DEFAULT_TEXT_TIMEOUT,
    chat_completion_result,
)

# 单批文案生成的最大条数：一次请求生成太多容易被截断
TEXT_BATCH_SIZE = 10

# 每条文案预留的输出 token。中文描述按约 300 字估算，2048 不够写完多条。
TOKENS_PER_ITEM = 600
# 自动抬高输出上限时的封顶，避免单次请求无限变大
TOKEN_BUDGET_CAP = 8192
# 输出被截断后，继续补剩余条数的最大轮数
MAX_TRUNCATION_RETRIES = 2

# 素材字段长度上限，与素材库创建接口保持一致，避免入库后前端编辑必然校验失败
TITLE_MAX_LENGTH = 200
DESCRIPTION_MAX_LENGTH = 1500

# 内置文案提示词模板（配置里填了 prompt_template 时优先用用户的）
DEFAULT_PROMPT_TEMPLATE = """请为闲鱼平台生成 {count} 条二手商品素材，主题：{keyword}。
{hints}要求：
1. title 不超过 30 个字，突出卖点；
2. description 不超过 300 个字，说明成色、规格、发货等买家关心的信息；
3. price 为人民币数字（元）；
4. 每条内容互不重复；
5. 字符串内不要出现英文双引号和换行，保证每条 JSON 对象完整闭合。
只返回如下 JSON，不要任何解释或代码块标记：
{{"items":[{{"title":"...","description":"...","price":0}}]}}"""

SYSTEM_PROMPT = "你是熟悉闲鱼二手交易的商品文案专家，只输出符合要求的 JSON，不输出任何解释。"


def _build_hints(params: Dict[str, Any]) -> str:
    """把分类/成色/价格等约束拼成提示词里的补充说明"""
    defaults = params.get("material_defaults") or {}
    hints: List[str] = []
    if defaults.get("category"):
        hints.append(f"商品分类：{defaults['category']}。")
    if defaults.get("condition"):
        hints.append(f"成色：{defaults['condition']}。")
    if params.get("price_mode") == "fixed" and params.get("price") is not None:
        hints.append(f"价格固定为 {params['price']} 元。")
    elif params.get("price_min") is not None and params.get("price_max") is not None:
        hints.append(f"价格控制在 {params['price_min']}~{params['price_max']} 元之间。")
    return "".join(hints) + ("\n" if hints else "")


def _strip_code_fence(text: str) -> str:
    """去掉模型可能加上的 ```json 代码块标记"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned[3:]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    return cleaned.strip()


def _output_token_budget(configured: int, batch_size: int) -> int:
    """按本批条数估算输出 token，配置值更高时仍尊重配置。"""
    needed = max(1, batch_size) * TOKENS_PER_ITEM
    return max(configured, min(TOKEN_BUDGET_CAP, needed))


def _is_truncated(finish_reason: str) -> bool:
    """结束原因是否表示输出被 token 上限截断。"""
    return finish_reason.strip().lower() in {"length", "max_tokens"}


def _iter_complete_objects(text: str) -> List[str]:
    """按括号配对抽出已经闭合的 JSON 对象，截断在半截的最后一条会被跳过。"""
    objects: List[str] = []
    stack: List[int] = []
    in_string = False
    escape = False
    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            stack.append(index)
        elif char == "}" and stack:
            start = stack.pop()
            objects.append(text[start:index + 1])
    return objects


def _clean_items(raw_items: List[Any], expect: int) -> List[Dict[str, Any]]:
    """清洗标题、描述和价格，丢掉缺字段的条目。"""
    items: List[Dict[str, Any]] = []
    seen_titles: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()[:TITLE_MAX_LENGTH]
        description = str(raw.get("description") or "").strip()[:DESCRIPTION_MAX_LENGTH]
        if not title or not description or title in seen_titles:
            continue
        seen_titles.add(title)
        items.append({"title": title, "description": description, "price": raw.get("price")})
        if len(items) >= expect:
            break
    return items


def _loads_payload(text: str) -> Any:
    """解析整段 JSON。前后有解释文字时，截取最外层对象或数组再解析。"""
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except (ValueError, TypeError):
                continue
    raise ValueError("不是完整 JSON")


def _salvage_items(text: str, expect: int) -> List[Dict[str, Any]]:
    """全文不是合法 JSON 时，只保留已经闭合、且带标题和描述的对象。"""
    salvaged: List[Any] = []
    for chunk in _iter_complete_objects(text):
        try:
            obj = json.loads(chunk)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict) and (obj.get("title") or obj.get("description")):
            salvaged.append(obj)
    return _clean_items(salvaged, expect)


def parse_generated_items(text: str, expect: int) -> tuple[List[Dict[str, Any]], bool]:
    """解析模型返回的 JSON 文案

    Args:
        text: 模型输出。
        expect: 本批期望条数。
    Returns:
        ``(条目列表, 是否经过残缺修复)``。条目含 title/description/price，最多 expect 条。
        第二个值表示全文无法直接解析，只抽出了已闭合的条目。
    Raises:
        AiCallError: 返回内容不是合法 JSON，且没有任何完整条目。
    """
    cleaned = _strip_code_fence(text)
    repaired = False
    try:
        payload = _loads_payload(cleaned)
    except (ValueError, TypeError):
        items = _salvage_items(cleaned, expect)
        if not items:
            hint = "，输出可能在中途被截断" if not cleaned.rstrip().endswith(("}", "]")) else ""
            raise AiCallError(f"AI返回内容不是合法JSON（共{len(cleaned)}字{hint}）：{cleaned[:200]}")
        logger.warning(
            f"【AI铺货】文案 JSON 不完整，已保留 {len(items)} 条闭合内容，原文长度={len(cleaned)}"
        )
        return items, True

    raw_items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(raw_items, list) or not raw_items:
        items = _salvage_items(cleaned, expect)
        if not items:
            raise AiCallError(f"AI返回内容缺少items字段：{cleaned[:200]}")
        repaired = True
    else:
        items = _clean_items(raw_items, expect)
        if not items:
            raise AiCallError("AI返回的条目缺少标题或描述")
    return items, repaired


async def generate_text_batch(
    client: httpx.AsyncClient,
    config: AiListingConfig,
    params: Dict[str, Any],
    keyword: str,
    batch_size: int,
) -> List[Dict[str, Any]]:
    """生成一批文案

    Args:
        client: 复用的 httpx 客户端。
        config: AI 铺货配置。
        params: 任务参数快照。
        keyword: 生成主题。
        batch_size: 本批条数。
    Returns:
        条目列表。
    Raises:
        AiCallError: 接口调用或解析失败。
    """
    template = (config.prompt_template or "").strip() or DEFAULT_PROMPT_TEMPLATE
    hints = _build_hints(params)

    def prompt_for(count: int) -> str:
        """按本次实际条数渲染提示词，补生成时不会继续要求原来的条数。"""
        try:
            return template.format(count=count, keyword=keyword, hints=hints)
        except (KeyError, IndexError, ValueError):
            # 用户自定义模板里有非法占位符时，退回内置模板，避免整个任务卡死
            logger.warning("【AI铺货】自定义提示词模板占位符非法，已回退内置模板")
            return DEFAULT_PROMPT_TEMPLATE.format(count=count, keyword=keyword, hints=hints)

    return await _request_items(client, config, prompt_for, batch_size, depth=0)


def _merge_items(current: List[Dict[str, Any]], extra: List[Dict[str, Any]], expect: int) -> List[Dict[str, Any]]:
    """按标题去重后合并，避免截断重试把同一条又生成一遍。"""
    merged = list(current)
    seen = {str(item.get("title") or "") for item in merged}
    for item in extra:
        title = str(item.get("title") or "")
        if not title or title in seen:
            continue
        seen.add(title)
        merged.append(item)
        if len(merged) >= expect:
            break
    return merged


async def _request_items(
    client: httpx.AsyncClient,
    config: AiListingConfig,
    prompt_for: Callable[[int], str],
    batch_size: int,
    depth: int,
) -> List[Dict[str, Any]]:
    """请求一批文案。输出被截断时保留已闭合条目，并补生成剩余条数。"""
    configured = int(config.text_max_tokens or 2048)
    budget = _output_token_budget(configured, batch_size)
    result = await chat_completion_result(
        base_url=config.text_base_url,
        api_key=config.text_api_key,
        model=config.text_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt_for(batch_size)},
        ],
        temperature=float(config.text_temperature or 0.7),
        max_tokens=budget,
        timeout=DEFAULT_TEXT_TIMEOUT,
        client=client,
        json_mode=True,
    )
    logger.info(
        f"【AI铺货】文案返回 model={config.text_model} 期望={batch_size} "
        f"max_tokens={budget} 输出长度={len(result.content)} finish_reason={result.finish_reason or '未知'}"
    )

    try:
        items, repaired = parse_generated_items(result.content, batch_size)
    except AiCallError:
        if depth >= MAX_TRUNCATION_RETRIES or batch_size <= 1 or not _is_truncated(result.finish_reason):
            raise
        smaller = max(1, batch_size // 2)
        logger.warning(f"【AI铺货】文案被截断且无法解析，改为每批 {smaller} 条重试")
        first = await _request_items(client, config, prompt_for, smaller, depth + 1)
        missing = batch_size - len(first)
        if missing <= 0:
            return first[:batch_size]
        extra = await _request_items(client, config, prompt_for, missing, depth + 1)
        return _merge_items(first, extra, batch_size)

    truncated = _is_truncated(result.finish_reason) or repaired
    if truncated and len(items) < batch_size and batch_size > 1 and depth < MAX_TRUNCATION_RETRIES:
        missing = batch_size - len(items)
        logger.warning(f"【AI铺货】文案输出不完整，已得到 {len(items)} 条，继续生成剩余 {missing} 条")
        extra = await _request_items(client, config, prompt_for, missing, depth + 1)
        items = _merge_items(items, extra, batch_size)
    return items



__all__ = ["TEXT_BATCH_SIZE", "generate_text_batch", "parse_generated_items"]
