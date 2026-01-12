import { messagingApi, middleware } from '@line/bot-sdk';
import { config } from '../config.js';
import {
  createTask,
  getTask,
  getTaskResult,
  TaskStatus
} from '../services/taskManager.js';
import { runKataGoAnalysis } from './katagoHandler.js';
import { filterCriticalMoves, getTopScoreLossMoves } from './sgfHandler.js';
import { drawAllMovesGif } from './drawHandler.js';
import { callOpenAI } from '../LLM/providers/openai.js';
import { writeFile, mkdir, readdir, stat, readFile } from 'fs/promises';
import { join } from 'path';
import { fileURLToPath } from 'url';
import { dirname } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const { MessagingApiClient, MessagingApiBlobClient } = messagingApi;

const client = new MessagingApiClient({
  channelAccessToken: config.line.channelAccessToken
});

const blobClient = new MessagingApiBlobClient({
  channelAccessToken: config.line.channelAccessToken
});

let currentSgfFileName = null;
let botUserId = null;

// 獲取 Bot 自己的 User ID
(async () => {
  try {
    const botInfo = await client.getBotInfo();
    botUserId = botInfo.userId;
    console.log('Bot User ID:', botUserId);
  } catch (error) {
    console.error('Failed to get bot info:', error);
  }
})();

/**
 * 驗證 URL 是否為有效的 HTTPS URL
 * @param {string} url - 要驗證的 URL
 * @returns {boolean} 是否為有效的 HTTPS URL
 */
function isValidHttpsUrl(url) {
  if (!url || typeof url !== 'string') {
    return false;
  }

  try {
    const parsedUrl = new URL(url);
    return parsedUrl.protocol === 'https:';
  } catch (error) {
    return false;
  }
}

/**
 * 編碼 URL 路徑（保留斜線，編碼其他特殊字符）
 * @param {string} path - 要編碼的路徑
 * @returns {string} 編碼後的路徑
 */
function encodeUrlPath(path) {
  // 將路徑按 / 分割，對每個部分進行編碼，然後重新組合
  return path
    .split('/')
    .map((part) => encodeURIComponent(part))
    .join('/');
}

/**
 * 創建單個 Bubble 內容（用於 Carousel）
 * @param {number} moveNumber - 手數
 * @param {string} color - 顏色（B/W）
 * @param {string} played - 落子位置
 * @param {string} comment - 評論
 * @param {string} previewImageUrl - 預覽圖 URL（GIF）
 * @param {string} videoUrl - 影片 URL（MP4）
 * @returns {Object} Bubble 物件
 */
function createVideoPreviewBubble(
  moveNumber,
  color,
  played,
  comment,
  previewImageUrl,
  videoUrl
) {
  const colorText = color === 'B' ? '黑' : '白';

  // 限制評論長度（LINE Flex Message 有字數限制）
  const maxCommentLength = 500;
  const truncatedComment =
    comment.length > maxCommentLength
      ? comment.substring(0, maxCommentLength) + '...'
      : comment;

  return {
    type: 'bubble',
    hero: {
      type: 'image',
      url: previewImageUrl,
      size: 'full',
      aspectRatio: '1:1',
      aspectMode: 'cover',
      action: {
        type: 'uri',
        uri: videoUrl,
        label: '觀看動畫'
      }
    },
    body: {
      type: 'box',
      layout: 'vertical',
      contents: [
        {
          type: 'text',
          text: `📍 第 ${moveNumber} 手（${colorText}）`,
          weight: 'bold',
          size: 'lg',
          color: '#1DB446'
        },
        {
          type: 'text',
          text: `落子位置：${played}`,
          size: 'sm',
          color: '#666666',
          margin: 'md'
        },
        {
          type: 'separator',
          margin: 'md'
        },
        {
          type: 'text',
          text: truncatedComment,
          wrap: true,
          size: 'sm',
          margin: 'md',
          color: '#333333'
        }
      ]
    },
    footer: {
      type: 'box',
      layout: 'vertical',
      spacing: 'sm',
      contents: [
        {
          type: 'button',
          style: 'primary',
          height: 'sm',
          action: {
            type: 'uri',
            label: '🎬 觀看動態棋譜',
            uri: videoUrl
          },
          color: '#1DB446'
        }
      ]
    }
  };
}

