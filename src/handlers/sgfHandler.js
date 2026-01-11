import sgf from '@sabaki/sgf';

/**
 * 將 SGF 座標轉換為標準格式 (如 "pd" -> "F14")
 * @param {string} coord - SGF 座標 (如 "pd", "cp")
 * @returns {string} 標準座標 (如 "F14", "C16")
 */
function sgfCoordToStandard(coord) {
  if (!coord || coord.length !== 2) return null;

  const x = coord.charCodeAt(0) - 97; // a=0, b=1, ..., z=25
  const y = coord.charCodeAt(1) - 97;

  // 轉換為標準格式：A=0, B=1, ..., H=7, J=8, K=9, ..., T=18 (19路棋盤)
  // 注意：圍棋座標系統跳過 I（因為 I 和 1 容易混淆）
  let letter;
  if (x < 8) {
    // A-H (0-7)
    letter = String.fromCharCode(65 + x); // A-H
  } else {
    // J-T (8-18)，跳過 I
    letter = String.fromCharCode(66 + x); // J-T (因為跳過 I，所以 +66 而不是 +65)
  }

  const number = 19 - y; // 從上到下是 1-19

  return `${letter}${number}`;
}

/**
 * 解析英文 AI 註解
 * @param {string} comment - 註解內容
 * @returns {object} 解析後的 AI 資訊
 */
function parseAIComment(comment) {
  if (!comment) return {};

  const result = {};

  // 解析手數、顏色和實際下的位置
  // 格式：Move 49: B F14
  const moveMatch = comment.match(/Move\s*(\d+):\s*([BW])\s+([A-T]\d+)/);
  if (moveMatch) {
    result.move = parseInt(moveMatch[1]);
    result.color = moveMatch[2];
    result.played = moveMatch[3];
  }

  // 解析勝率（下之後）
  // 格式：Win rate: B 59.4% 或 Win rate: W 60.5%
  const winrateMatch = comment.match(/Win rate:\s*[BW]\s*([\d.]+)%/);
  if (winrateMatch) {
    result.winrate_after = parseFloat(winrateMatch[1]);
  }

  // 解析目數損失
  // 格式：Estimated point loss: 2.2
  const scoreLossMatch = comment.match(/Estimated point loss:\s*([\d.]+)/);
  if (scoreLossMatch) {
    result.score_loss = parseFloat(scoreLossMatch[1]);
  }

  // 解析最佳選點
  // 格式：Predicted top move was K15 (B+3.4).
  const aiBestMatch = comment.match(/Predicted top move was\s+([A-T]\d+)/);
  if (aiBestMatch) {
    result.ai_best = aiBestMatch[1];
  }

  // 解析 PV (變化圖)
  // 格式：PV: BK15 L15 K14 J16 C13 或 PV: BF11 F12 D11 B12 C13 C14 H12
  // 注意：只有第一個座標可能有 B/W 前綴，後續座標中的 B/W 是列名，不應移除
  // PV 後面可能跟著其他內容（如 "Move was #123"），需要限制匹配範圍
  const pvMatch = comment.match(/PV:\s*([BW]?[A-T]\d+(?:\s+[A-T]\d+)*)/);
  if (pvMatch) {
    const pvStr = pvMatch[1].trim();
    const coords = pvStr.split(/\s+/);

    result.pv = coords
      .map((coord, index) => {
        // 只有第一個座標需要移除 B/W 前綴
        if (index === 0) {
          // 移除第一個座標的 B/W 前綴
          return coord.replace(/^[BW]/, '');
        }
        // 後續座標保持原樣（B/W 是列名的一部分）
        return coord;
      })
      .filter((coord) => coord.length > 0 && /^[A-T]\d+$/.test(coord));
  }

  return result;
}

/**
 * 從 SGF 樹結構中提取每一步的資訊
 * @param {Array} tree - @sabaki/sgf 解析後的樹結構
 * @returns {Array} 每一步的資訊
 */
