from google.cloud import compute_v1
from config import config
import uuid
from typing import Dict, Optional


def generate_instance_name() -> str:
    """Generate unique VM instance name"""
    timestamp = int(time.time() * 1000)
    uuid_str = str(uuid.uuid4()).split("-")[0]
    return f"{config['vm']['instance_name_prefix']}-{timestamp}-{uuid_str}"


async def create_preemptible_vm(instance_name: str, sgf_gcs_path: str) -> Dict[str, str]:
    """Create Preemptible VM instance"""
    # Create startup script (download SGF from GCS and execute analysis)
    startup_script = f"""#!/bin/bash
set -e

# Install gcloud CLI (if not already installed)
if ! command -v gsutil &> /dev/null; then
  echo "Installing gcloud CLI..."
  curl https://sdk.cloud.google.com | bash
  exec -l $SHELL
fi

# Download SGF file
echo "Downloading SGF file from {sgf_gcs_path}..."
gsutil cp {sgf_gcs_path} /tmp/input.sgf

# Execute KataGo analysis
echo "Running KataGo analysis..."
{config['katago']['script_path']} /tmp/input.sgf /tmp/result.txt

# Upload results to GCS
echo "Uploading results..."
gsutil cp /tmp/result.txt gs://{config['storage']['bucket_name']}/results/{instance_name}/result.txt

# Mark task completed
echo "Task completed" > /tmp/status.txt
gsutil cp /tmp/status.txt gs://{config['storage']['bucket_name']}/results/{instance_name}/status.txt

# Shutdown VM (auto shutdown after analysis completes)
sudo shutdown -h +1
"""
    
    # Create instance
    instance_client = compute_v1.InstancesClient()
    
    instance = compute_v1.Instance()
    instance.name = instance_name
    instance.machine_type = f"zones/{config['gcp']['zone']}/machineTypes/{config['vm']['machine_type']}"
    
    # Boot disk
    disk = compute_v1.AttachedDisk()
    disk.boot = True
    disk.auto_delete = True
    disk.initialize_params = compute_v1.AttachedDiskInitializeParams()
    disk.initialize_params.source_image = (
        f"projects/{config['vm']['image_project']}/global/images/family/{config['vm']['image_family']}"
    )
    disk.initialize_params.disk_size_gb = config["vm"]["disk_size_gb"]
    instance.disks = [disk]
    
    # Metadata
    instance.metadata = compute_v1.Metadata()
    instance.metadata.items = [
        compute_v1.Items(key="startup-script", value=startup_script)
    ]
    
    # Scheduling
    instance.scheduling = compute_v1.Scheduling()
    instance.scheduling.preemptible = config["vm"]["preemptible"]
    instance.scheduling.on_host_maintenance = "TERMINATE"
    instance.scheduling.automatic_restart = False
    
    # Service account
    instance.service_accounts = [
        compute_v1.ServiceAccount(
            email="default",
            scopes=[
                "https://www.googleapis.com/auth/cloud-platform",
                "https://www.googleapis.com/auth/devstorage.read_write"
            ]
        )
    ]
    
    # Network
    network_interface = compute_v1.NetworkInterface()
    network_interface.network = "global/networks/default"
    access_config = compute_v1.AccessConfig()
    access_config.name = "External NAT"
    access_config.type_ = "ONE_TO_ONE_NAT"
    network_interface.access_configs = [access_config]
    instance.network_interfaces = [network_interface]
    
    # Tags
    instance.tags = compute_v1.Tags()
    instance.tags.items = ["katago-worker"]
    
    # Insert instance
    operation = instance_client.insert(
        project=config["gcp"]["project_id"],
        zone=config["gcp"]["zone"],
        instance_resource=instance
    )
    
    # Wait for operation to complete
    operation_client = compute_v1.ZoneOperationsClient()
    while operation.status != compute_v1.Operation.Status.DONE:
        operation = operation_client.get(
            project=config["gcp"]["project_id"],
            zone=config["gcp"]["zone"],
            operation=operation.name
        )
        await asyncio.sleep(1)
    
    return {
        "name": instance_name,
        "zone": config["gcp"]["zone"],
        "status": "PROVISIONING",
        "sgfPath": sgf_gcs_path
    }


async def get_vm_status(instance_name: str) -> Dict[str, any]:
    """Get VM status"""
    try:
        instance_client = compute_v1.InstancesClient()
        instance = instance_client.get(
            project=config["gcp"]["project_id"],
            zone=config["gcp"]["zone"],
            instance=instance_name
        )
        
        return {
            "exists": True,
            "status": instance.status,
            "name": instance.name,
            "zone": config["gcp"]["zone"]
        }
    except Exception as error:
        if hasattr(error, "code") and error.code in [404, 5]:
            return {"exists": False}
        raise


async def delete_vm(instance_name: str):
    """Delete VM instance"""
    try:
        instance_client = compute_v1.InstancesClient()
        operation = instance_client.delete(
            project=config["gcp"]["project_id"],
            zone=config["gcp"]["zone"],
            instance=instance_name
        )
        
        # Wait for operation to complete
        operation_client = compute_v1.ZoneOperationsClient()
        while operation.status != compute_v1.Operation.Status.DONE:
            operation = operation_client.get(
                project=config["gcp"]["project_id"],
                zone=config["gcp"]["zone"],
                operation=operation.name
            )
            await asyncio.sleep(1)
    except Exception as error:
        # If VM doesn't exist, ignore error
        if not (hasattr(error, "code") and error.code in [404, 5]):
            raise


async def wait_for_vm_ready(instance_name: str, max_wait_time: int = 60000) -> bool:
    """Wait for VM to be ready"""
    start_time = time.time() * 1000
    
    while (time.time() * 1000 - start_time) < max_wait_time:
        status = await get_vm_status(instance_name)
        
        if status.get("exists") and status.get("status") == "RUNNING":
            return True
        
        if status.get("exists") and status.get("status") == "TERMINATED":
            return False
        
        # Wait 2 seconds before checking again
        await asyncio.sleep(2)
    
    return False


# Add missing imports
import time
import asyncio