/**
 * 創建 Carousel Flex Message（合併多個 bubble）
 * @param {Array<Object>} bubbles - Bubble 陣列
 * @param {number} startIndex - 起始索引（用於 altText）
 * @param {number} totalCount - 總數（用於 altText）
 * @returns {Object} Flex Message 物件
 */
function createCarouselFlexMessage(bubbles, startIndex = 1, totalCount = bubbles.length) {
  return {
    type: 'flex',
    altText: `關鍵手數分析（${startIndex}-${startIndex + bubbles.length - 1}/${totalCount}）`,
    contents: {
      type: 'carousel',
      contents: bubbles
    }
  };
}

/**
 * 幫助訊息內容
 */
const HELP_MESSAGE = `歡迎使用圍棋分析 Bot！

📤 上傳 SGF 棋譜檔案，棋譜會被保存到伺服器。

指令：
• help / 幫助 / 說明 - 顯示此說明
• 覆盤 - 對最新上傳的棋譜執行 KataGo 分析

使用流程：
1️⃣ 上傳 SGF 棋譜檔案
2️⃣ 輸入「覆盤」開始分析
3️⃣ 等待 10-15 分鐘獲得分析結果

注意事項：
• 分析使用 KataGo AI（visits=200）
• KataGo 全盤分析約 10 分鐘
• ChatGPT 評論生成約 1 分鐘
• GIF 動畫繪製約 10 秒
• 覆盤功能每次消耗 4 個推播訊息 × 群組人數
• 每月訊息上限為 200 則，請注意使用頻率，超出上限將無法使用覆盤功能`;

/**
 * 保存 SGF 檔案到 static 資料夾
 * @param {Buffer} fileBuffer - 檔案內容
 * @param {string} originalFileName - 原始檔案名稱
 * @returns {Promise<{fileName: string, filePath: string}>} 保存的檔案資訊
 */
async function saveSgfFile(fileBuffer, originalFileName) {
  const staticDir = join(__dirname, '../../static');
  const filePath = join(staticDir, originalFileName);

  // 確保 static 資料夾存在
  await mkdir(staticDir, { recursive: true });

  // 寫入檔案
  await writeFile(filePath, fileBuffer);

  return { fileName: originalFileName, filePath };
}

/**
 * 發送訊息（優先使用 replyMessage 減少用量，如果 replyToken 已過期則使用 pushMessage）
 * @param {string} targetId - 推送目標 ID
 * @param {string|null} replyToken - 回覆 Token（可能為 null 或已過期）
 * @param {Array} messages - 訊息陣列
 * @returns {Promise<boolean>} 是否成功使用 replyMessage
 */
async function sendMessage(targetId, replyToken, messages) {
  // 如果有 replyToken，嘗試使用 replyMessage
  if (replyToken) {
    try {
      await client.replyMessage({
        replyToken,
        messages
      });
      return true; // 成功使用 replyMessage
    } catch (error) {
      // replyToken 可能已過期，回退到 pushMessage
      console.log('replyToken expired or invalid, using pushMessage instead');
    }
  }

  // 使用 pushMessage
  await client.pushMessage({
    to: targetId,
    messages
  });
  return false; // 使用了 pushMessage
}

/**
 * 處理覆盤指令
 * @param {string} targetId - 推送目標 ID
 * @param {string|null} replyToken - 回覆 Token（用於初始回覆，減少用量）
 */
