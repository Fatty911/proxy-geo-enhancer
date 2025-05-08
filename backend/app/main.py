from fastapi import FastAPI, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import logging
import os

from backend.app.models.subscription import SubscriptionRequest, SubscriptionResponse
from backend.app.core.sub_converter import process_subscriptions
from backend.app.utils.github_api import get_clash_meta_binary, get_singbox_binary
from backend.app.core.config import settings # Import settings

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.PROJECT_NAME)

# CORS middleware (if your frontend is on a different domain/port during development)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Allow all origins, or specify your frontend domain
    allow_credentials=True,
    allow_methods=["*"], # Allow all methods
    allow_headers=["*"], # Allow all headers
)


# Dependency to ensure cores are downloaded
async def check_proxy_cores():
    clash_ready = await get_clash_meta_binary()
    singbox_ready = await get_singbox_binary()
    if not (clash_ready or singbox_ready):
        logger.warning("Neither Clash nor Singbox core could be prepared. IP testing functionality will be limited.")
    # Not raising HTTPException here as the app can still run, but testing might fail.
    # Individual tests will check for specific core existence.

# Run core checks on startup
@app.on_event("startup")
async def startup_event():
    logger.info("Application startup: Checking proxy cores...")
    # Ensure the directories for cores and temp configs exist
    os.makedirs(settings.CORES_DIR, exist_ok=True)
    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    await check_proxy_cores()
    logger.info("Proxy core check complete.")
    logger.info(f"Clash core expected at: {os.path.abspath(settings.CLASH_CORE_PATH)}")
    logger.info(f"Singbox core expected at: {os.path.abspath(settings.SINGBOX_CORE_PATH)}")


@app.post("/api/process-subscriptions", response_model=SubscriptionResponse)
async def process_subs_endpoint(request: SubscriptionRequest):
    logger.info(f"Received request to process {len(request.urls)} subscription(s) for format: {request.output_format}")
    try:
        # Ensure cores are available before processing, or handle gracefully in process_subscriptions
        # This is a good place to re-check or rely on the startup check.
        
        # Check if at least one core is available that might be used by process_subscriptions
        clash_exists = os.path.exists(settings.CLASH_CORE_PATH) and os.path.getsize(settings.CLASH_CORE_PATH) > 0
        singbox_exists = os.path.exists(settings.SINGBOX_CORE_PATH) and os.path.getsize(settings.SINGBOX_CORE_PATH) > 0

        if not (clash_exists or singbox_exists):
            logger.error("No proxy testing cores (Clash or Singbox) are available. Cannot perform IP checks.")
            # Fallback: return combined subscriptions without testing? Or error out?
            # For now, error out as the core feature is IP checking.
            # Alternatively, you could parse and combine without testing if desired.
            # This path should ideally not be hit if startup core download is robust.
            raise HTTPException(status_code=503, detail="Proxy testing cores are not available on the server. Please try again later.")


        new_content = await process_subscriptions(
            urls=[str(url) for url in request.urls], # Convert HttpUrl to str
            output_format=request.output_format
        )
        if "Error:" in new_content or not new_content.strip(): # Basic error check from process_subscriptions
            raise HTTPException(status_code=500, detail=new_content or "Failed to process subscriptions: Empty content returned.")

        logger.info(f"Successfully processed subscriptions. Output format: {request.output_format}. Content length: {len(new_content)}")
        return SubscriptionResponse(new_subscription_content=new_content, message="Subscriptions processed successfully.")
    except HTTPException: # Re-raise HTTPExceptions
        raise
    except FileNotFoundError as e: # Specific error for missing cores if not caught earlier
        logger.error(f"File not found during processing: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail=f"A required file was not found on the server: {e}. Testing cores might be missing.")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}", exc_info=True) # Log full traceback
        raise HTTPException(status_code=500, detail=f"An internal server error occurred: {str(e)}")

# Serve frontend static files
# Ensure the path to your frontend files is correct relative to where the backend is run.
# If backend/ is the PWD, then ../frontend is correct.
# If proxy-geo-enhancer/ is the PWD, then frontend/ is correct.
# Dockerfile will usually set WORKDIR to backend/, so ../frontend would be appropriate.
# However, for a cleaner setup, the static files could be served by Nginx or another
# web server in front of the API, or the frontend build output could be copied into
#_frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
_frontend_dir = "/app/frontend"
if not os.path.exists(_frontend_dir) or not os.path.isdir(_frontend_dir):
    logger.warning(f"Frontend directory '{_frontend_dir}' not found. Static file serving will be disabled.")
else:
    logger.info(f"Serving static files from '{_frontend_dir}'")
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="static")


if __name__ == "__main__":
    # This is for local development, not for production deployment with Docker
    import uvicorn
    # uvicorn.run(app, host="0.0.0.0", port=8000)
    # More robust way to run for dev, matching Docker entrypoint somewhat:
    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8000, reload=True)