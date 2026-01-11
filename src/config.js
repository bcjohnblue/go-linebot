import dotenv from 'dotenv';

dotenv.config();

export const config = {
  // LINE Bot
  line: {
    channelAccessToken: process.env.LINE_CHANNEL_ACCESS_TOKEN,
    channelSecret: process.env.LINE_CHANNEL_SECRET,
  },

  // GCP
  gcp: {
    projectId: process.env.GCP_PROJECT_ID,
    zone: process.env.GCP_ZONE || 'asia-east1-a',
    serviceAccountKeyPath: process.env.GCP_SERVICE_ACCOUNT_KEY_PATH,
  },

  // VM Configuration
  vm: {
    instanceNamePrefix: process.env.VM_INSTANCE_NAME_PREFIX || 'katago-worker',
    machineType: process.env.VM_MACHINE_TYPE || 'n1-standard-2',
    imageProject: process.env.VM_IMAGE_PROJECT || 'ubuntu-os-cloud',
    imageFamily: process.env.VM_IMAGE_FAMILY || 'ubuntu-2204-lts',
    diskSizeGB: parseInt(process.env.VM_DISK_SIZE_GB || '20'),
    preemptible: true, // Always use preemptible
  },

  // Storage
  storage: {
    bucketName: process.env.GCS_BUCKET_NAME,
  },

  // KataGo
  katago: {
    scriptPath: process.env.KATAGO_SCRIPT_PATH || '/home/ubuntu/analyze.sh',
    resultPath: process.env.KATAGO_RESULT_PATH || '/home/ubuntu/result.txt',
  },

  // Server
  server: {
    port: parseInt(process.env.PORT || '3000'),
    webhookPath: process.env.WEBHOOK_PATH || '/webhook',
    publicUrl: process.env.PUBLIC_URL || null, // 用於 LINE 圖片訊息（需要 HTTPS）
  },

  // Minimax
  minimax: {
    apiKey: process.env.MINIMAX_API_KEY,
    baseURL: process.env.MINIMAX_BASE_URL || 'https://api.minimax.chat/v1',
  },

  // OpenAI
  openai: {
    apiKey: process.env.OPENAI_API_KEY,
    baseURL: process.env.OPENAI_BASE_URL,
  },
};

// Validate required config
const requiredEnvVars = [
  'LINE_CHANNEL_ACCESS_TOKEN',
  'LINE_CHANNEL_SECRET',
  'GCP_PROJECT_ID',
  'GCS_BUCKET_NAME',
];

for (const envVar of requiredEnvVars) {
  if (!process.env[envVar]) {
    throw new Error(`Missing required environment variable: ${envVar}`);
  }
}