async function handleReviewCommand(targetId, replyToken) {
  const staticDir = join(__dirname, '../../static');
  let usedReplyToken = false; // 追蹤是否已使用 replyToken

  try {
    const sgfFileName = currentSgfFileName;
    if (!sgfFileName) {
      usedReplyToken = await sendMessage(targetId, replyToken, [
        {
          type: 'text',
          text: '❌ 找不到棋譜，請先上傳棋譜。'
        }
      ]);
      return;
    }

    const sgfPath = join(staticDir, sgfFileName);

    // 通知開始分析（使用 replyMessage 如果可用）
    usedReplyToken = await sendMessage(targetId, replyToken, [
      {
        type: 'text',
        text: `✅ 開始對棋譜：${sgfFileName} 進行覆盤分析，完成大約需要 12 分鐘...，請稍後再回來查看分析結果。`
      }
    ]);

    // 使用 replyToken 後設為 null，後續訊息使用 pushMessage
    if (usedReplyToken) {
      replyToken = null;
    }

    // 執行 KataGo 分析
    console.log(`Starting KataGo analysis for: ${sgfPath}`);
    const result = await runKataGoAnalysis(sgfPath, {
      onProgress: (output) => {
        process.stdout.write(output);
      },
      visits: 200
    });

    // 檢查分析是否成功
    if (!result.success) {
      await sendMessage(
        targetId,
        null, // replyToken 已用過或不存在
        [
          {
            type: 'text',
            text: `❌ KataGo 分析失敗：${result.stderr || '未知錯誤'}`
          }
        ]
      );
      return;
    }

    // 檢查是否有 moveStats
    if (!result.moveStats) {
      await sendMessage(targetId, null, [
        {
          type: 'text',
          text: '❌ 分析完成但無法轉換結果數據'
        }
      ]);
      return;
    }

    // 分析成功，通知用戶
    await sendMessage(targetId, null, [
      {
        type: 'text',
        text: `✅ KataGo 全盤分析完成！

📊 分析結果：
• 檔案：${sgfFileName}
• 總手數：${result.moveStats.moves.length}

🤖 接續使用 ChatGPT 分析 20 筆關鍵手數並生成評論，大約需要 1 分鐘...，請稍後再回來查看評論結果。`
      }
    ]);

    // 篩選前 20 個關鍵點
    const criticalMoves = filterCriticalMoves(result.moveStats.moves);
    const topScoreLossMoves = getTopScoreLossMoves(criticalMoves, 20);

    console.log('Preparing to call OpenAI...');

    // 調用 LLM 取得評論
    const llmComments = await callOpenAI(topScoreLossMoves);
    // const llmComments = [];
    console.log(`LLM generated ${llmComments.length} comments`);

    // 生成 GIF 動畫
    // await sendMessage(targetId, null, [
    //   {
    //     type: 'text',
    //     text: `🎨 正在繪製棋局動畫（共 ${topScoreLossMoves.length} 手）...`
    //   }
    // ]);

    // 使用 result.jsonPath（完整路徑）而不是 result.jsonFilename
    const jsonFilePath = result.jsonPath;
    if (!jsonFilePath) {
      console.error('KataGo analysis result:', JSON.stringify(result, null, 2));
      await sendMessage(targetId, null, [
        {
          type: 'text',
          text: '❌ 無法取得 KataGo 分析結果檔案路徑'
        }
      ]);
      return;
    }

    // 從完整路徑中提取文件名（不含副檔名）
    const jsonFilename = jsonFilePath
      .split('/')
      .pop()
      .replace(/\.json$/, '');
    const outputDir = join(__dirname, '../../draw/outputs', jsonFilename);

    console.log(`JSON file path: ${jsonFilePath}`);
    console.log(`Output directory: ${outputDir}`);

    const gifPaths = await drawAllMovesGif(jsonFilePath, outputDir);
    console.log(`Generated ${gifPaths.length} GIFs`);

    // 建立評論的映射（move number -> comment）
    const commentMap = {};
    llmComments.forEach((item) => {
      commentMap[item.move] = item.comment;
    });

    // 建立 GIF 的映射（move number -> gif path）
    const gifMap = {};
    gifPaths.forEach((path) => {
      const filename = path.split('/').pop() || path.split('\\').pop();
      const match = filename.match(/move_(\d+)\.gif/);
      if (match) {
        gifMap[parseInt(match[1])] = path;
      }
    });

    // 先發送 global_board.png 讓使用者看到全盤手順
    const globalBoardPath = join(outputDir, 'global_board.png');
    const publicUrl = config.server.publicUrl;

    try {
      if (publicUrl && isValidHttpsUrl(publicUrl)) {
        // 構建全盤圖片的公開 URL
        const relativePath = globalBoardPath.split('/draw/outputs/')[1];
        // 編碼路徑以處理空格和特殊字符
        const encodedPath = encodeUrlPath(relativePath);
        const globalBoardUrl = `${publicUrl}/draw/outputs/${encodedPath}`;

        // 驗證構建的 URL 是否有效
        if (isValidHttpsUrl(globalBoardUrl)) {
          await sendMessage(targetId, null, [
            {
              type: 'text',
              text: '🗺️ 全盤手順圖：'
            },
            {
              type: 'image',
              originalContentUrl: globalBoardUrl,
              previewImageUrl: globalBoardUrl
            }
          ]);
        } else {
          console.warn(`Invalid HTTPS URL for global board: ${globalBoardUrl}`);
          await sendMessage(targetId, null, [
            {
              type: 'text',
              text: `🗺️ 全盤手順圖已生成\n\n⚠️ 圖片 URL 無效（必須使用 HTTPS）\n請檢查 PUBLIC_URL 環境變數設定`
            }
          ]);
        }
      } else {
        console.warn(`PUBLIC_URL not set or not HTTPS: ${publicUrl}`);
        await sendMessage(targetId, null, [
          {
            type: 'text',
            text: `🗺️ 全盤手順圖已生成\n\n⚠️ 未設定有效的 PUBLIC_URL（必須使用 HTTPS）\n請在環境變數中設定 PUBLIC_URL`
          }
        ]);
      }

      // 等待 1 秒後再開始發送每一手的評論
      await new Promise((resolve) => setTimeout(resolve, 1000));
    } catch (globalBoardError) {
      console.error('Error sending global board image:', globalBoardError);
      if (globalBoardError.response) {
        console.error(
          'LINE API Error Response:',
          globalBoardError.response.data
        );
      }
      // 即使全盤圖片發送失敗，也繼續發送其他內容
    }

    // 收集所有關鍵手數的 bubble（用於合併成 Carousel）
    const allBubbles = [];
    const fallbackMessages = []; // 無法生成 bubble 的訊息（如 URL 無效）

    for (let i = 0; i < topScoreLossMoves.length; i++) {
      const move = topScoreLossMoves[i];
      const moveNumber = move.move;
      const comment = commentMap[moveNumber] || '無評論';
      const gifPath = gifMap[moveNumber];

      // 如果有 GIF，嘗試創建 bubble
      if (gifPath) {
        try {
          if (publicUrl && isValidHttpsUrl(publicUrl)) {
            const relativePath = gifPath.split('/draw/outputs/')[1];
            const encodedPath = encodeUrlPath(relativePath);

            // 將 .gif 替換為 .mp4
            const mp4Path = encodedPath.replace(/\.gif$/, '.mp4');
            const mp4Url = `${publicUrl}/draw/outputs/${mp4Path}`;

            // GIF 作為預覽圖
            const gifUrl = `${publicUrl}/draw/outputs/${encodedPath}`;

            // 驗證構建的 URL 是否有效
            if (isValidHttpsUrl(mp4Url) && isValidHttpsUrl(gifUrl)) {
              console.log(`Creating bubble for move ${moveNumber}`);

              // 創建 bubble（用於 Carousel）
              const bubble = createVideoPreviewBubble(
                moveNumber,
                move.color,
                move.played,
                comment,
                gifUrl,
                mp4Url
              );

              allBubbles.push(bubble);
            } else {
              console.warn(
                `Invalid HTTPS URL for move ${moveNumber}: ${mp4Url}`
              );
              // 如果 URL 無效，記錄為回退訊息
              fallbackMessages.push({
                moveNumber,
                text: `📍 第 ${moveNumber} 手（${
                  move.color === 'B' ? '黑' : '白'
                }）- ${move.played}\n\n${comment}\n\n⚠️ 影片連結無效`
              });
            }
          } else {
            // 如果沒有有效的 PUBLIC_URL，記錄為回退訊息
            fallbackMessages.push({
              moveNumber,
              text: `📍 第 ${moveNumber} 手（${
                move.color === 'B' ? '黑' : '白'
              }）- ${move.played}\n\n${comment}`
            });
          }
        } catch (flexError) {
          console.error(
            `Error preparing bubble for move ${moveNumber}:`,
            flexError
          );
          // 錯誤時記錄為回退訊息
          fallbackMessages.push({
            moveNumber,
            text: `📍 第 ${moveNumber} 手（${
              move.color === 'B' ? '黑' : '白'
            }）- ${move.played}\n\n${comment}`
          });
        }
      } else {
        // 如果沒有 GIF，記錄為回退訊息
        fallbackMessages.push({
          moveNumber,
          text: `📍 第 ${moveNumber} 手（${
            move.color === 'B' ? '黑' : '白'
          }）- ${move.played}\n\n${comment}`
        });
      }
    }

    // 分批發送 Carousel（LINE 限制每組最多 12 個 bubble，設定為 10 以確保穩定）
    const MAX_BUBBLES_PER_CAROUSEL = 10;
    const totalBubbles = allBubbles.length;

    if (totalBubbles > 0) {
      console.log(`Sending ${totalBubbles} bubbles in Carousel format`);

      // 分批處理
      for (let i = 0; i < allBubbles.length; i += MAX_BUBBLES_PER_CAROUSEL) {
        const batch = allBubbles.slice(i, i + MAX_BUBBLES_PER_CAROUSEL);
        const startIndex = i + 1;
        const endIndex = Math.min(i + batch.length, totalBubbles);

        try {
          // 創建 Carousel Flex Message
          const carouselMessage = createCarouselFlexMessage(
            batch,
            startIndex,
            totalBubbles
          );

          await sendMessage(targetId, null, [carouselMessage]);

          console.log(
            `Sent Carousel ${Math.floor(i / MAX_BUBBLES_PER_CAROUSEL) + 1} (moves ${startIndex}-${endIndex})`
          );

          // 避免發送太快，間隔 1 秒
          if (i + MAX_BUBBLES_PER_CAROUSEL < allBubbles.length) {
            await new Promise((resolve) => setTimeout(resolve, 1000));
          }
        } catch (carouselError) {
          console.error(
            `Error sending Carousel (moves ${startIndex}-${endIndex}):`,
            carouselError
          );
        }
      }
    }

    // 發送無法生成 bubble 的回退訊息（如果有的話）
    if (fallbackMessages.length > 0) {
      console.log(`Sending ${fallbackMessages.length} fallback text messages`);
      for (const fallback of fallbackMessages) {
        try {
          await sendMessage(targetId, null, [
            {
              type: 'text',
              text: fallback.text
            }
          ]);
          await new Promise((resolve) => setTimeout(resolve, 500));
        } catch (fallbackError) {
          console.error(
            `Error sending fallback message for move ${fallback.moveNumber}:`,
            fallbackError
          );
        }
      }
    }

    // 完成通知
    // await sendMessage(targetId, null, [
    //   {
    //     type: 'text',
    //     text: `🎉 所有分析已完成！共分析 ${topScoreLossMoves.length} 個關鍵手數。`
    //   }
    // ]);
  } catch (error) {
    console.error('Error in 覆盤 command:', error);
    await sendMessage(targetId, null, [
      {
        type: 'text',
        text: `❌ 執行覆盤時發生錯誤：${error.message}`
      }
    ]);
  }
}

