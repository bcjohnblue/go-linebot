from typing import Dict, Optional, List
from services.vm_manager import (
    generate_instance_name, create_preemptible_vm, get_vm_status, delete_vm
)
from services.storage import upload_buffer, file_exists, download_file
from config import config
import uuid
import time

# Task status
class TaskStatus:
    PENDING = "PENDING"
    VM_CREATING = "VM_CREATING"
    VM_RUNNING = "VM_RUNNING"
    ANALYZING = "ANALYZING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


# In-memory task state (production should use Redis or database)
tasks: Dict[str, Dict] = {}


async def create_task(user_id: str, sgf_buffer: bytes, file_name: str) -> Dict:
    """Create new analysis task"""
    task_id = str(uuid.uuid4())
    timestamp = int(time.time() * 1000)
    
    # Upload SGF file to GCS
    sgf_path = f"sgf/{task_id}/{file_name}"
    sgf_gcs_path = await upload_buffer(sgf_buffer, sgf_path)
    
    # Create task record
    task = {
        "taskId": task_id,
        "userId": user_id,
        "fileName": file_name,
        "sgfPath": sgf_gcs_path,
        "status": TaskStatus.PENDING,
        "createdAt": timestamp,
        "updatedAt": timestamp,
        "instanceName": None,
        "resultPath": None,
        "error": None,
    }
    
    tasks[task_id] = task
    
    # Asynchronously start VM and execute analysis
    import asyncio
    asyncio.create_task(start_analysis(task_id))
    
    return task


async def start_analysis(task_id: str):
    """Start analysis process"""
    task = tasks.get(task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")
    
    try:
        # Generate VM instance name
        instance_name = generate_instance_name()
        update_task_status(task_id, TaskStatus.VM_CREATING, {"instanceName": instance_name})
        
        # Create Preemptible VM
        await create_preemptible_vm(instance_name, task["sgfPath"])
        update_task_status(task_id, TaskStatus.VM_RUNNING)
        
        # Start monitoring task status
        asyncio.create_task(monitor_task(task_id, instance_name))
    except Exception as error:
        logger.error(f"Failed to start analysis for task {task_id}: {error}", exc_info=True)
        update_task_status(task_id, TaskStatus.FAILED, {"error": str(error)})
        raise


async def monitor_task(task_id: str, instance_name: str):
    """Monitor task status"""
    max_wait_time = 10 * 60 * 1000  # 10 minutes
    check_interval = 5000  # Check every 5 seconds
    start_time = time.time() * 1000
    
    async def check_status():
        task = tasks.get(task_id)
        if not task:
            return
        
        # Check if timeout
        if (time.time() * 1000 - start_time) > max_wait_time:
            update_task_status(task_id, TaskStatus.FAILED, {"error": "Task timeout"})
            await delete_vm(instance_name)
            return
        
        # Check if result file exists
        result_path = f"results/{instance_name}/result.txt"
        status_path = f"results/{instance_name}/status.txt"
        
        try:
            result_exists = await file_exists(result_path)
            status_exists = await file_exists(status_path)
            
            if result_exists and status_exists:
                # Task completed
                update_task_status(
                    task_id,
                    TaskStatus.COMPLETED,
                    {"resultPath": f"gs://{config['storage']['bucket_name']}/{result_path}"}
                )
                
                # Clean up VM (if still running)
                import asyncio
                await asyncio.sleep(30)  # Wait 30 seconds before deleting
                await delete_vm(instance_name)
                return
            
            # Check VM status
            vm_status = await get_vm_status(instance_name)
            
            if not vm_status.get("exists") or vm_status.get("status") == "TERMINATED":
                # VM was interrupted or closed
                if result_exists:
                    # Has result, might be normal completion
                    update_task_status(
                        task_id,
                        TaskStatus.COMPLETED,
                        {"resultPath": f"gs://{config['storage']['bucket_name']}/{result_path}"}
                    )
                else:
                    # No result, might have been interrupted
                    update_task_status(
                        task_id,
                        TaskStatus.INTERRUPTED,
                        {"error": "VM was preempted or terminated"}
                    )
                    
                    # Retry (max 3 retries)
                    retry_count = task.get("retryCount", 0) + 1
                    if retry_count < 3:
                        print(f"Retrying task {task_id}, attempt {retry_count}")
                        task["retryCount"] = retry_count
                        await start_analysis(task_id)
                        return
                return
            
            if vm_status.get("status") == "RUNNING":
                update_task_status(task_id, TaskStatus.ANALYZING)
            
            # Continue monitoring
            await asyncio.sleep(check_interval / 1000)
            await check_status()
        except Exception as error:
            logger.error(f"Error monitoring task {task_id}: {error}", exc_info=True)
            await asyncio.sleep(check_interval / 1000)
            await check_status()
    
    # Wait for VM to start before beginning monitoring
    await asyncio.sleep(10)  # Wait 10 seconds before starting check
    await check_status()


def update_task_status(task_id: str, status: str, updates: Dict = None):
    """Update task status"""
    task = tasks.get(task_id)
    if task:
        task.update({
            "status": status,
            "updatedAt": int(time.time() * 1000),
            **(updates or {})
        })
        tasks[task_id] = task


def get_task(task_id: str) -> Optional[Dict]:
    """Get task information"""
    return tasks.get(task_id)


def get_user_tasks(user_id: str) -> List[Dict]:
    """Get all tasks for user"""
    return [
        task for task in tasks.values()
        if task.get("userId") == user_id
    ]


async def get_task_result(task_id: str) -> Optional[bytes]:
    """Get task result"""
    task = tasks.get(task_id)
    if not task or not task.get("resultPath"):
        return None
    
    # Extract file path from GCS path
    import re
    match = re.search(r"gs://[^/]+/(.+)", task["resultPath"])
    if not match:
        return None
    
    remote_path = match.group(1)
    return await download_file(remote_path)


# Add missing import
import asyncio

