import asyncio
import os
import yaml
import json
import logging
from backend.app.core.config import settings

logger = logging.getLogger(__name__)

async def run_proxy_core(core_path: str, config_path: str, core_type: str):
    """
    Starts the proxy core (Clash or Singbox) as a subprocess.
    Returns the process object.
    """
    if not os.path.exists(core_path):
        logger.error(f"{core_type} core not found at {core_path}")
        raise FileNotFoundError(f"{core_type} core not found at {core_path}")
    if not os.path.exists(config_path):
        logger.error(f"{core_type} config not found at {config_path}")
        raise FileNotFoundError(f"{core_type} config not found at {config_path}")

    # Ensure the core is executable
    try:
        os.chmod(core_path, 0o755)
    except Exception as e:
        logger.warning(f"Could not chmod {core_path}: {e}")


    command = []
    if core_type == "clash":
        # Clash Meta: ./clash-meta -d /path/to/config_dir (where config.yaml is)
        # The config file must be named config.yaml or specified with -f
        # For simplicity, we assume config_path is the full path to the config file.
        # We'll tell Clash where its "home" directory is, which contains the config.
        config_dir = os.path.dirname(config_path)
        command = [core_path, "-d", config_dir, "-f", config_path]
    elif core_type == "singbox":
        # Sing-box: ./sing-box run -c /path/to/config.json
        command = [core_path, "run", "-c", config_path] # V1.8+ `run` command
        # Older versions might use `sing-box -c /path/to/config.json` directly

    logger.info(f"Starting {core_type} with command: {' '.join(command)}")
    
    # Use asyncio.create_subprocess_exec for non-blocking operation
    # Capture stdout/stderr for debugging if needed
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    # Give it a moment to start up
    await asyncio.sleep(2) # Adjust as needed; ideally check for port listening
    logger.info(f"{core_type} process started (PID: {process.pid}).")
    return process

async def stop_proxy_core(process: asyncio.subprocess.Process, core_type: str):
    """Stops the proxy core process."""
    if process and process.returncode is None: # Check if process is running
        logger.info(f"Stopping {core_type} process (PID: {process.pid})...")
        try:
            process.terminate()
            await asyncio.wait_for(process.wait(), timeout=5.0)
            logger.info(f"{core_type} process terminated.")
        except asyncio.TimeoutError:
            logger.warning(f"{core_type} process did not terminate gracefully, killing.")
            process.kill()
            await process.wait()
            logger.info(f"{core_type} process killed.")
        except Exception as e:
            logger.error(f"Error stopping {core_type} process: {e}")
    elif process:
        logger.info(f"{core_type} process (PID: {process.pid}) already stopped with code {process.returncode}.")
    else:
        logger.info(f"No active {core_type} process to stop.")

async def monitor_process_output(process: asyncio.subprocess.Process, core_type: str):
    """Monitors stdout and stderr of the process for debugging."""
    async def read_stream(stream, stream_name):
        while True:
            line = await stream.readline()
            if line:
                logger.debug(f"[{core_type} {stream_name} PID:{process.pid}]: {line.decode().strip()}")
            else:
                break
    
    if process.stdout:
      await asyncio.create_task(read_stream(process.stdout, "stdout"))
    if process.stderr:
      await asyncio.create_task(read_stream(process.stderr, "stderr"))