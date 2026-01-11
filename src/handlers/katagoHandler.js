import { readFile, writeFile } from 'fs/promises';
import { spawn } from 'child_process';
import { fileURLToPath } from 'url';
import { dirname, join, resolve, isAbsolute } from 'path';
import { existsSync } from 'fs';

/**
 * 將 JSONL 文件內容轉換為 JSON 陣列
 *
 * @param {string} jsonlContent - JSONL 文件內容（每行一個 JSON 對象）
 * @returns {Array} JSON 對象陣列
 */
export function jsonlToJson(jsonlContent) {
  if (!jsonlContent || typeof jsonlContent !== 'string') {
    return [];
  }

  // 按行分割，過濾空行
  const lines = jsonlContent
    .trim()
    .split('\n')
    .filter((line) => line.trim());

  // 將每行解析為 JSON 對象
  const jsonArray = lines
    .map((line, index) => {
      try {
        return JSON.parse(line);
      } catch (error) {
        console.error(`Error parsing JSONL line ${index + 1}:`, error);
        console.error(`Line content: ${line.substring(0, 100)}...`);
        return null;
      }
    })
    .filter((item) => item !== null); // 過濾掉解析失敗的行

  return jsonArray;
}

/**
 * 讀取 JSONL 文件並轉換為 JSON 陣列
 *
 * @param {string} filePath - JSONL 文件路徑
 * @returns {Promise<Array>} JSON 對象陣列
 */
export async function readJsonlFile(filePath) {
  try {
    const content = await readFile(filePath, 'utf-8');
    return jsonlToJson(content);
  } catch (error) {
    console.error(`Error reading JSONL file ${filePath}:`, error);
    throw error;
  }
}

/**
 * 將 JSONL 文件轉換為結構化的 JSON 對象
 *
 * @param {string} filePath - JSONL 文件路徑
 * @returns {Promise<Object>} 包含 filename, totalLines, data 的對象
 */
export async function convertJsonlToJson(filePath) {
  try {
    const data = await readJsonlFile(filePath);
    const filename = filePath.split('/').pop() || filePath.split('\\').pop();

    return {
      filename,
      totalLines: data.length,
      data
    };
  } catch (error) {
    console.error(`Error converting JSONL to JSON:`, error);
    throw error;
  }
}

/**
 * 從 KataGo JSONL 響應中提取單手統計信息
 * 返回格式：
 * {
 *   "move": 59,
 *   "color": "B",
 *   "played": "C13",
 *   "ai_best": "F11",
 *   "pv": ["F11", "F12", "D11", "B12", "C13", "C14", "H12"],
 *   "winrate_before": 72.3,
 *   "winrate_after": 60.5,
 *   "score_loss": 3.1
 * }
 *
 * @param {Object} response - KataGo JSONL 響應對象
 * @returns {Object} 包含統計信息的對象
 */
