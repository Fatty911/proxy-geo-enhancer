import httpx
import os
import shutil
import platform
import logging
import tarfile
import gzip
import zipfile
import io
from backend.app.core.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def download_file(url: str, dest_path: str):
    async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
        try:
            logger.info(f"Attempting to download from: {url}")
            response = await client.get(url)
            response.raise_for_status() # Raises an exception for 4XX/5XX errors
            
            # If it's a GZ file, decompress it directly
            if url.endswith(".gz") and not url.endswith(".tar.gz"):
                logger.info(f"Decompressing GZ file to {dest_path}")
                with gzip.open(io.BytesIO(response.content), 'rb') as f_in:
                    with open(dest_path, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)
            elif url.endswith(".zip"):
                logger.info(f"Decompressing ZIP file to {os.path.dirname(dest_path)}")
                with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
                    # Find the executable within the zip. This logic might need adjustment based on zip structure.
                    # Assuming the main executable is often named similar to the repo or is the largest file.
                    # For simplicity, we'll extract all and assume the caller knows the executable name.
                    zf.extractall(os.path.dirname(dest_path))
                    # Potentially rename the main executable if needed, or ensure the core_path points to it.
                    # This part is tricky as zip contents vary.
                    # Example: if sing-box executable is inside a folder in the zip.
                    # For now, this just extracts. The caller (ensure_core_binary) will need to find the correct binary.
            else: # For tar.gz or direct binary
                with open(dest_path, 'wb') as f:
                    f.write(response.content)
            
            # Make executable if it's a binary (not a compressed archive itself)
            if not url.endswith((".gz", ".zip", ".tar.gz")): # or after decompression
                 os.chmod(dest_path, 0o755)
            logger.info(f"Successfully downloaded/extracted to {dest_path}")
            return True
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error downloading {url}: {e.response.status_code} - {e.response.text}")
        except Exception as e:
            logger.error(f"Failed to download {url}: {e}")
    return False

def find_executable_in_dir(dir_path, possible_names):
    for root, _, files in os.walk(dir_path):
        for f_name in files:
            if f_name in possible_names:
                return os.path.join(root, f_name)
    return None


async def ensure_core_binary(core_name: str, github_api_url: str, target_binary_path: str, asset_keyword: str, executable_name_in_archive: str):
    if os.path.exists(target_binary_path) and os.path.getsize(target_binary_path) > 0:
        logger.info(f"{core_name} binary already exists at {target_binary_path}")
        # Basic check: make sure it's executable
        try:
            os.chmod(target_binary_path, 0o755)
        except Exception:
            pass # May fail if already set or permissions issue, not critical here
        return True

    logger.info(f"Downloading latest {core_name} binary...")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(github_api_url)
            response.raise_for_status()
            latest_release = response.json()
            assets = latest_release.get("assets", [])

            arch = platform.machine().lower()
            sys_platform = platform.system().lower()

            # Simplified architecture mapping, needs to be robust
            if arch == "x86_64": gh_arch = "amd64"
            elif arch == "aarch64": gh_arch = "arm64"
            elif arch == "armv7l": gh_arch = "armv7" # Example
            else: gh_arch = arch

            # Construct a more specific keyword based on system
            # Example for Clash.Meta: clash.meta-linux-amd64-alpha-vx.y.z.gz
            # Example for Sing-box: sing-box-1.8.0-linux-amd64.tar.gz
            
            # This keyword matching is crucial and needs to be precise for each core
            # For Clash.Meta (look for .gz typically, not .tar.gz)
            # For Sing-Box (look for .tar.gz or .zip)
            
            dl_url = None
            found_asset_name = None

            for asset in assets:
                name = asset.get("name", "").lower()
                if asset_keyword in name and gh_arch in name and sys_platform in name:
                    # Prioritize specific builds if multiple match (e.g., non-premium, non-dev)
                    # This is a very basic filter. Real-world might need more complex logic.
                    dl_url = asset.get("browser_download_url")
                    found_asset_name = name
                    logger.info(f"Found matching asset: {name} for {sys_platform}-{gh_arch}")
                    break
            
            if not dl_url:
                logger.warning(f"Could not find a suitable {core_name} asset for {sys_platform}-{gh_arch} with keyword '{asset_keyword}'. Available assets:")
                for asset in assets: logger.warning(f" - {asset.get('name')}")
                return False

            download_dir = os.path.dirname(target_binary_path)
            os.makedirs(download_dir, exist_ok=True)
            
            temp_download_path = os.path.join(download_dir, found_asset_name)

            if not await download_file(dl_url, temp_download_path):
                return False

            final_executable_path = None
            if found_asset_name.endswith(".tar.gz"):
                logger.info(f"Extracting {temp_download_path}...")
                with tarfile.open(temp_download_path, "r:gz") as tar:
                    # Try to find the executable within the tar
                    members = tar.getmembers()
                    for member in members:
                        if executable_name_in_archive in member.name.lower() and member.isfile():
                            # Extract to a temp name then rename, or extract directly if path is correct
                            # We assume the executable is in a top-level dir or at root of tar
                            tar.extract(member, path=download_dir)
                            extracted_file_path = os.path.join(download_dir, member.name)
                            # If it's in a subfolder, we need to move it or adjust target_binary_path
                            # For simplicity, assuming it extracts to a predictable name or path
                            if os.path.basename(extracted_file_path) == executable_name_in_archive:
                                final_executable_path = extracted_file_path
                                break
                    if not final_executable_path:
                         # Fallback: try to find by expected name after extracting all
                        tar.extractall(path=download_dir) # Extract all if specific member not found easily
                        final_executable_path = find_executable_in_dir(download_dir, [executable_name_in_archive, core_name])


            elif found_asset_name.endswith(".gz"): # e.g., clash.meta-linux-amd64-vX.Y.Z.gz
                # The download_file function already handled decompression for .gz
                # The decompressed file is at `temp_download_path` (which was the original .gz name)
                # We need to rename it to `target_binary_path`
                decompressed_path = temp_download_path # download_file saves it as this name after decompressing
                final_executable_path = decompressed_path # The file is already there, just needs chmod
                # It might be better if download_file saved with the final binary name, not the gz name

            elif found_asset_name.endswith(".zip"):
                # download_file extracts all from zip. We need to find the executable.
                final_executable_path = find_executable_in_dir(download_dir, [executable_name_in_archive, core_name])


            else: # Direct binary
                final_executable_path = temp_download_path


            if final_executable_path and os.path.exists(final_executable_path):
                # Ensure the final target_binary_path is correct and move/rename if necessary
                if final_executable_path != target_binary_path:
                     shutil.move(final_executable_path, target_binary_path)
                os.chmod(target_binary_path, 0o755)
                logger.info(f"{core_name} downloaded and prepared at {target_binary_path}")
            else:
                logger.error(f"Could not locate executable '{executable_name_in_archive}' after download/extraction.")
                return False

            # Clean up downloaded archive if it's different from the binary itself
            if temp_download_path != target_binary_path and os.path.exists(temp_download_path):
                try:
                    if os.path.isfile(temp_download_path):
                        os.remove(temp_download_path)
                    elif os.path.isdir(temp_download_path): # if zip extracted into a dir with archive name
                        shutil.rmtree(temp_download_path)
                except Exception as e:
                    logger.warning(f"Could not clean up {temp_download_path}: {e}")
            return True

        except httpx.RequestError as e:
            logger.error(f"Request error fetching {core_name} version: {e}")
        except Exception as e:
            logger.error(f"Error ensuring {core_name} binary: {e}", exc_info=True)
    return False