/**
 * 處理文字訊息
 */
export async function handleTextMessage(event) {
  const { replyToken, message, source } = event;
  let text = message.text.trim();

  // 在群組/聊天室中，只處理 mention 訊息
  if (source.type === 'group' || source.type === 'room') {
    // 檢查是否有 mention
    if (
      !message.mention ||
      !message.mention.mentionees ||
      message.mention.mentionees.length === 0
    ) {
      // 沒有 mention，忽略此訊息
      return Promise.resolve(null);
    }

    // 檢查 mention 是否包含 bot 自己
    const mentions = message.mention.mentionees;
    const isBotMentioned = mentions.some(
      (mentionee) => mentionee.userId === botUserId
    );

    if (!isBotMentioned) {
      // mention 的不是 bot，忽略此訊息
      return Promise.resolve(null);
    }

    // 移除 mention 標記以取得實際指令
    // 使用 mention 的 index 和 length 精確移除
    let cleanText = text;

    // 從後往前移除，避免索引位置改變
    mentions
      .sort((a, b) => b.index - a.index)
      .forEach((mention) => {
        cleanText =
          cleanText.substring(0, mention.index) +
          cleanText.substring(mention.index + mention.length);
      });

    text = cleanText.trim();
  }

  if (text === 'help' || text === '幫助' || text === '說明') {
    return client.replyMessage({
      replyToken,
      messages: [
        {
          type: 'text',
          text: HELP_MESSAGE
        }
      ]
    });
  }

  if (text === '覆盤') {
    // 取得推送目標 ID
    const targetId = source.groupId || source.roomId || source.userId;
    // 傳遞 replyToken 用於初始回覆（減少用量）
    await handleReviewCommand(targetId, replyToken);
    return Promise.resolve(null);
  }

  // if (text === 'status' || text === '狀態') {
  //   // 這裡可以實作查詢用戶任務狀態的功能
  //   return client.replyMessage({
  //     replyToken,
  //     messages: [{
  //       type: 'text',
  //       text: '狀態查詢功能開發中...',
  //     }],
  //   });
  // }

  // return client.replyMessage({
  //   replyToken,
  //   messages: [
  //     {
  //       type: 'text',
  //       text: '請上傳 SGF 棋譜檔案（.sgf）進行分析。輸入 "help" 查看說明。'
  //     }
  //   ]
  // });
}

