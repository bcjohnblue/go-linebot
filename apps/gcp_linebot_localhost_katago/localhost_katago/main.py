import os
import asyncio
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from config import config
from logger import logger
from handlers.katago_handler import run_katago_analysis, run_katago_gtp_next_move, run_katago_analysis_evaluation
import httpx
import tempfile


app = FastAPI(title="Localhost Review Service")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def execute_review_task(
    task_id: str,
    sgf_gcs_path: str,
    callback_url: str,
    target_id: str,
    visits: int,
):
    """Execute KataGo review task in background"""
    try:
        # Extract GCS path (gs://bucket/path or bucket/path)
        if sgf_gcs_path.startswith("gs://"):
            gcs_path = sgf_gcs_path[5:]  # Remove gs:// prefix
        else:
            gcs_path = sgf_gcs_path

        # Split bucket and path
        parts = gcs_path.split("/", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid GCS path format: {sgf_gcs_path}")

        bucket_name, remote_path = parts

        # Verify bucket matches configured bucket
        from services.storage import storage_client

        configured_bucket = storage_client.bucket(config["storage"]["bucket_name"])
        if bucket_name != config["storage"]["bucket_name"]:
            # Use the bucket from the path if different
            bucket = storage_client.bucket(bucket_name)
        else:
            bucket = configured_bucket

        # Create temporary directory for review
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Download SGF file from GCS
            logger.info(f"Downloading SGF file from GCS: {remote_path}")
            blob = bucket.blob(remote_path)
            sgf_content = blob.download_as_bytes()
            local_sgf_path = temp_path / f"{task_id}.sgf"
            local_sgf_path.write_bytes(sgf_content)
            logger.info(f"Downloaded SGF file to: {local_sgf_path}")

            # Execute KataGo review
            logger.info(f"Starting KataGo review for task: {task_id}")
            result = await run_katago_analysis(str(local_sgf_path), visits=visits)

            if not result.get("success"):
                error_msg = result.get("stderr", "Unknown error")
                logger.error(f"KataGo review failed for task {task_id}: {error_msg}")

                # Notify Cloud Run of failure
                async with httpx.AsyncClient() as client:
                    await client.post(
                        callback_url,
                        json={
                            "task_id": task_id,
                            "status": "failed",
                            "error": error_msg,
                            "target_id": target_id,
                        },
                        timeout=30.0,
                    )
                return

            # Upload review results to GCS
            result_paths = {}

            # Upload JSON file if exists
            if result.get("jsonPath") and os.path.exists(result["jsonPath"]):
                json_remote_path = f"target_{target_id}/reviews/{task_id}.json"
                # Use configured bucket for upload
                json_blob = configured_bucket.blob(json_remote_path)
                # Set cache control to avoid caching
                json_blob.cache_control = "no-cache, max-age=0"
                json_blob.upload_from_filename(result["jsonPath"])
                result_paths["json_gcs_path"] = (
                    f"gs://{config['storage']['bucket_name']}/{json_remote_path}"
                )
                logger.info(f"Uploaded JSON to: {json_remote_path}")

            # Prepare callback payload
            callback_payload = {
                "task_id": task_id,
                "status": "success",
                "target_id": target_id,
                "result_paths": result_paths,
                "move_stats": result.get("moveStats"),
            }

            # Notify Cloud Run of completion
            logger.info(f"Notifying Cloud Run of completion: {callback_url}")
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    callback_url,
                    json=callback_payload,
                    timeout=600.0,
                )
                response.raise_for_status()
                logger.info(f"Successfully notified Cloud Run: {response.status_code}")

    except Exception as error:
        logger.error(f"Error in review task {task_id}: {error}", exc_info=True)
        # Try to notify Cloud Run of error
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    callback_url,
                    json={
                        "task_id": task_id,
                        "status": "failed",
                        "error": str(error),
                        "target_id": target_id,
                    },
                    timeout=600.0,
                )
        except Exception as callback_error:
            logger.error(
                f"Failed to send error callback: {callback_error}", exc_info=True
            )


