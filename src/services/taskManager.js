import { generateInstanceName, createPreemptibleVM, getVMStatus, deleteVM } from './vmManager.js';
import { uploadBuffer, fileExists, downloadFile } from './storage.js';
import { config } from '../config.js';
import { v4 as uuidv4 } from 'uuid';

/**
 * 任務狀態
 */
export const TaskStatus = {
  PENDING: 'PENDING',
  VM_CREATING: 'VM_CREATING',
  VM_RUNNING: 'VM_RUNNING',
  ANALYZING: 'ANALYZING',
  COMPLETED: 'COMPLETED',
  FAILED: 'FAILED',
  INTERRUPTED: 'INTERRUPTED',
};

// 記憶體中的任務狀態（生產環境建議使用 Redis 或資料庫）
const tasks = new Map();

/**
 * 建立新的分析任務
 * @param {string} userId - LINE 用戶 ID
 * @param {Buffer} sgfBuffer - SGF 檔案內容
 * @param {string} fileName - 檔案名稱
 * @returns {Promise<Object>} 任務資訊
 */
export async function createTask(userId, sgfBuffer, fileName) {
  const taskId = uuidv4();
  const timestamp = Date.now();
  
  // 上傳 SGF 檔案至 GCS
  const sgfPath = `sgf/${taskId}/${fileName}`;
  const sgfGcsPath = await uploadBuffer(sgfBuffer, sgfPath);

  // 建立任務記錄
  const task = {
    taskId,
    userId,
    fileName,
    sgfPath: sgfGcsPath,
    status: TaskStatus.PENDING,
    createdAt: timestamp,
    updatedAt: timestamp,
    instanceName: null,
    resultPath: null,
    error: null,
  };

  tasks.set(taskId, task);

  // 非同步啟動 VM 並執行分析
  startAnalysis(taskId).catch(error => {
    console.error(`Task ${taskId} failed:`, error);
    updateTaskStatus(taskId, TaskStatus.FAILED, { error: error.message });
  });

  return task;
}

/**
 * 啟動分析流程
 * @param {string} taskId - 任務 ID
 * @returns {Promise<void>}
 */
async function startAnalysis(taskId) {
  const task = tasks.get(taskId);
  if (!task) {
    throw new Error(`Task ${taskId} not found`);
  }

  try {
    // 產生 VM 實例名稱
    const instanceName = generateInstanceName();
    updateTaskStatus(taskId, TaskStatus.VM_CREATING, { instanceName });

    // 建立 Preemptible VM
    await createPreemptibleVM(instanceName, task.sgfPath);
    updateTaskStatus(taskId, TaskStatus.VM_RUNNING);

    // 開始監控任務狀態
    monitorTask(taskId, instanceName);
  } catch (error) {
    console.error(`Failed to start analysis for task ${taskId}:`, error);
    updateTaskStatus(taskId, TaskStatus.FAILED, { error: error.message });
    throw error;
  }
}

/**
 * 監控任務狀態
 * @param {string} taskId - 任務 ID
 * @param {string} instanceName - VM 實例名稱
 */
async function monitorTask(taskId, instanceName) {
  const maxWaitTime = 10 * 60 * 1000; // 10 分鐘
  const checkInterval = 5000; // 每 5 秒檢查一次
  const startTime = Date.now();

  const checkStatus = async () => {
    const task = tasks.get(taskId);
    if (!task) return;

    // 檢查是否超時
    if (Date.now() - startTime > maxWaitTime) {
      updateTaskStatus(taskId, TaskStatus.FAILED, {
        error: 'Task timeout',
      });
      await deleteVM(instanceName);
      return;
    }

    // 檢查結果檔案是否存在
    const resultPath = `results/${instanceName}/result.txt`;
    const statusPath = `results/${instanceName}/status.txt`;

    try {
      const resultExists = await fileExists(resultPath);
      const statusExists = await fileExists(statusPath);

      if (resultExists && statusExists) {
        // 任務完成
        updateTaskStatus(taskId, TaskStatus.COMPLETED, {
          resultPath: `gs://${config.storage.bucketName}/${resultPath}`,
        });
        
        // 清理 VM（如果還在運行）
        setTimeout(() => deleteVM(instanceName), 30000); // 30 秒後刪除
        return;
      }

      // 檢查 VM 狀態
      const vmStatus = await getVMStatus(instanceName);
      
      if (!vmStatus.exists || vmStatus.status === 'TERMINATED') {
        // VM 被中斷或已關閉
        if (resultExists) {
          // 有結果，可能是正常完成
          updateTaskStatus(taskId, TaskStatus.COMPLETED, {
            resultPath: `gs://${config.storage.bucketName}/${resultPath}`,
          });
        } else {
          // 沒有結果，可能是被中斷
          updateTaskStatus(taskId, TaskStatus.INTERRUPTED, {
            error: 'VM was preempted or terminated',
          });
          
          // 重試（最多重試 3 次）
          const retryCount = (task.retryCount || 0) + 1;
          if (retryCount < 3) {
            console.log(`Retrying task ${taskId}, attempt ${retryCount}`);
            task.retryCount = retryCount;
            await startAnalysis(taskId);
            return;
          }
        }
        return;
      }

      if (vmStatus.status === 'RUNNING') {
        updateTaskStatus(taskId, TaskStatus.ANALYZING);
      }

      // 繼續監控
      setTimeout(checkStatus, checkInterval);
    } catch (error) {
      console.error(`Error monitoring task ${taskId}:`, error);
      setTimeout(checkStatus, checkInterval);
    }
  };

  // 等待 VM 啟動後開始監控
  setTimeout(checkStatus, 10000); // 10 秒後開始檢查
}

/**
 * 更新任務狀態
 * @param {string} taskId - 任務 ID
 * @param {string} status - 新狀態
 * @param {Object} updates - 其他更新欄位
 */
function updateTaskStatus(taskId, status, updates = {}) {
  const task = tasks.get(taskId);
  if (task) {
    Object.assign(task, {
      status,
      updatedAt: Date.now(),
      ...updates,
    });
    tasks.set(taskId, task);
  }
}

/**
 * 取得任務資訊
 * @param {string} taskId - 任務 ID
 * @returns {Object|null}
 */
export function getTask(taskId) {
  return tasks.get(taskId) || null;
}

/**
 * 取得用戶的所有任務
 * @param {string} userId - LINE 用戶 ID
 * @returns {Array}
 */
export function getUserTasks(userId) {
  return Array.from(tasks.values())
    .filter(task => task.userId === userId)
    .sort((a, b) => b.createdAt - a.createdAt);
}

/**
 * 取得任務結果
 * @param {string} taskId - 任務 ID
 * @returns {Promise<Buffer|null>}
 */
export async function getTaskResult(taskId) {
  const task = tasks.get(taskId);
  if (!task || !task.resultPath) {
    return null;
  }

  // 從 GCS 路徑中提取檔案路徑
  const match = task.resultPath.match(/gs:\/\/[^/]+\/(.+)/);
  if (!match) {
    return null;
  }

  const remotePath = match[1];
  return await downloadFile(remotePath);
}

