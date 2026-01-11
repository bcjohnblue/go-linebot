import OpenAI from 'openai';
import { config } from '../../config.js';

// - 標註失誤類型，依據 score_loss 分類：
//        * "大失誤": score_loss 超過 10，偏離 AI 最佳落子，局面大幅惡化
//        * "小錯誤": score_loss 在 3~10 之間，局面略微惡化
//        * "好手": score_loss 接近 0，，落子接近最佳落子，局面理想

// 初始化 OpenAI 客户端
const openai = new OpenAI({
  apiKey: config.openai?.apiKey || process.env.OPENAI_API_KEY,
  baseURL: config.openai?.baseURL || process.env.OPENAI_BASE_URL
});

// 默认系统提示词
const DEFAULT_SYSTEM_PROMPT = `你是一個圍棋策略分析助手。下面提供了棋局歷史資料，每一個物件代表一步落子：

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
8. 用自然文字撰寫評論，不要再嵌套 JSON 或列表。`;

/**
 * 调用 OpenAI API 处理 KataGo 分析结果
 *
 * @param {Object} moves - KataGo 分析结果数据（JSON 格式）
 * @param {Object} options - 选项
 * @param {string} options.model - 模型名称（默认 'gpt-5-mini'）
 * @param {string} options.systemPrompt - 系统提示词（可选）
 * @param {number} options.maxTokens - 最大 token 数（可选）
 * @returns {Promise<string>} OpenAI 的回复文本
 */
export async function callOpenAI(moves, options = {}) {
  const {
    model = 'gpt-5-mini',
    systemPrompt = DEFAULT_SYSTEM_PROMPT,
    maxTokens = 10000
  } = options;

  // 构建提示词
  const userPrompt = buildPrompt(moves);

  try {
    const response = await openai.chat.completions.create({
      model,
      messages: [
        {
          role: 'system',
          content: systemPrompt
        },
        {
          role: 'user',
          content: userPrompt
        }
      ],
      max_completion_tokens: maxTokens
      // response_format: { type: 'json_object' } // 要求返回 JSON 格式
    });

    console.log('OpenAI API response:', response);
    console.log('OpenAI API response message:', response.choices[0]?.message);

    if (response.choices[0].finish_reason !== 'stop') {
      throw new Error('LLM output truncated');
    }

    const content = response.choices[0]?.message.content || '{}';

    // const usage = response.usage;
    // console.log('OpenAI API usage:', usage);

    // 尝试解析 JSON 响应
    try {
      const parsed = JSON.parse(content);
      // 如果返回的是包含数组的对象，提取数组；否则直接返回
      if (Array.isArray(parsed)) {
        return parsed;
      } else if (
        parsed.moves ||
        parsed.comments ||
        Array.isArray(parsed.data)
      ) {
        return parsed.moves || parsed.comments || parsed.data || parsed;
      }
      return parsed;
    } catch (error) {
      // 如果不是有效的 JSON，尝试提取 JSON 部分
      const jsonMatch = content.match(/\[[\s\S]*\]/);
      if (jsonMatch) {
        return JSON.parse(jsonMatch[0]);
      }
      // 如果都失败，返回原始内容
      return content;
    }
  } catch (error) {
    console.error('OpenAI API error:', error);
    throw new Error(`OpenAI API call failed: ${error.message}`);
  }
}

/**
 * 构建发送给 OpenAI 的提示词
 *
 * @param {Object} moves - KataGo 分析结果数据
 * @returns {string} 格式化后的提示词
 */
function buildPrompt(moves) {
  let prompt = `資料：\n\n`;
  prompt += JSON.stringify(moves);

  return prompt;
}

/**
 * 流式调用 OpenAI API
 *
 * @param {Object} moves - KataGo 分析结果数据
 * @param {Function} onChunk - 接收每个 chunk 的回调函数
 * @param {Object} options - 选项
 * @returns {Promise<void>}
 */
export async function callOpenAIStream(moves, onChunk, options = {}) {
  const {
    model = 'gpt-5-mini',
    systemPrompt = DEFAULT_SYSTEM_PROMPT,
    maxTokens = 3000
  } = options;

  const userPrompt = buildPrompt(moves);

  try {
    const stream = await openai.chat.completions.create({
      model,
      messages: [
        {
          role: 'system',
          content: systemPrompt
        },
        {
          role: 'user',
          content: userPrompt
        }
      ],
      max_tokens: maxTokens,
      temperature: 0.7,
      stream: true
    });

    for await (const chunk of stream) {
      const content = chunk.choices[0]?.delta?.content;
      if (content) {
        onChunk(content);
      }
    }
  } catch (error) {
    console.error('OpenAI API stream error:', error);
    throw new Error(`OpenAI API stream call failed: ${error.message}`);
  }
}