/**
 * 處理檔案訊息
 */
export async function handleFileMessage(event) {
  const { replyToken, message, source } = event;

  // 取得推送目標 ID（根據來源類型）
  const targetId = source.groupId || source.roomId || source.userId;
  // 取得用戶 ID（用於任務追蹤）
  const userId = source.userId || targetId;

  try {
    // 取得檔案內容
    const contentId = message.id;
    const stream = await blobClient.getMessageContent(contentId);

    // 將 stream 轉換為 Buffer
    const chunks = [];
    for await (const chunk of stream) {
      chunks.push(chunk);
    }
    const fileBuffer = Buffer.concat(chunks);

    // 檢查檔案類型
    const fileName = message.fileName || 'game.sgf';
    if (!fileName.toLowerCase().endsWith('.sgf')) {
      return;
    }

    // 保存文件到 static 文件夾
    const { fileName: uploadedSgfFile, filePath: uploadedSgfPath } =
      await saveSgfFile(fileBuffer, fileName);

    currentSgfFileName = uploadedSgfFile;

    // 通知用戶文件已保存（使用 replyMessage 減少用量）
    await client.replyMessage({
      replyToken,
      messages: [
        {
          type: 'text',
          text: `✅ 棋譜已保存！

📁 檔案: ${fileName}

棋譜已保存到伺服器，後續可執行 "@NTUGOAnalysis 覆盤" 指令進行分析...`
        }
      ]
    });
  } catch (error) {
    console.error('Error handling file message:', error);
    await client.replyMessage({
      replyToken,
      messages: [
        {
          type: 'text',
          text: `❌ 儲存棋譜時發生錯誤：${error.message}`
        }
      ]
    });
  }
}

