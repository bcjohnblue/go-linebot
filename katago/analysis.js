import { spawn } from 'child_process';
import { fileURLToPath } from 'url';
import { dirname, join, resolve, isAbsolute } from 'path';
import { existsSync } from 'fs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
// 項目根目錄（katago 的父目錄）
const projectRoot = resolve(__dirname, '..');

function resolveSgfPath(sgfPath) {
  // 如果是絕對路徑，直接返回
  if (isAbsolute(sgfPath)) {
    return sgfPath;
  }
  
  // 如果是相對路徑，嘗試多種可能的位置
  const possiblePaths = [
    resolve(process.cwd(), sgfPath), // 相對於當前工作目錄
    resolve(projectRoot, sgfPath),    // 相對於項目根目錄
    resolve(__dirname, sgfPath),     // 相對於 katago 目錄
  ];
  
  // 找到第一個存在的路徑
  for (const path of possiblePaths) {
    if (existsSync(path)) {
      return path;
    }
  }
  
  // 如果都找不到，返回相對於項目根目錄的絕對路徑（讓腳本處理錯誤）
  return resolve(projectRoot, sgfPath);
}

function runAnalysisScript(sgfPath, visits = null, ...additionalArgs) {
  return new Promise((resolve, reject) => {
    // 解析 SGF 文件路徑
    const resolvedSgfPath = resolveSgfPath(sgfPath);
    
    // 檢查文件是否存在
    if (!existsSync(resolvedSgfPath)) {
      reject(new Error(`SGF file not found: ${sgfPath}\nResolved to: ${resolvedSgfPath}`));
      return;
    }
    
    // 構建腳本路徑
    const scriptPath = join(__dirname, 'scripts', 'analysis.sh');
    
    // 構建參數：第一個是解析後的 SGF 文件路徑（使用絕對路徑）
    const args = [resolvedSgfPath];
    
    // 構建環境變量（如果提供了 visits，設置 VISITS 環境變量）
    // 同時傳遞 OUTPUT_JSONL（如果存在於環境變量中）
    const env = { ...process.env };
    if (visits !== null && visits !== undefined) {
      env.VISITS = visits.toString();
    }
    // OUTPUT_JSONL 會從父進程的環境變量中繼承

    // 執行腳本並傳遞參數
    const script = spawn('bash', [scriptPath, ...args], {
      cwd: __dirname,
      env: env, // 傳遞環境變量（包含 VISITS 和 OUTPUT_JSONL）
      stdio: 'inherit' // 直接繼承 stdin/stdout/stderr，讓輸出即時顯示
    });

    // 監聽結束事件
    script.on('close', (code) => {
      if (code === 0) {
        console.log('分析完成！');
        resolve();
      } else {
        reject(new Error(`腳本異常結束，代碼: ${code}`));
      }
    });

    script.on('error', (error) => {
      reject(new Error(`無法啟動腳本: ${error.message}`));
    });
  });
}

// 主函數
async function main() {
  // 獲取命令行參數
  const args = process.argv.slice(2);
  
  if (args.length === 0) {
    console.error('錯誤: 請提供 SGF 文件路徑');
    console.error('使用方式: npm run node:analysis <sgf_file_path> [additional_args...]');
    console.error('範例: npm run node:analysis ./static/example.sgf');
    process.exit(1);
  }

  const sgfPath = args[0];
  // 第二個參數可能是 visits（數字），否則作為額外參數
  let visits = null;
  let additionalArgs = [];
  
  if (args.length > 1) {
    const secondArg = args[1];
    // 檢查是否為數字（visits）
    if (/^\d+$/.test(secondArg)) {
      visits = parseInt(secondArg, 10);
      additionalArgs = args.slice(2);
    } else {
      additionalArgs = args.slice(1);
    }
  }

  try {
    await runAnalysisScript(sgfPath, visits, ...additionalArgs);
    console.log('現在可以處理接下來的事情了。');
  } catch (error) {
    console.error('執行失敗:', error.message);
    process.exit(1);
  }
}

main();