export function extractMoveStats(response) {
  if (!response || typeof response !== 'object') {
    return null;
  }

  const turnNumber = response.turnNumber ?? 0;
  const moveNumber = turnNumber + 1; // turnNumber 從 0 開始，move 從 1 開始

  const rootInfo = response.rootInfo || {};
  const moveInfos = response.moveInfos || [];
  const currentPlayer = rootInfo.currentPlayer || 'B';

  // 獲取實際走的下一步（從 nextMove 和 nextMoveColor）
  const nextMove = response.nextMove;
  const nextMoveColor = response.nextMoveColor;
  const nextRootInfo = response.nextRootInfo || {};

  // winrate_before: 走子之前的勝率（當前節點的勝率）
  // rootInfo.winrate 是從當前玩家視角的勝率（0-1），需要轉換為百分比
  const winrateBefore = rootInfo.winrate ?? 0;
  const winrateBeforePercent =
    currentPlayer === 'B' ? winrateBefore * 100 : (1 - winrateBefore) * 100;

  // winrate_after: 走子之後的勝率（相對於當前落子方）
  // 優先使用 nextRootInfo.winrate，如果沒有則從實際走的走子的 moveInfo 中獲取
  let winrateAfter = null;
  if (nextRootInfo.winrate !== undefined) {
    // 修正：使用 currentPlayer 而不是 nextPlayer，保持視角一致
    winrateAfter =
      currentPlayer === 'B'
        ? nextRootInfo.winrate * 100
        : (1 - nextRootInfo.winrate) * 100;
  } else if (nextMove && moveInfos.length > 0) {
    // 如果沒有 nextRootInfo，嘗試從實際走的走子的 moveInfo 中獲取
    const playedMoveInfo = moveInfos.find((m) => m.move === nextMove);
    if (playedMoveInfo && playedMoveInfo.winrate !== undefined) {
      // 修正：使用 currentPlayer 而不是 nextPlayer，保持視角一致
      winrateAfter =
        currentPlayer === 'B'
          ? playedMoveInfo.winrate * 100
          : (1 - playedMoveInfo.winrate) * 100;
    }
  }

  // 計算實際走的走子和 AI 最佳走子
  let playedMove = null;
  let aiBestMove = null;
  let pv = [];
  let scoreLoss = null;

  if (moveInfos.length > 0) {
    // 最佳走子是 moveInfos[0]（order 0）
    const bestMoveInfo = moveInfos[0];
    aiBestMove = bestMoveInfo.move;
    pv = bestMoveInfo.pv || [];

    // 如果知道實際走的走子，計算 score_loss
    if (nextMove && nextMoveColor) {
      playedMove = nextMove;

      // 在 moveInfos 中找到實際走的走子
      const playedMoveInfo = moveInfos.find((m) => m.move === nextMove);

      if (playedMoveInfo) {
        // score_loss = 最佳走子的 scoreLead - 實際走子的 scoreLead
        // 注意：scoreLead 是從當前玩家視角
        const bestScore = bestMoveInfo.scoreLead ?? 0;
        const playedScore = playedMoveInfo.scoreLead ?? 0;

        // 計算 score_loss（從當前玩家視角）
        if (currentPlayer === 'B') {
          scoreLoss = bestScore - playedScore;
        } else {
          // 對於 W，scoreLead 的正負號相反
          scoreLoss = -bestScore - -playedScore;
        }

        // 確保 score_loss 為正數（損失應該是正數）
        scoreLoss = Math.abs(scoreLoss);
      } else {
        // 如果找不到實際走的走子，使用 nextScoreGain 來估算
        if (response.nextScoreGain !== undefined) {
          scoreLoss = Math.abs(response.nextScoreGain);
        }
      }
    }
  }

  return {
    move: moveNumber,
    color: nextMoveColor || currentPlayer,
    played: playedMove,
    ai_best: aiBestMove,
    pv: pv,
    winrate_before: parseFloat(winrateBeforePercent.toFixed(1)),
    winrate_after:
      winrateAfter !== null ? parseFloat(winrateAfter.toFixed(1)) : null,
    score_loss: scoreLoss !== null ? parseFloat(scoreLoss.toFixed(1)) : null
  };
}

/**
 * 將 JSONL 數據轉換為包含統計信息的格式
 * 每個響應都會被轉換為類似 SGF 註釋的統計信息
 *
 * @param {Array} jsonlData - JSONL 解析後的數據數組
 * @returns {Array} 包含統計信息的對象數組
 */
export function convertJsonlToMoveStats(jsonlData) {
  if (!Array.isArray(jsonlData)) {
    return [];
  }

  return jsonlData
    .map((response) => extractMoveStats(response))
    .filter((stats) => stats !== null);
}

/**
 * 將 JSONL 文件轉換為包含統計信息的格式
 *
 * @param {string} filePath - JSONL 文件路徑
 * @returns {Promise<Object>} 包含 filename, totalLines, moves 的對象
 */
export async function convertJsonlToMoveStatsFile(filePath) {
  try {
    const data = await readJsonlFile(filePath);
    const filename = filePath.split('/').pop() || filePath.split('\\').pop();
    const moves = convertJsonlToMoveStats(data);

    return {
      filename,
      totalLines: data.length,
      moves
    };
  } catch (error) {
    console.error(`Error converting JSONL to move stats:`, error);
    throw error;
  }
}

/**
 * 執行 KataGo 分析腳本
 *
 * @param {string} sgfPath - SGF 文件路徑（可以是相對或絕對路徑）
 * @param {Object} options - 選項
 * @param {number} options.visits - 搜索次數（可選）
 * @param {Function} options.onProgress - 進度回調函數（可選）
 * @returns {Promise<Object>} 包含分析結果的對象
 */
