import compute from '@google-cloud/compute';
import { config } from '../config.js';
import { v4 as uuidv4 } from 'uuid';

const instancesClient = new compute.InstancesClient({
  projectId: config.gcp.projectId,
  keyFilename: config.gcp.serviceAccountKeyPath
});

/**
 * 建立 Preemptible VM 實例
 * @param {string} instanceName - VM 實例名稱
 * @param {string} sgfGcsPath - SGF 檔案在 GCS 的路徑
 * @returns {Promise<Object>} VM 實例資訊
 */
export async function createPreemptibleVM(instanceName, sgfGcsPath) {
  // 建立啟動腳本（從 GCS 下載 SGF 並執行分析）
  const startupScript = `
#!/bin/bash
set -e

# 安裝 gcloud CLI（如果尚未安裝）
if ! command -v gsutil &> /dev/null; then
  echo "Installing gcloud CLI..."
  curl https://sdk.cloud.google.com | bash
  exec -l $SHELL
fi

# 下載 SGF 檔案
echo "Downloading SGF file from ${sgfGcsPath}..."
gsutil cp ${sgfGcsPath} /tmp/input.sgf

# 執行 KataGo 分析
echo "Running KataGo analysis..."
${config.katago.scriptPath} /tmp/input.sgf /tmp/result.txt

# 上傳結果至 GCS
echo "Uploading results..."
gsutil cp /tmp/result.txt gs://${config.storage.bucketName}/results/${instanceName}/result.txt

# 標記任務完成
echo "Task completed" > /tmp/status.txt
gsutil cp /tmp/status.txt gs://${config.storage.bucketName}/results/${instanceName}/status.txt

# 關閉 VM（分析完成後自動關閉）
sudo shutdown -h +1
`;

  const instanceResource = {
    name: instanceName,
    machineType: `zones/${config.gcp.zone}/machineTypes/${config.vm.machineType}`,
    disks: [
      {
        boot: true,
        autoDelete: true,
        initializeParams: {
          sourceImage: `projects/${config.vm.imageProject}/global/images/family/${config.vm.imageFamily}`,
          diskSizeGb: config.vm.diskSizeGB
        }
      }
    ],
    metadata: {
      items: [
        {
          key: 'startup-script',
          value: startupScript
        }
      ]
    },
    scheduling: {
      preemptible: config.vm.preemptible,
      onHostMaintenance: 'TERMINATE',
      automaticRestart: false
    },
    serviceAccounts: [
      {
        email: 'default',
        scopes: [
          'https://www.googleapis.com/auth/cloud-platform',
          'https://www.googleapis.com/auth/devstorage.read_write'
        ]
      }
    ],
    tags: {
      items: ['katago-worker']
    },
    networkInterfaces: [
      {
        network: 'global/networks/default',
        accessConfigs: [
          {
            name: 'External NAT',
            type: 'ONE_TO_ONE_NAT'
          }
        ]
      }
    ]
  };

  const [response] = await instancesClient.insert({
    project: config.gcp.projectId,
    zone: config.gcp.zone,
    instanceResource
  });

  // 等待操作完成
  const operationsClient = new compute.ZoneOperationsClient({
    projectId: config.gcp.projectId,
    keyFilename: config.gcp.serviceAccountKeyPath
  });

  let operation = response.latestResponse;
  while (operation.status !== 'DONE') {
    [operation] = await operationsClient.wait({
      operation: operation.name,
      project: config.gcp.projectId,
      zone: config.gcp.zone
    });
  }

  return {
    name: instanceName,
    zone: config.gcp.zone,
    status: 'PROVISIONING',
    sgfPath: sgfGcsPath
  };
}

/**
 * 取得 VM 狀態
 * @param {string} instanceName - VM 實例名稱
 * @returns {Promise<Object>} VM 狀態資訊
 */
export async function getVMStatus(instanceName) {
  try {
    const [instance] = await instancesClient.get({
      project: config.gcp.projectId,
      zone: config.gcp.zone,
      instance: instanceName
    });

    return {
      exists: true,
      status: instance.status,
      name: instance.name,
      zone: config.gcp.zone
    };
  } catch (error) {
    if (error.code === 404 || error.code === 5) {
      return { exists: false };
    }
    throw error;
  }
}

/**
 * 刪除 VM 實例
 * @param {string} instanceName - VM 實例名稱
 * @returns {Promise<void>}
 */
export async function deleteVM(instanceName) {
  try {
    const [response] = await instancesClient.delete({
      project: config.gcp.projectId,
      zone: config.gcp.zone,
      instance: instanceName
    });

    // 等待操作完成
    const operationsClient = new compute.ZoneOperationsClient({
      projectId: config.gcp.projectId,
      keyFilename: config.gcp.serviceAccountKeyPath
    });

    let operation = response.latestResponse;
    while (operation.status !== 'DONE') {
      [operation] = await operationsClient.wait({
        operation: operation.name,
        project: config.gcp.projectId,
        zone: config.gcp.zone
      });
    }
  } catch (error) {
    // 如果 VM 不存在，忽略錯誤
    if (error.code !== 404 && error.code !== 5) {
      throw error;
    }
  }
}

/**
 * 產生唯一的 VM 實例名稱
 * @returns {string}
 */
export function generateInstanceName() {
  const timestamp = Date.now();
  const uuid = uuidv4().split('-')[0];
  return `${config.vm.instanceNamePrefix}-${timestamp}-${uuid}`;
}

/**
 * 等待 VM 啟動完成
 * @param {string} instanceName - VM 實例名稱
 * @param {number} maxWaitTime - 最大等待時間（毫秒）
 * @returns {Promise<boolean>} 是否成功啟動
 */
export async function waitForVMReady(instanceName, maxWaitTime = 60000) {
  const startTime = Date.now();

  while (Date.now() - startTime < maxWaitTime) {
    const status = await getVMStatus(instanceName);

    if (status.exists && status.status === 'RUNNING') {
      return true;
    }

    if (status.exists && status.status === 'TERMINATED') {
      return false;
    }

    // 等待 2 秒後再檢查
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }

  return false;
}
