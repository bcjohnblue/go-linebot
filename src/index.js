import express from 'express';
import { readFile, readdir } from 'fs/promises';
import { join } from 'path';
import { fileURLToPath } from 'url';
import { dirname } from 'path';
import { config } from './config.js';
// import { lineMiddleware } from './handlers/lineHandler.js';
import {
  handleTextMessage,
  handleFileMessage
} from './handlers/lineHandler.js';
import {
  parseSGF,
  filterCriticalMoves,
  getTopScoreLossMoves
} from './handlers/sgfHandler.js';
import {
  convertJsonlToJson,
  readJsonlFile,
  jsonlToJson,
  convertJsonlToMoveStatsFile,
  runKataGoAnalysis
} from './handlers/katagoHandler.js';
import { drawAllMovesGif } from './handlers/drawHandler.js';
import { callOpenAI } from './LLM/providers/openai.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const app = express();

// 解析 JSON body
app.use(express.json());

// 静态文件服务 - 提供 static 目录下的文件
app.use('/static', express.static(join(__dirname, '../static')));

// 静态文件服务 - 提供 draw/outputs 目录下的 GIF 文件
app.use('/draw/outputs', express.static(join(__dirname, '../draw/outputs')));

// LINE Webhook 驗證中間件
// app.use(config.server.webhookPath, lineMiddleware);

// LINE Webhook 處理
app.post(config.server.webhookPath, async (req, res) => {
  try {
    const events = req.body.events || [];
    console.log('events', events);

    for (const event of events) {
      // 處理訊息事件（支援 1對1、群組、聊天室）
      if (event.type === 'message') {
        // 確保有 source 和對應的 ID
        const hasValidSource =
          (event.source?.type === 'user' && event.source?.userId) ||
          (event.source?.type === 'group' && event.source?.groupId) ||
          (event.source?.type === 'room' && event.source?.roomId);

        if (hasValidSource) {
          if (event.message.type === 'text') {
            await handleTextMessage(event);
          } else if (event.message.type === 'file') {
            await handleFileMessage(event);
          }
        }
      }
    }

    res.status(200).send('OK');
  } catch (error) {
    console.error('Webhook error:', error);
    res.status(500).send('Internal Server Error');
  }
});

// 健康檢查端點
app.get('/health', (req, res) => {
  res.status(200).json({
    status: 'ok',
    timestamp: new Date().toISOString()
  });
});

// GET /example/original 路由 - 讀取並解析 SGF 檔案
// app.get('/example/original', async (req, res) => {
//   try {
//     const staticDir = join(__dirname, '../static');

//     // 讀取 static 目錄下的所有檔案
//     const files = await readdir(staticDir);

//     // 找出所有 .sgf 檔案
//     const sgfFiles = files.filter((file) => file.endsWith('.sgf'));

//     if (sgfFiles.length === 0) {
//       return res.status(404).json({
//         error: 'No SGF files found in static directory'
//       });
//     }

//     // 讀取第一個 SGF 檔案（或可以讓用戶指定檔案名）
//     const sgfFile = sgfFiles[0];
//     const sgfPath = join(staticDir, sgfFile);
//     const sgfContent = await readFile(sgfPath, 'utf-8');

//     // 使用 sgfHandler 解析 SGF 內容
//     const parsedData = parseSGF(sgfContent);
//     const criticalMoves = filterCriticalMoves(parsedData.moves);
//     const topScoreLossMoves = getTopScoreLossMoves(criticalMoves);

//     // 回傳 JSON
//     res.json({
//       filename: sgfFile,
//       moves: topScoreLossMoves,
//       totalMoves: parsedData.totalMoves
//     });
//   } catch (error) {
//     console.error('Error reading/parsing SGF file:', error);
//     res.status(500).json({
//       error: 'Failed to read or parse SGF file',
//       message: error.message
//     });
//   }
// });