export async function runKataGoAnalysis(sgfPath, options = {}) {
  const { visits, onProgress } = options;

  // 獲取當前文件所在目錄
  const __filename = fileURLToPath(import.meta.url);
  const __dirname = dirname(__filename);
  // 項目根目錄
  const projectRoot = resolve(__dirname, '../..');
  // katago 目錄
  const katagoDir = join(projectRoot, 'katago');
  // analysis.js 路徑
  const analysisScript = join(katagoDir, 'analysis.js');

  // 解析 SGF 文件路徑
  function resolveSgfPath(path) {
    if (isAbsolute(path)) {
      return path;
    }

    const possiblePaths = [
      resolve(process.cwd(), path),
      resolve(projectRoot, path),
      resolve(katagoDir, path)
    ];

    for (const p of possiblePaths) {
      if (existsSync(p)) {
        return p;
      }
    }

    return resolve(projectRoot, path);
  }

  const resolvedSgfPath = resolveSgfPath(sgfPath);

  // 檢查 SGF 文件是否存在
  if (!existsSync(resolvedSgfPath)) {
    throw new Error(
      `SGF file not found: ${sgfPath}\nResolved to: ${resolvedSgfPath}`
    );
  }

  // 檢查 analysis.js 是否存在
  if (!existsSync(analysisScript)) {
    throw new Error(`Analysis script not found: ${analysisScript}`);
  }

  return new Promise((resolve, reject) => {
    // 生成時間戳（年月日時分）用於輸出文件名
    const now = new Date();
    const year = now.getFullYear();
    const month = String(now.getMonth() + 1).padStart(2, '0');
    const day = String(now.getDate()).padStart(2, '0');
    const hour = String(now.getHours()).padStart(2, '0');
    const minute = String(now.getMinutes()).padStart(2, '0');
    const timestamp = `${year}${month}${day}${hour}${minute}`;

    // 構建輸出文件名（與 analysis.sh 的格式一致）
    const sgfBasename = resolvedSgfPath
      .split('/')
      .pop()
      .replace(/\.sgf$/, '');
    const resultsDir = join(katagoDir, 'results');
    const outputJsonl = join(
      resultsDir,
      `${sgfBasename}_analysis_${timestamp}_${visits}.jsonl`
    );

    // 構建參數
    const args = [resolvedSgfPath];
    if (visits) {
      args.push(visits.toString());
    }

    // 構建環境變量（傳遞輸出文件名）
    const env = { ...process.env };
    env.OUTPUT_JSONL = outputJsonl;

    // 執行分析腳本
    const script = spawn('node', [analysisScript, ...args], {
      cwd: projectRoot,
      env: env, // 傳遞環境變量（包含 OUTPUT_JSONL）
      stdio: ['inherit', 'pipe', 'pipe'] // stdin 繼承，stdout/stderr 捕獲
    });

    let stdout = '';
    let stderr = '';

    // 捕獲 stdout
    script.stdout.on('data', (data) => {
      const output = data.toString();
      stdout += output;
      if (onProgress) {
        onProgress(output);
      } else {
        // 如果沒有進度回調，直接輸出到控制台
        process.stdout.write(output);
      }
    });

    // 捕獲 stderr
    script.stderr.on('data', (data) => {
      const output = data.toString();
      stderr += output;
      if (onProgress) {
        onProgress(output);
      } else {
        // 如果沒有進度回調，直接輸出到控制台
        process.stderr.write(output);
      }
    });

    // 監聽結束事件
    script.on('close', async (code) => {
      if (code === 0) {
        // 分析成功，使用預先定義的輸出文件路徑
        const jsonlPath = outputJsonl;

        let moveStats = null;
        let jsonPath = null;

        // 如果 JSONL 文件存在，自動轉換為統計信息 JSON
        if (existsSync(jsonlPath)) {
          try {
            moveStats = await convertJsonlToMoveStatsFile(jsonlPath);

            // 將 moveStats 保存為 JSON 文件（文件名加上時間戳）
            // 例如：sample-original_analysis_202401011230.json
            const jsonlBasename = jsonlPath
              .split('/')
              .pop()
              .replace(/\.jsonl$/, '');
            const jsonDir = dirname(jsonlPath);
            jsonPath = join(jsonDir, `${jsonlBasename}.json`);

            await writeFile(
              jsonPath,
              JSON.stringify(moveStats, null, 2),
              'utf-8'
            );

            console.log(`Move stats JSON saved: ${jsonPath}`);
          } catch (error) {
            console.error(
              'Warning: Failed to convert JSONL to move stats or save JSON file:',
              error
            );
            // 不阻止成功返回，只是記錄警告
          }
        }

        resolve({
          success: true,
          sgfPath: resolvedSgfPath,
          jsonlPath: existsSync(jsonlPath) ? jsonlPath : null,
          jsonPath, // 新增：保存的 JSON 文件路徑
          moveStats, // 包含轉換後的統計信息
          stdout,
          stderr
        });
      } else {
        reject(new Error(`Analysis failed with exit code ${code}\n${stderr}`));
      }
    });

    script.on('error', (error) => {
      reject(new Error(`Failed to start analysis script: ${error.message}`));
    });
  });
}