function extractMoves(tree) {
  const moves = [];
  let moveNumber = 0;
  let previousWinrate = null;

  /**
   * 遞迴遍歷樹結構
   * @sabaki/sgf 的結構通常是：根節點是數組，每個元素是一個節點對象
   * 節點對象包含屬性（B, W, C 等）和可能的子節點
   */
  function traverse(nodes) {
    if (!nodes || !Array.isArray(nodes)) return;

    for (let i = 0; i < nodes.length; i++) {
      const node = nodes[i];
      if (typeof node !== 'object' || node === null) continue;

      let currentMove = null;
      let currentComment = null;

      // 檢查是否有走子（B 或 W）
      // @sabaki/sgf 的結構：node.data 包含屬性
      const nodeData = node.data || node;

      if (nodeData.B) {
        moveNumber++;
        const coord = Array.isArray(nodeData.B) ? nodeData.B[0] : nodeData.B;
        currentMove = {
          move: moveNumber,
          color: 'B',
          played: sgfCoordToStandard(coord),
          ai_best: null,
          pv: [],
          winrate_before: previousWinrate,
          winrate_after: null,
          score_loss: null
        };
      } else if (nodeData.W) {
        moveNumber++;
        const coord = Array.isArray(nodeData.W) ? nodeData.W[0] : nodeData.W;
        currentMove = {
          move: moveNumber,
          color: 'W',
          played: sgfCoordToStandard(coord),
          ai_best: null,
          pv: [],
          winrate_before: previousWinrate,
          winrate_after: null,
          score_loss: null
        };
      }

      // 檢查是否有註解（C）- 註解可能在同一個節點，也可能在下一個節點
      if (nodeData.C) {
        const comment = Array.isArray(nodeData.C) ? nodeData.C[0] : nodeData.C;
        currentComment = parseAIComment(comment);
      }

      // 如果有走子，合併註解資訊
      if (currentMove) {
        if (currentComment) {
          // 從註解中提取資訊（優先使用註解中的資訊，因為更準確）
          if (currentComment.played) {
            currentMove.played = currentComment.played;
          }
          if (currentComment.color) {
            currentMove.color = currentComment.color;
          }
          if (currentComment.ai_best) {
            currentMove.ai_best = currentComment.ai_best;
          }
          if (currentComment.pv && currentComment.pv.length > 0) {
            currentMove.pv = currentComment.pv;
          }
          if (currentComment.winrate_after !== undefined) {
            currentMove.winrate_after = currentComment.winrate_after;
            // 更新 previousWinrate 供下一步使用
            previousWinrate = currentComment.winrate_after;
          }
          if (currentComment.score_loss !== undefined) {
            currentMove.score_loss = currentComment.score_loss;
          }
        }

        moves.push(currentMove);
      }

      // 處理子節點 - @sabaki/sgf 的子節點通常作為節點對象的屬性
      // 遍歷節點的所有屬性，尋找可能是子節點的數組
      for (const key in node) {
        if (key === 'B' || key === 'W' || key === 'C') continue; // 跳過已處理的屬性

        const value = node[key];
        if (Array.isArray(value) && value.length > 0) {
          // 可能是子節點數組，遞迴處理
          traverse(value);
        }
      }
    }
  }

  traverse(tree);
  return moves;
}

/**
 * 解析 SGF 檔案內容
 * @param {string} sgfContent - SGF 檔案內容
 * @returns {object} 解析後的結果
 */
export function parseSGF(sgfContent) {
  try {
    // 使用 @sabaki/sgf 解析
    const tree = sgf.parse(sgfContent);

    // 提取遊戲資訊
    // const gameInfo = {};
    // if (tree && tree.length > 0 && tree[0]) {
    //   const root = tree[0];
    //   if (typeof root === 'object') {
    //     // 提取根節點的屬性
    //     [
    //       'FF',
    //       'GM',
    //       'SZ',
    //       'RU',
    //       'CA',
    //       'AP',
    //       'PB',
    //       'PW',
    //       'DT',
    //       'RE',
    //       'KM'
    //     ].forEach((key) => {
    //       if (root[key]) {
    //         gameInfo[key] = Array.isArray(root[key]) ? root[key][0] : root[key];
    //       }
    //     });
    //   }
    // }

    // 提取每一步的資訊
    const moves = extractMoves(tree);

    return {
      moves,
      totalMoves: moves.length
    };
  } catch (error) {
    throw new Error(`Failed to parse SGF: ${error.message}`);
  }
}

/**
 * 篩選關鍵手（score_loss 大於指定閾值的走子）
 * @param {Array} moves - 走子列表
 * @param {number} threshold - 閾值，默認為 2.0
 * @returns {Array} 關鍵手列表
 */
export function filterCriticalMoves(moves, threshold = 2.0) {
  if (!moves || !Array.isArray(moves)) {
    return [];
  }

  return moves.filter((move) => {
    // 只返回 score_loss 存在且大於閾值的走子
    return (
      move.score_loss !== null &&
      move.score_loss !== undefined &&
      move.score_loss > threshold
    );
  });
}

/**
 * 挑選出 score_loss 最大的前 N 手（避免關鍵點過多）
 * @param {Array} moves - 走子列表
 * @param {number} topN - 返回前 N 手，默認為 20
 * @returns {Array} score_loss 最大的前 N 手列表（按手數排序）
 */
export function getTopScoreLossMoves(moves, topN = 20) {
  if (!moves || !Array.isArray(moves)) {
    return [];
  }

  // 過濾出有 score_loss 的走子
  const movesWithScoreLoss = moves.filter(
    (move) =>
      move.score_loss !== null &&
      move.score_loss !== undefined &&
      typeof move.score_loss === 'number'
  );

  // 按 score_loss 降序排序
  const sortedByScoreLoss = movesWithScoreLoss.sort(
    (a, b) => b.score_loss - a.score_loss
  );

  // 取前 topN 手
  const topMoves = sortedByScoreLoss.slice(0, topN);

  // 最後按手數（move）升序排序
  return topMoves.sort((a, b) => a.move - b.move);
}
