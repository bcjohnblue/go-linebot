import OpenAI from 'openai';
import { config } from '../../config.js';

// 初始化 Minimax 客户端（使用 OpenAI 兼容接口）
const minimaxClient = new OpenAI({
  apiKey: config.minimax.apiKey,
  baseURL: config.minimax.baseURL || 'https://api.minimax.chat/v1',
});

/**
 * 调用 Minimax 2.1 模型
 * @param {string} text - 要发送的文本
 * @param {object} options - 可选参数
 * @param {string} options.model - 模型名称，默认为 'abab6.5s-chat'
 * @param {number} options.temperature - 温度参数，默认 0.7
 * @param {number} options.maxTokens - 最大 token 数
 * @param {Array} options.messages - 消息历史（可选）
 * @returns {Promise<string>} AI 回复的文本
 */
export async function callMinimax(text, options = {}) {
  try {
    const {
      model = 'abab6.5s-chat',
      temperature = 0.7,
      maxTokens = 400,
      messages,
    } = options;

    // 构建消息列表
    let messageList = messages || [
      {
        role: 'user',
        content: text,
      },
    ];

    // 调用 Minimax API
    const response = await minimaxClient.chat.completions.create({
      model,
      messages: messageList,
      temperature,
      ...(maxTokens && { max_tokens: maxTokens }),
    });

    // 提取回复内容
    const reply = response.choices[0]?.message?.content;
    
    if (!reply) {
      throw new Error('No response from Minimax API');
    }

    return reply;
  } catch (error) {
    console.error('Error calling Minimax API:', error);
    throw new Error(`Minimax API error: ${error.message}`);
  }
}

/**
 * 流式调用 Minimax 2.1 模型
 * @param {string} text - 要发送的文本
 * @param {object} options - 可选参数
 * @param {string} options.model - 模型名称，默认为 'abab6.5s-chat'
 * @param {number} options.temperature - 温度参数，默认 0.7
 * @param {Function} options.onChunk - 接收每个 chunk 的回调函数
 * @returns {Promise<string>} 完整的回复文本
 */
export async function callMinimaxStream(text, options = {}) {
  try {
    const {
      model = 'abab6.5s-chat',
      temperature = 0.7,
      onChunk,
    } = options;

    const messages = [
      {
        role: 'user',
        content: text,
      },
    ];

    // 调用 Minimax API（流式）
    const stream = await minimaxClient.chat.completions.create({
      model,
      messages,
      temperature,
      stream: true,
    });

    let fullResponse = '';

    // 处理流式响应
    for await (const chunk of stream) {
      const content = chunk.choices[0]?.delta?.content || '';
      if (content) {
        fullResponse += content;
        if (onChunk) {
          onChunk(content);
        }
      }
    }

    return fullResponse;
  } catch (error) {
    console.error('Error calling Minimax API (stream):', error);
    throw new Error(`Minimax API error: ${error.message}`);
  }
}

