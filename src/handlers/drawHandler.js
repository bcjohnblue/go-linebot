import { spawn } from 'child_process';
import { fileURLToPath } from 'url';
import { dirname, join, resolve } from 'path';
import { existsSync, mkdirSync } from 'fs';
import { readFile, writeFile } from 'fs/promises';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const projectRoot = resolve(__dirname, '../..');
const drawDir = join(projectRoot, 'draw');
const venvPython = join(drawDir, 'venv', 'bin', 'python3');

/**
 * 调用 Python 脚本绘制所有 topScoreLossMoves 的 GIF
 *
 * @param {string} jsonFilePath - JSON 文件路径（包含 moves 数据）
 * @param {string} outputDir - 输出目录
 * @returns {Promise<Array<string>>} 生成的 GIF 文件路径数组
 */
export async function drawAllMovesGif(jsonFilePath, outputDir = null) {
  // 默认输出目录
  if (!outputDir) {
    outputDir = join(projectRoot, 'draw', 'output');
  }

  // 确保输出目录存在
  if (!existsSync(outputDir)) {
    mkdirSync(outputDir, { recursive: true });
  }

  // 提取并输出文件夹名称
  const dirName = outputDir.split('/').pop() || outputDir.split('\\').pop();
  console.log(`Drawing all moves GIFs to outputDir: ${dirName}`);

  // Python 脚本路径
  const pythonScript = join(drawDir, 'draw.py');

  if (!existsSync(pythonScript)) {
    throw new Error(`Python script not found: ${pythonScript}`);
  }

  if (!existsSync(jsonFilePath)) {
    throw new Error(`JSON file not found: ${jsonFilePath}`);
  }

  // 检查虚拟环境是否存在
  if (!existsSync(venvPython)) {
    throw new Error(
      `Virtual environment not found: ${venvPython}\nPlease create it with: python3 -m venv ${join(
        drawDir,
        'venv'
      )} && ${venvPython} -m pip install -r ${join(
        drawDir,
        'requirements.txt'
      )}`
    );
  }

  return new Promise((resolve, reject) => {
    // 使用虚拟环境中的 Python 解释器调用脚本
    const python = spawn(venvPython, [pythonScript, jsonFilePath, outputDir], {
      cwd: projectRoot,
      stdio: ['inherit', 'pipe', 'pipe']
    });

    let stdout = '';
    let stderr = '';

    python.stdout.on('data', (data) => {
      const output = data.toString();
      stdout += output;
      // 实时输出到 shell，只显示文件名
      const lines = output.split('\n');
      lines.forEach((line) => {
        const match = line.match(/GIF created: (.+)/);
        if (match) {
          const fullPath = match[1];
          const filename =
            fullPath.split('/').pop() || fullPath.split('\\').pop();
          process.stdout.write(`GIF created: ${filename}\n`);
        } else if (line.trim()) {
          // 其他输出原样显示
          process.stdout.write(line + '\n');
        }
      });
    });

    python.stderr.on('data', (data) => {
      const output = data.toString();
      stderr += output;
      // 实时输出到 shell
      process.stderr.write(output);
    });

    python.on('close', (code) => {
      if (code === 0) {
        // 从 stdout 中提取所有生成的 GIF 路径
        const gifMatches = stdout.matchAll(/GIF created: (.+)/g);
        const gifPaths = Array.from(gifMatches, (match) => match[1]);
        resolve(gifPaths);
      } else {
        reject(new Error(`Python script failed with code ${code}\n${stderr}`));
      }
    });

    python.on('error', (error) => {
      reject(new Error(`Failed to start Python script: ${error.message}`));
    });
  });
}