/**
 * 監控任務並回傳結果
 * @param {string} targetId - 推送目標 ID（userId、groupId 或 roomId）
 * @param {string} taskId - 任務 ID
 */
async function monitorAndReplyTask(targetId, taskId) {
  const maxWaitTime = 10 * 60 * 1000; // 10 分鐘
  const checkInterval = 10000; // 每 10 秒檢查一次
  const startTime = Date.now();
  let lastStatus = null;

  const checkTask = async () => {
    const task = getTask(taskId);

    if (!task) {
      await client.pushMessage({
        to: targetId,
        messages: [
          {
            type: 'text',
            text: '❌ 任務不存在或已過期'
          }
        ]
      });
      return;
    }

    // 檢查是否超時
    if (Date.now() - startTime > maxWaitTime) {
      await client.pushMessage({
        to: targetId,
        messages: [
          {
            type: 'text',
            text: '⏱️ 任務執行超時，請稍後再試或聯繫管理員。'
          }
        ]
      });
      return;
    }

    // 如果狀態改變，通知用戶
    if (task.status !== lastStatus) {
      lastStatus = task.status;

      let statusText = '';
      switch (task.status) {
        case TaskStatus.VM_CREATING:
          statusText = '🔧 正在建立 VM...';
          break;
        case TaskStatus.VM_RUNNING:
          statusText = '🚀 VM 已啟動，準備分析...';
          break;
        case TaskStatus.ANALYZING:
          statusText = '⚙️ 正在執行 KataGo 分析...';
          break;
        case TaskStatus.COMPLETED:
          // 取得結果並回傳
          try {
            const resultBuffer = await getTaskResult(taskId);
            if (resultBuffer) {
              const resultText = resultBuffer.toString('utf-8');

              // 如果結果太長，分段發送
              const maxLength = 5000;
              if (resultText.length > maxLength) {
                await client.pushMessage({
                  to: targetId,
                  messages: [
                    {
                      type: 'text',
                      text: `✅ 分析完成！\n\n結果（前 ${maxLength} 字元）：\n\n${resultText.substring(
                        0,
                        maxLength
                      )}...\n\n（結果已截斷，完整結果請查看 GCS）`
                    }
                  ]
                });
              } else {
                await client.pushMessage({
                  to: targetId,
                  messages: [
                    {
                      type: 'text',
                      text: `✅ 分析完成！\n\n結果：\n\n${resultText}`
                    }
                  ]
                });
              }
            } else {
              await client.pushMessage({
                to: targetId,
                messages: [
                  {
                    type: 'text',
                    text: '✅ 分析完成，但無法取得結果檔案。'
                  }
                ]
              });
            }
          } catch (error) {
            console.error('Error getting task result:', error);
            await client.pushMessage({
              to: targetId,
              messages: [
                {
                  type: 'text',
                  text: `✅ 分析完成，但讀取結果時發生錯誤：${error.message}`
                }
              ]
            });
          }
          return; // 任務完成，停止監控
        case TaskStatus.FAILED:
          await client.pushMessage({
            to: targetId,
            messages: [
              {
                type: 'text',
                text: `❌ 任務失敗：${task.error || '未知錯誤'}`
              }
            ]
          });
          return; // 任務失敗，停止監控
        case TaskStatus.INTERRUPTED:
          await client.pushMessage({
            to: targetId,
            messages: [
              {
                type: 'text',
                text: '⚠️ VM 被中斷，正在重試...'
              }
            ]
          });
          break;
      }

      if (statusText) {
        await client.pushMessage({
          to: targetId,
          messages: [
            {
              type: 'text',
              text: statusText
            }
          ]
        });
      }
    }

    // 如果任務還在進行中，繼續監控
    if (
      [
        TaskStatus.PENDING,
        TaskStatus.VM_CREATING,
        TaskStatus.VM_RUNNING,
        TaskStatus.ANALYZING,
        TaskStatus.INTERRUPTED
      ].includes(task.status)
    ) {
      setTimeout(checkTask, checkInterval);
    }
  };

  // 開始監控
  setTimeout(checkTask, 5000); // 5 秒後開始檢查
}

/**
 * LINE Webhook 中間件
 */
// export const lineMiddleware = middleware({
//   channelAccessToken: config.line.channelAccessToken,
//   channelSecret: config.line.channelSecret
// });
