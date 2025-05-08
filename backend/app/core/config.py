import os

class Settings:
    PROJECT_NAME: str = "Proxy Geo Enhancer"
    API_V1_STR: str = "/api/v1" # 如果想版本化API

    # IP Geolocation API
    IP_API_URL: str = "http://ip-api.com/json?fields=countryCode,country,query" # query是出口IP

    # Paths for downloaded cores (relative to backend working directory)
    CORES_DIR: str = "downloaded_cores"
    CLASH_CORE_PATH: str = os.path.join(CORES_DIR, "clash-meta")
    SINGBOX_CORE_PATH: str = os.path.join(CORES_DIR, "sing-box")


    # GitHub API URLs for latest versions
    CLASH_META_LATEST_RELEASE_URL: str = "https://api.github.com/repos/MetaCubeX/Clash.Meta/releases/latest"
    SINGBOX_LATEST_RELEASE_URL: str = "https://api.github.com/repos/SagerNet/sing-box/releases/latest"

    # Temp config paths
    TEMP_DIR: str = "temp_configs"
    TEMP_CLASH_CONFIG_PATH: str = os.path.join(TEMP_DIR, "temp_clash_config.yaml")
    TEMP_SINGBOX_CONFIG_PATH: str = os.path.join(TEMP_DIR, "temp_singbox_config.json")
    TEMP_PROXY_PORT: int = 10808 # 临时代理端口

settings = Settings()

# Ensure temp_configs and downloaded_cores directories exist
os.makedirs(settings.CORES_DIR, exist_ok=True)
os.makedirs(settings.TEMP_DIR, exist_ok=True)