// GET /example/katago-comment 路由 - 讀取 example-katago-comment.sgf 並解析 SGF 檔案
app.get('/parse/sample-katrain', async (req, res) => {
  try {
    const staticDir = join(__dirname, '../static');

    // 讀取 static 目錄下的所有檔案
    const files = await readdir(staticDir);

    // 找出 katago-comment 開頭的檔案
    const katagoCommentFile = files.find(
      (file) => file.endsWith('.sgf') && file.includes('sample-katrain')
    );

    if (!katagoCommentFile) {
      return res.status(404).json({
        error: 'No katago-comment SGF file found in static directory'
      });
    }

    // 讀取 katago-comment SGF 檔案
    const sgfFile = katagoCommentFile;
    const sgfPath = join(staticDir, sgfFile);
    const sgfContent = await readFile(sgfPath, 'utf-8');

    // 使用 sgfHandler 解析 SGF 內容
    const parsedData = parseSGF(sgfContent);
    const criticalMoves = filterCriticalMoves(parsedData.moves);
    const topScoreLossMoves = getTopScoreLossMoves(criticalMoves);

    // 回傳 JSON
    res.json({
      filename: sgfFile,
      moves: topScoreLossMoves,
      totalMoves: parsedData.totalMoves
    });
  } catch (error) {
    console.error('Error reading/parsing SGF file:', error);
    res.status(500).json({
      error: 'Failed to read or parse SGF file',
      message: error.message
    });
  }
});

// GET /katago 路由 - 執行 KataGo 分析並返回統計信息
app.get('/katago', async (req, res) => {
  try {
    // 構建 example-original.sgf 文件路徑
    const staticDir = join(__dirname, '../static');
    const sgfPath = join(staticDir, 'sample-raw.sgf');

    // 執行 KataGo 分析
    console.log(`Starting KataGo analysis for: ${sgfPath}`);
    const result = await runKataGoAnalysis(sgfPath, {
      onProgress: (output) => {
        // 可選：實時輸出進度（如果需要）
        process.stdout.write(output);
      },
      visits: 200
    });

    // 檢查分析是否成功
    if (!result.success) {
      return res.status(500).json({
        error: 'KataGo analysis failed',
        message: result.stderr || 'Unknown error'
      });
    }

    // 檢查是否有 moveStats（轉換後的統計信息）
    if (!result.moveStats) {
      return res.status(500).json({
        error: 'Failed to convert JSONL to move stats',
        message: 'Analysis completed but move stats conversion failed'
      });
    }

    // 返回 moveStats
    res.json(result.moveStats);
  } catch (error) {
    console.error('Error in /katrain route:', error);
    res.status(500).json({
      error: 'Failed to run KataGo analysis',
      message: error.message
    });
  }
});

// GET /katago/results/:filename 路由 - 讀取 katago/results 下的 .json
app.get('/katago/results/:filename', async (req, res) => {
  try {
    const { filename } = req.params;
    const fileContent = await readFile(
      join(__dirname, '../katago/results', filename),
      'utf-8'
    );

    // 解析 JSON 字符串
    const result = JSON.parse(fileContent);

    const criticalMoves = filterCriticalMoves(result.moves);
    const topScoreLossMoves = getTopScoreLossMoves(criticalMoves);

    // 回傳 JSON
    res.json({
      filename,
      moves: topScoreLossMoves,
      totalMoves: result.moves.length
    });
  } catch (error) {
    console.error('Error reading result file:', error);
    res.status(500).json({
      error: 'Failed to read result file',
      message: error.message
    });
  }
});

app.get('/katago/draw/:filename', async (req, res) => {
  try {
    const { filename } = req.params;
    const jsonFilePath = join(__dirname, '../katago/results', filename);

    const fileContent = await readFile(jsonFilePath, 'utf-8');
    const result = JSON.parse(fileContent);

    const criticalMoves = filterCriticalMoves(result.moves);
    const topScoreLossMoves = getTopScoreLossMoves(criticalMoves);

    // 生成所有 GIF，outputDir 加上 filename 作为子文件夹
    const outputDir = join(
      __dirname,
      '../draw/outputs',
      filename.replace(/\.json$/, '')
    );
    const { drawAllMovesGif } = await import('./handlers/drawHandler.js');
    const gifPaths = await drawAllMovesGif(jsonFilePath, outputDir);

    // 回傳結果
    res.json({
      filename,
      moves: topScoreLossMoves,
      totalMoves: result.moves.length,
      gifs: gifPaths.map((path) => {
        // 返回相对路径，方便前端访问
        const relativePath = path.replace(join(__dirname, '..'), '');
        return relativePath.startsWith('/') ? relativePath : '/' + relativePath;
      })
    });
  } catch (error) {
    console.error('Error generating GIFs:', error);
    res.status(500).json({
      error: 'Failed to generate GIFs',
      message: error.message
    });
  }
});

