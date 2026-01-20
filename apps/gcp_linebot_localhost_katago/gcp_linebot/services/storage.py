import asyncio
from typing import Optional
from google.cloud import storage
from config import config

storage_client = storage.Client(
    project=config["gcp"]["project_id"],
    credentials=None,  # Will use default credentials or service account key
)

bucket = storage_client.bucket(config["gcs"]["bucket_name"])


async def upload_file(
    local_path: str, remote_path: str, cache_control: str | None = None
) -> str:
    """Upload file to GCS"""
    blob = bucket.blob(remote_path)
    if cache_control:
        blob.cache_control = cache_control
    # 在後台線程執行同步上傳操作，避免阻塞事件循環
    await asyncio.to_thread(blob.upload_from_filename, local_path)
    return f"gs://{config['gcs']['bucket_name']}/{remote_path}"


async def upload_buffer(
    buffer: bytes, remote_path: str, content_type: str = None, cache_control: str = None
) -> str:
    """Upload Buffer to GCS

    Args:
        buffer: The data to upload
        remote_path: The remote path in GCS
        content_type: Optional content type (e.g., 'application/json')
        cache_control: Optional cache control header (e.g., 'no-cache, max-age=0')
    """
    blob = bucket.blob(remote_path)

    # 在上傳前設置 cache_control（如果提供）
    # 這樣可以確保 cache_control 與 content_type 一起上傳，避免衝突
    if cache_control:
        blob.cache_control = cache_control

    # 上傳時同時指定 content_type 和已設置的 cache_control
    if content_type:
        await asyncio.to_thread(
            blob.upload_from_string, buffer, content_type=content_type
        )
    else:
        await asyncio.to_thread(blob.upload_from_string, buffer)

    return f"gs://{config['gcs']['bucket_name']}/{remote_path}"


async def download_file(remote_path: str) -> bytes:
    """Download file from GCS using SDK (bypasses public cache)"""
    blob = bucket.blob(remote_path)
    # 在後台線程執行同步下載操作，避免阻塞事件循環
    # 使用 SDK 讀取會直接繞過公開快取層，保證拿到最新版
    return await asyncio.to_thread(lambda: blob.download_as_bytes())


async def download_file_as_text(remote_path: str, encoding: str = "utf-8") -> str:
    """Download file from GCS as text using SDK (bypasses public cache)"""
    blob = bucket.blob(remote_path)
    # 使用 SDK 讀取會直接繞過公開快取層，保證拿到最新版
    return await asyncio.to_thread(lambda: blob.download_as_text(encoding=encoding))


async def file_exists(remote_path: str) -> bool:
    """Check if file exists"""
    blob = bucket.blob(remote_path)
    # 在後台線程執行同步檢查操作，避免阻塞事件循環
    return await asyncio.to_thread(lambda: blob.exists())


async def delete_file(remote_path: str):
    """Delete file"""
    blob = bucket.blob(remote_path)
    # 在後台線程執行同步刪除操作，避免阻塞事件循環
    await asyncio.to_thread(lambda: blob.delete())


async def list_files(prefix: str) -> list:
    """List all files with the given prefix"""
    # 在後台線程執行同步列出操作，避免阻塞事件循環
    blobs = await asyncio.to_thread(lambda: list(bucket.list_blobs(prefix=prefix)))
    return [blob.name for blob in blobs]


async def get_latest_file(prefix: str) -> Optional[str]:
    """Get the latest file (by time created) with the given prefix"""
    blobs = await asyncio.to_thread(lambda: list(bucket.list_blobs(prefix=prefix)))
    if not blobs:
        return None

    # Sort by time created (newest first)
    latest_blob = max(blobs, key=lambda b: b.time_created)
    return latest_blob.name


async def delete_folder(prefix: str):
    """Delete all files in a folder (with the given prefix)"""
    blobs = await asyncio.to_thread(lambda: list(bucket.list_blobs(prefix=prefix)))
    # Delete all blobs in the folder
    for blob in blobs:
        await asyncio.to_thread(lambda b=blob: b.delete())


def get_public_url(remote_path: str) -> str:
    """Get public URL for a file in GCS"""
    bucket_name = config["gcs"]["bucket_name"]
    from urllib.parse import quote

    encoded_path = "/".join(quote(part, safe="") for part in remote_path.split("/"))
    return f"https://storage.googleapis.com/{bucket_name}/{encoded_path}"