@app.post("/review")
async def review_from_cloud_run(request: Request, background_tasks: BackgroundTasks):
    """Receive review request from Cloud Run and execute KataGo review asynchronously"""
    try:
        body = await request.json()
        task_id = body.get("task_id")
        sgf_gcs_path = body.get("sgf_gcs_path")
        callback_url = body.get("callback_url")
        target_id = body.get("target_id")
        visits = body.get("visits", 5)

        if not all([task_id, sgf_gcs_path, callback_url, target_id]):
            raise HTTPException(
                status_code=400,
                detail="Missing required fields: task_id, sgf_gcs_path, callback_url, target_id",
            )

        logger.info(
            f"Received review request: task_id={task_id}, sgf_gcs_path={sgf_gcs_path}"
        )

        # Add review task to background tasks
        background_tasks.add_task(
            execute_review_task,
            task_id=task_id,
            sgf_gcs_path=sgf_gcs_path,
            callback_url=callback_url,
            target_id=target_id,
            visits=visits,
        )

        # Immediately return response (202 Accepted)
        return JSONResponse(
            content={
                "status": "accepted",
                "task_id": task_id,
                "message": "Review task started",
            },
            status_code=202,
        )

    except Exception as error:
        logger.error(f"Error in review endpoint: {error}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to process review request: {str(error)}"
        )


@app.post("/evaluation")
async def evaluation_from_cloud_run(request: Request):
    """Receive evaluation request from Cloud Run and execute KataGo evaluation synchronously"""
    try:
        body = await request.json()
        sgf_gcs_path = body.get("sgf_gcs_path")
        current_turn = body.get("current_turn", 1)
        visits = body.get("visits", 1000)

        if not sgf_gcs_path:
            raise HTTPException(
                status_code=400,
                detail="Missing required field: sgf_gcs_path",
            )

        logger.info(f"Received evaluation request: sgf_gcs_path={sgf_gcs_path}")

        # Download SGF from GCS to temporary file
        from services.storage import download_file, storage_client, bucket

        # Extract GCS path (gs://bucket/path or bucket/path)
        if sgf_gcs_path.startswith("gs://"):
            parts = sgf_gcs_path[5:].split("/", 1)
            bucket_name = parts[0]
            remote_path = parts[1] if len(parts) > 1 else ""
        else:
            bucket_name = config.get("gcs", {}).get("bucket_name")
            remote_path = sgf_gcs_path

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            local_sgf_path = temp_path / "evaluation.sgf"

            logger.info(f"Downloading SGF file from GCS: {remote_path}")
            blob = bucket.blob(remote_path)
            sgf_content = blob.download_as_bytes()
            local_sgf_path.write_bytes(sgf_content)
            logger.info(f"Downloaded SGF file to: {local_sgf_path}")

            # Execute KataGo evaluation
            logger.info(f"Starting KataGo evaluation")
            result = await run_katago_analysis_evaluation(
                str(local_sgf_path), current_turn, visits=visits
            )

            if not result.get("success"):
                error_msg = result.get("error", "Unknown error")
                logger.error(f"KataGo evaluation failed: {error_msg}")
                raise HTTPException(
                    status_code=500, detail=f"KataGo evaluation failed: {error_msg}"
                )

            return JSONResponse(content=result, status_code=200)

    except Exception as error:
        logger.error(f"Error in evaluation endpoint: {error}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to process evaluation request: {str(error)}"
        )


@app.post("/get_ai_next_move")
async def get_ai_next_move(request: Request, background_tasks: BackgroundTasks):
    """Receive request from Cloud Run to get AI's next move and execute KataGo GTP asynchronously"""
    try:
        body = await request.json()
        sgf_gcs_path = body.get("sgf_gcs_path")
        callback_url = body.get("callback_url")
        target_id = body.get("target_id")
        current_turn = body.get("current_turn")
        reply_token = body.get("reply_token")
        user_board_image_url = body.get("user_board_image_url")
        visits = body.get("visits", 400)  # Default visits for AI vs player

        if not all([sgf_gcs_path, callback_url, target_id, current_turn is not None]):
            raise HTTPException(
                status_code=400,
                detail="Missing required fields: sgf_gcs_path, callback_url, target_id, current_turn",
            )

        logger.info(
            f"Received get_ai_next_move request: target_id={target_id}, current_turn={current_turn}"
        )

        # Add task to background tasks
        background_tasks.add_task(
            execute_get_ai_next_move_task,
            sgf_gcs_path=sgf_gcs_path,
            callback_url=callback_url,
            target_id=target_id,
            current_turn=current_turn,
            reply_token=reply_token,
            user_board_image_url=user_board_image_url,
            visits=visits,
        )

        # Immediately return response (202 Accepted)
        return JSONResponse(
            content={
                "status": "accepted",
                "message": "AI next move task started",
            },
            status_code=202,
        )

    except Exception as error:
        logger.error(f"Error in get_ai_next_move endpoint: {error}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to process get_ai_next_move request: {str(error)}"
        )