// app.get('/katago/results/:filename', async (req, res) => {
//   try {
//     const { filename } = req.params;

//     // 確保檔案名以 .jsonl 結尾
//     const jsonlFilename = filename.endsWith('.jsonl')
//       ? filename
//       : `${filename}.jsonl`;

//     const resultsDir = join(__dirname, '../katago/results');
//     const jsonlPath = join(resultsDir, jsonlFilename);

//     // 使用 katagoHandler 的函數轉換 JSONL 為 JSON
//     const result = await convertJsonlToMoveStatsFile(jsonlPath);

//     // 回傳 JSON
//     res.json(result);
//   } catch (error) {
//     if (error.code === 'ENOENT') {
//       return res.status(404).json({
//         error: 'JSONL file not found',
//         message: error.message
//       });
//     }
//     console.error('Error reading/parsing JSONL file:', error);
//     res.status(500).json({
//       error: 'Failed to read or parse JSONL file',
//       message: error.message
//     });
//   }
// });

// GET /katago/results 路由 - 列出所有可用的 .jsonl 檔案
// app.get('/katago/results', async (req, res) => {
//   try {
//     const resultsDir = join(__dirname, '../katago/results');

//     // 讀取 results 目錄下的所有檔案
//     const files = await readdir(resultsDir);

//     // 找出所有 .jsonl 檔案
//     const jsonlFiles = files.filter((file) => file.endsWith('.jsonl'));

//     // 回傳檔案列表
//     res.json({
//       directory: 'katago/results',
//       files: jsonlFiles,
//       count: jsonlFiles.length
//     });
//   } catch (error) {
//     console.error('Error reading results directory:', error);
//     res.status(500).json({
//       error: 'Failed to read results directory',
//       message: error.message
//     });
//   }
// });

// app.get('/katago/result/:filename', async (req, res) => {
//   try {
//     const { filename } = req.params;
//     const result = await readFile(
//       join(__dirname, '../katago/results', filename),
//       'utf-8'
//     );

//     const criticalMoves = filterCriticalMoves(result.moves);
//     const topScoreLossMoves = getTopScoreLossMoves(criticalMoves);

//     // 回傳 JSON
//     res.json({
//       filename,
//       moves: topScoreLossMoves,
//       totalMoves: result.moves.length
//     });
//   } catch (error) {
//     console.error('Error reading result file:', error);
//     res.status(500).json({
//       error: 'Failed to read result file',
//       message: error.message
//     });
//   }
// });

// GET /llm/:filename 路由 - 读取 katago/results/*.json 并调用 OpenAI
app.get('/llm/:filename', async (req, res) => {
  try {
    const { filename } = req.params;
    const jsonFilePath = join(__dirname, '../katago/results', filename);

    // 读取 JSON 文件
    const fileContent = await readFile(jsonFilePath, 'utf-8');
    const katagoData = JSON.parse(fileContent);

    // 过滤关键手数
    const criticalMoves = filterCriticalMoves(katagoData.moves);
    const topScoreLossMoves = getTopScoreLossMoves(criticalMoves);

    // 导入并调用 OpenAI
    const response = await callOpenAI(topScoreLossMoves);

    // 返回结果
    res.json({
      filename,
      llmResponse: response
    });
  } catch (error) {
    console.error('Error calling OpenAI:', error);
    res.status(500).json({
      error: 'Failed to call OpenAI',
      message: error.message
    });
  }
});

// 錯誤處理
app.use((err, req, res, next) => {
  console.error('Unhandled error:', err);
  res.status(500).json({
    error: 'Internal Server Error',
    message: err.message
  });
});

// 啟動伺服器
app.listen(config.server.port, () => {
  console.log(`🚀 Server is running on port ${config.server.port}`);
  console.log(
    `📡 Webhook URL: http://localhost:${config.server.port}${config.server.webhookPath}`
  );
  console.log(`📋 Environment: ${process.env.NODE_ENV || 'development'}`);
});
