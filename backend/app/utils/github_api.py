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

# 从环境变量中获取 GitHub Token
GITHUB_API_TOKEN = os.environ.get("RENDER_PROXY_GEO_ENHANCER")
COMMON_HEADERS = {}
if GITHUB_API_TOKEN:
    # 对于 GitHub API, 'token' 或 'Bearer' 都可以，'token' 更常用于 PAT (classic)
    COMMON_HEADERS["Authorization"] = f"token {GITHUB_API_TOKEN}"
    logger.info("GitHub API Token found. Using token for API requests.")
else:
    logger.warning("GITHUB_TOKEN environment variable not set. GitHub API requests may be rate-limited.")

async def download_file(url: str, dest_path: str):
    # 下载 GitHub Release 的 'browser_download_url' 通常不需要认证
    # Token 主要用于 api.github.com 的元数据请求
    async with httpx.AsyncClient(follow_redirects=True, timeout=120.0) as client: # 增加超时时间
        try:
            logger.info(f"Attempting to download asset from: {url}")
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
            # if not url.endswith((".gz", ".zip", ".tar.gz")): # or after decompression
            #      os.chmod(dest_path, 0o755)
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
        try:
            os.chmod(target_binary_path, 0o755) # 确保可执行
        except Exception:
            pass
        return True

    logger.info(f"Requesting latest {core_name} release info from {github_api_url} using configured headers.")
    # !!! 关键改动: 在 httpx.AsyncClient 中传入 headers=COMMON_HEADERS !!!
    async with httpx.AsyncClient(headers=COMMON_HEADERS, timeout=30.0, follow_redirects=True) as client:
        try:
            response = await client.get(github_api_url)
            response.raise_for_status() # 对 4xx/5xx 错误抛出异常
            latest_release = response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error getting {core_name} release info ({github_api_url}): {e.response.status_code} - {e.response.text}", exc_info=True)
            return False
        except Exception as e:
            logger.error(f"Failed to get {core_name} release info ({github_api_url}): {e}", exc_info=True)
            return False

    assets = latest_release.get("assets", [])
    arch = platform.machine().lower()
    sys_platform = platform.system().lower()

    if arch == "x86_64": gh_arch = "amd64"
    elif arch == "aarch64": gh_arch = "arm64"
    elif arch == "armv7l": gh_arch = "armv7"
    else: gh_arch = arch

    dl_url = None
    found_asset_name = None

    for asset in assets:
        name = asset.get("name", "").lower()
        # 你原有的 asset 筛选逻辑
        if asset_keyword in name and gh_arch in name and sys_platform in name:
            dl_url = asset.get("browser_download_url")
            found_asset_name = name
            logger.info(f"Found matching asset for {core_name}: {name} for {sys_platform}-{gh_arch}")
            break

    if not dl_url:
        logger.warning(f"Could not find a suitable {core_name} asset for {sys_platform}-{gh_arch} with keyword '{asset_keyword}'. Available assets:")
        for asset in assets: logger.warning(f" - {asset.get('name')}")
        return False

    download_dir = os.path.dirname(target_binary_path) # e.g., /app/backend/downloaded_cores
    os.makedirs(download_dir, exist_ok=True)

    # 临时下载路径，使用 GitHub 上的资源名
    temp_download_path = os.path.join(download_dir, found_asset_name) 

    logger.info(f"Downloading asset {found_asset_name} for {core_name} from {dl_url}")
    if not await download_file(dl_url, temp_download_path):
        logger.error(f"Failed to download asset for {core_name} from {dl_url}.")
        return False

    # --- 核心的解压、重命名、权限设置逻辑 ---
    # 这部分需要非常健壮，确保最终 `target_binary_path` 是正确的、可执行的二进制文件
    final_executable_path_after_processing = None

    if found_asset_name.endswith(".tar.gz"):
        logger.info(f"Extracting {temp_download_path}...")
        extract_to_dir = os.path.join(download_dir, f"{core_name}_extracted") # 临时解压目录
        os.makedirs(extract_to_dir, exist_ok=True)
        with tarfile.open(temp_download_path, "r:gz") as tar:
            tar.extractall(path=extract_to_dir)
        final_executable_path_after_processing = find_executable_in_dir(extract_to_dir, [executable_name_in_archive, core_name])
        if final_executable_path_after_processing and final_executable_path_after_processing != target_binary_path:
             shutil.move(final_executable_path_after_processing, target_binary_path)
        if os.path.exists(extract_to_dir): # 清理临时解压目录
            shutil.rmtree(extract_to_dir)

    elif found_asset_name.endswith(".gz"): # 例如 clash.meta-linux-amd64-vX.Y.Z.gz
        # download_file 已经解压到 temp_download_path (原名)
        # 我们需要将它移动/重命名到 target_binary_path
        if temp_download_path != target_binary_path:
            shutil.move(temp_download_path, target_binary_path)
        else: # 如果 temp_download_path 就是 target_binary_path (不太可能，因为名字不同)
            pass 
        final_executable_path_after_processing = target_binary_path

    elif found_asset_name.endswith(".zip"):
        logger.info(f"Extracting {temp_download_path}...")
        extract_to_dir = os.path.join(download_dir, f"{core_name}_extracted_zip")
        os.makedirs(extract_to_dir, exist_ok=True)
        with zipfile.ZipFile(temp_download_path, 'r') as zip_ref:
            zip_ref.extractall(extract_to_dir)
        final_executable_path_after_processing = find_executable_in_dir(extract_to_dir, [executable_name_in_archive, core_name])
        if final_executable_path_after_processing and final_executable_path_after_processing != target_binary_path:
             shutil.move(final_executable_path_after_processing, target_binary_path)
        if os.path.exists(extract_to_dir):
            shutil.rmtree(extract_to_dir)
    else: # 直接是二进制文件
        if temp_download_path != target_binary_path:
            shutil.move(temp_download_path, target_binary_path)
        final_executable_path_after_processing = target_binary_path

    # 清理下载的原始压缩包 (如果它不是最终目标文件)
    if os.path.exists(temp_download_path) and temp_download_path != target_binary_path:
        try:
            if os.path.isfile(temp_download_path):
                os.remove(temp_download_path)
            # elif os.path.isdir(temp_download_path): # unlikely for temp_download_path itself
            #     shutil.rmtree(temp_download_path) 
        except Exception as e:
            logger.warning(f"Could not clean up downloaded archive {temp_download_path}: {e}")

    # 确认最终文件存在并且设置权限
    if os.path.exists(target_binary_path) and os.path.isfile(target_binary_path):
        try:
            os.chmod(target_binary_path, 0o755)
            logger.info(f"{core_name} binary successfully prepared at {target_binary_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to set executable permission on {target_binary_path}: {e}", exc_info=True)
            return False
    else:
        logger.error(f"Final executable {target_binary_path} not found after processing for {core_name}.")
        return False

async def get_clash_meta_binary():
    return await ensure_core_binary(
        core_name="Mihomo",
        github_api_url=settings.CLASH_META_LATEST_RELEASE_URL,
        target_binary_path=settings.CLASH_CORE_PATH,
        asset_keyword="mihomo", # 根据实际情况调整, e.g. "compatible"
        executable_name_in_archive="mihomo" # 解压后二进制文件的确切名称
    )

async def get_singbox_binary():
    return await ensure_core_binary(
        core_name="Sing-box",
        github_api_url=settings.SINGBOX_LATEST_RELEASE_URL,
        target_binary_path=settings.SINGBOX_CORE_PATH,
        asset_keyword="sing-box", # 根据实际情况调整
        executable_name_in_archive="sing-box" # 解压后二进制文件的确切名称
    )
# Example usage (typically called at application startup)
# import asyncio
# asyncio.run(get_clash_meta_binary())
# asyncio.run(get_singbox_binary())