async def execute_get_ai_next_move_task(
    sgf_gcs_path: str,
    callback_url: str,
    target_id: str,
    current_turn: int,
    reply_token: Optional[str],
    user_board_image_url: Optional[str],
    visits: int,
):
    """Execute KataGo GTP next move task in background"""
    try:
        # Extract GCS path (gs://bucket/path or bucket/path)
        if sgf_gcs_path.startswith("gs://"):
            gcs_path = sgf_gcs_path[5:]  # Remove gs:// prefix
        else:
            gcs_path = sgf_gcs_path

        # Split bucket and path
        parts = gcs_path.split("/", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid GCS path format: {sgf_gcs_path}")

        bucket_name, remote_path = parts

        # Verify bucket matches configured bucket
        from services.storage import storage_client

        configured_bucket = storage_client.bucket(config["storage"]["bucket_name"])
        if bucket_name != config["storage"]["bucket_name"]:
            # Use the bucket from the path if different
            bucket = storage_client.bucket(bucket_name)
        else:
            bucket = configured_bucket

        # Create temporary directory for GTP task
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Download SGF file from GCS
            logger.info(f"Downloading SGF file from GCS: {remote_path}")
            blob = bucket.blob(remote_path)
            sgf_content = blob.download_as_bytes()
            local_sgf_path = temp_path / "game.sgf"
            local_sgf_path.write_bytes(sgf_content)
            logger.info(f"Downloaded SGF file to: {local_sgf_path}")

            # Execute KataGo GTP next move
            logger.info(f"Starting KataGo GTP for next move: target_id={target_id}, current_turn={current_turn}")
            result = await run_katago_gtp_next_move(str(local_sgf_path), current_turn, visits=visits)

            if not result.get("success"):
                error_msg = result.get("error", "Unknown error")
                logger.error(f"KataGo GTP failed for target {target_id}: {error_msg}")

                # Notify Cloud Run of failure
                async with httpx.AsyncClient() as client:
                    await client.post(
                        callback_url,
                        json={
                            "status": "failed",
                            "target_id": target_id,
                            "error": error_msg,
                            "reply_token": reply_token,
                            "user_board_image_url": user_board_image_url,
                        },
                        timeout=30.0,
                    )
                return

            # Get move from result
            move = result.get("move")
            if not move:
                error_msg = "KataGo returned success but no move"
                logger.error(error_msg)
                async with httpx.AsyncClient() as client:
                    await client.post(
                        callback_url,
                        json={
                            "status": "failed",
                            "target_id": target_id,
                            "error": error_msg,
                            "reply_token": reply_token,
                            "user_board_image_url": user_board_image_url,
                        },
                        timeout=30.0,
                    )
                return

            # Prepare callback payload
            callback_payload = {
                "status": "success",
                "target_id": target_id,
                "move": move,
                "current_turn": current_turn,
                "reply_token": reply_token,
                "user_board_image_url": user_board_image_url,
            }

            # Notify Cloud Run of completion
            logger.info(f"Notifying Cloud Run of completion: {callback_url}")
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    callback_url,
                    json=callback_payload,
                    timeout=60.0,
                )
                response.raise_for_status()
                logger.info(f"Successfully notified Cloud Run: {response.status_code}")

    except Exception as error:
        logger.error(f"Error in get_ai_next_move task: {error}", exc_info=True)
        # Try to notify Cloud Run of error
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    callback_url,
                    json={
                        "status": "failed",
                        "error": str(error),
                        "target_id": target_id,
                        "reply_token": reply_token,
                        "user_board_image_url": user_board_image_url,
                    },
                    timeout=60.0,
                )
        except Exception as callback_error:
            logger.error(
                f"Failed to send error callback: {callback_error}", exc_info=True
            )


@app.get("/health")
async def health():
    """Health check endpoint"""
    from datetime import datetime

    return {"status": "ok", "timestamp": datetime.now().isoformat()}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=config["server"]["port"], reload=True)
