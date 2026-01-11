import { Storage } from '@google-cloud/storage';
import { config } from '../config.js';

const storage = new Storage({
  projectId: config.gcp.projectId,
  keyFilename: config.gcp.serviceAccountKeyPath,
});

const bucket = storage.bucket(config.storage.bucketName);

/**
 * 上傳檔案至 GCS
 * @param {string} localPath - 本地檔案路徑
 * @param {string} remotePath - GCS 遠端路徑
 * @returns {Promise<string>} 公開 URL
 */
export async function uploadFile(localPath, remotePath) {
  await bucket.upload(localPath, {
    destination: remotePath,
  });
  return `gs://${config.storage.bucketName}/${remotePath}`;
}

/**
 * 上傳 Buffer 至 GCS
 * @param {Buffer} buffer - 檔案內容
 * @param {string} remotePath - GCS 遠端路徑
 * @returns {Promise<string>} GCS 路徑
 */
export async function uploadBuffer(buffer, remotePath) {
  const file = bucket.file(remotePath);
  await file.save(buffer);
  return `gs://${config.storage.bucketName}/${remotePath}`;
}

/**
 * 下載檔案從 GCS
 * @param {string} remotePath - GCS 遠端路徑
 * @returns {Promise<Buffer>} 檔案內容
 */
export async function downloadFile(remotePath) {
  const file = bucket.file(remotePath);
  const [buffer] = await file.download();
  return buffer;
}

/**
 * 檢查檔案是否存在
 * @param {string} remotePath - GCS 遠端路徑
 * @returns {Promise<boolean>}
 */
export async function fileExists(remotePath) {
  const file = bucket.file(remotePath);
  const [exists] = await file.exists();
  return exists;
}

/**
 * 刪除檔案
 * @param {string} remotePath - GCS 遠端路徑
 * @returns {Promise<void>}
 */
export async function deleteFile(remotePath) {
  const file = bucket.file(remotePath);
  await file.delete();
}

