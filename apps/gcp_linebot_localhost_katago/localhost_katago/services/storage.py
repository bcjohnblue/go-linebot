from google.cloud import storage
from config import config

storage_client = storage.Client(
    project=config["gcp"]["project_id"],
    credentials=None,  # Will use default credentials or service account key
)

bucket = storage_client.bucket(config["storage"]["bucket_name"])


async def upload_file(local_path: str, remote_path: str) -> str:
    """Upload file to GCS"""
    blob = bucket.blob(remote_path)
    blob.upload_from_filename(local_path)
    return f"gs://{config['storage']['bucket_name']}/{remote_path}"


async def upload_buffer(buffer: bytes, remote_path: str) -> str:
    """Upload Buffer to GCS"""
    blob = bucket.blob(remote_path)
    blob.upload_from_string(buffer)
    return f"gs://{config['storage']['bucket_name']}/{remote_path}"


async def download_file(remote_path: str) -> bytes:
    """Download file from GCS"""
    blob = bucket.blob(remote_path)
    return blob.download_as_bytes()


async def file_exists(remote_path: str) -> bool:
    """Check if file exists"""
    blob = bucket.blob(remote_path)
    return blob.exists()


async def delete_file(remote_path: str):
    """Delete file"""
    blob = bucket.blob(remote_path)
    blob.delete()
