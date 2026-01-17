from openai import AsyncOpenAI
from config import config
from logger import logger
from typing import List, Dict, Any, Optional, Callable
import os

# Initialize OpenAI client
openai_client = AsyncOpenAI(
    api_key=config["openai"]["api_key"] or os.getenv("OPENAI_API_KEY"),
    base_url=config["openai"]["base_url"] or os.getenv("OPENAI_BASE_URL"),
)

# Default system prompt
DEFAULT_SYSTEM_PROMPT = """你是一個圍棋策略分析助手。下面提供了棋局歷史資料，每一個物件代表一步落子：

資料格式：
[
  {
    "move": <手數>,
    "color": <B/W>,
    "played": <玩家落子>,
    "ai_best": <AI 推薦落子>,
    "pv": [<AI 預測的主要變化順序>],
    "winrate_before": <當前落子方(color)下這手前勝率>,
    "winrate_after": <當前落子方(color)下這手後勝率>,
    "score_loss": <這手棋相對於 AI 推薦落子造成的局面劣化，以目數為單位，數值越大代表此手越不理想，偏離最佳落子的程度越大>
  },
  ...
]

請你做以下事情：
1. 分析每一步落子，找出**關鍵失誤或值得注意的手**（例如勝率損失大、錯過 AI 推薦落子）。
2. 針對每一步產生評論，評論可以包含：
   - 評估該手落子的好壞，重點說明勝率是增加還是下降以及變化幅度，請用生動、自然的語言描述局面變化，例如「這手讓局面穩定了一些」或「這手失誤讓黑棋優勢大幅縮小」
   - 根據 AI 推薦的最佳落子，推測如果下該手可能會如何改善，並用生動語言描述可能帶來的局面優勢，例如「若下此手，白棋可更穩固控制左上角」
   - 可以簡要指出主要變化序列(PV) 中值得注意的連續落子，但以勝率影響為主，並用自然語言點出關鍵節點，例如「接下來黑棋可能會沿著右下角尋找反攻空間」
   - 根據勝率變化，提供對後續策略的建議，例如應加強防守、擴張地盤或尋求更安全路線，用易懂、自然的語言提醒玩家應注意的重點
3. 評論中可以根據 score_loss 的大小簡要敘述說明，但不要直接說出 score_loss 的數值
4. 評論中的 PV 請說是 AI 推薦的變化序列，不要出現 PV 字眼，大家會看不懂，並且只要提到 AI 的第一手落子即可，不要提到其他落子
5. 請提到這手棋下之後勝率的變化，例如「這手棋下之後，黑棋勝率從 50% 下降到 40%」
6. 請將分析結果整理成 **JSON 陣列**，外層使用 '[]' 包起來，每個元素對應一手棋。禁止多餘文字，整個回傳必須是一個合法 JSON 陣列。格式如下：
[
  {
    "move": <手數>,
    "comment": "<對這手的文字評論>"
  }
  ...
]
7. JSON 中每手都要有 "comment"，即使該手不是關鍵手，也給簡短評論。
8. 用自然文字撰寫評論，不要再嵌套 JSON 或列表。"""


async def call_openai(
    moves: List[Dict[str, Any]],
    model: str = "gpt-5-mini",
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    max_tokens: int = 10000,
) -> List[Dict[str, Any]]:
    """Call OpenAI API to process KataGo analysis results"""
    # Build prompt
    user_prompt = build_prompt(moves)

    try:
        response = await openai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_completion_tokens=max_tokens,
        )

        print(f"OpenAI API response: {response}")
        print(f"OpenAI API response message: {response.choices[0].message}")

        if response.choices[0].finish_reason != "stop":
            raise ValueError("LLM output truncated")

        content = response.choices[0].message.content or "{}"

        # Try to parse JSON response
        try:
            import json

            parsed = json.loads(content)
            # If returned is object containing array, extract array; otherwise return directly
            if isinstance(parsed, list):
                return parsed
            elif isinstance(parsed, dict):
                if "moves" in parsed:
                    return parsed["moves"]
                elif "comments" in parsed:
                    return parsed["comments"]
                elif "data" in parsed and isinstance(parsed["data"], list):
                    return parsed["data"]
                return parsed
            return parsed
        except json.JSONDecodeError:
            # If not valid JSON, try to extract JSON part
            import re

            json_match = re.search(r"\[[\s\S]*\]", content)
            if json_match:
                return json.loads(json_match.group(0))
            # If all fail, return original content
            return content
    except Exception as error:
        logger.error(f"OpenAI API error: {error}", exc_info=True)
        raise RuntimeError(f"OpenAI API call failed: {str(error)}")


def build_prompt(moves: List[Dict[str, Any]]) -> str:
    """Build prompt to send to OpenAI"""
    import json

    prompt = "資料：\n\n"
    prompt += json.dumps(moves, ensure_ascii=False, indent=2)
    return prompt


async def call_openai_stream(
    moves: List[Dict[str, Any]],
    on_chunk: Callable[[str], None],
    model: str = "gpt-5-mini",
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    max_tokens: int = 3000,
):
    """Stream call OpenAI API"""
    user_prompt = build_prompt(moves)

    try:
        stream = await openai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=0.7,
            stream=True,
        )

        async for chunk in stream:
            content = chunk.choices[0].delta.content
            if content:
                on_chunk(content)
    except Exception as error:
        logger.error(f"OpenAI API stream error: {error}", exc_info=True)
        raise RuntimeError(f"OpenAI API stream call failed: {str(error)}")