async def get_clash_meta_binary():
    # Clash Meta usually has assets like: clash.meta-linux-amd64-vx.y.z.gz
    # The executable inside is just 'clash-meta' or similar after decompression.
    # We need to ensure the asset_keyword is specific enough.
    # For 'clash.meta-linux-amd64-compatible.gz' it's a direct binary after gz.
    # For 'clash.meta-linux-amd64-vX.Y.Z.gz' it's also direct after gz.
    # The `executable_name_in_archive` should be the name of the binary AFTER extraction.
    # Let's target the "compatible" version for wider use if available, or a standard one.
    # The name of the binary after decompressing *.gz is usually the part before .gz
    # but GitHub asset names can vary. `asset_keyword` needs care.
    # `clash.meta-linux-amd64` often matches `clash.meta-linux-amd64-compatible.gz` or `clash.meta-linux-amd64-vX.Y.Z.gz`
    # The extracted binary name is usually `clash.meta-linux-amd64-compatible` or `clash.meta-linux-amd64-vX.Y.Z`
    # We want the final `settings.CLASH_CORE_PATH` to be just `clash-meta`. So renaming is key.
    # Let's simplify: assume the binary name is just 'clash-meta' after all processing.
    return await ensure_core_binary(
        core_name="Clash.Meta",
        github_api_url=settings.CLASH_META_LATEST_RELEASE_URL,
        target_binary_path=settings.CLASH_CORE_PATH,
        asset_keyword="clash.meta", # General keyword
        # asset_keyword="compatible.gz", # More specific for clash.meta, if using that build
        executable_name_in_archive="clash-meta" # This is what we expect the final binary to be named or found as
    )

async def get_singbox_binary():
    # Sing-box assets are often like: sing-box-1.8.0-linux-amd64.tar.gz
    # Inside the tar.gz, there might be a folder, and then the `sing-box` executable.
    return await ensure_core_binary(
        core_name="Sing-box",
        github_api_url=settings.SINGBOX_LATEST_RELEASE_URL,
        target_binary_path=settings.SINGBOX_CORE_PATH,
        asset_keyword="sing-box", # General keyword
        executable_name_in_archive="sing-box" # The binary name within the archive/folder
    )

# Example usage (typically called at application startup)
# import asyncio
# asyncio.run(get_clash_meta_binary())
# asyncio.run(get_singbox_binary())