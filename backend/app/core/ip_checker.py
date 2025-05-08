import httpx
import logging
from backend.app.core.config import settings

logger = logging.getLogger(__name__)

async def get_exit_ip_country(proxy_address: str): # proxy_address like "http://127.0.0.1:10808"
    """
    Fetches the exit IP's country by routing the request through the provided proxy.
    """
    try:
        async with httpx.AsyncClient(proxies={"http://": proxy_address, "https://": proxy_address}, timeout=15.0, verify=False) as client:
            # Using verify=False for simplicity with local proxies, ideally configure certs if needed
            response = await client.get(settings.IP_API_URL)
            response.raise_for_status()
            data = response.json()
            country_code = data.get("countryCode")
            # country_name = data.get("country")
            # actual_ip = data.get("query")
            # logger.info(f"IP check via {proxy_address}: Country={country_code}, IP={actual_ip}")
            if country_code:
                return country_code.upper() # e.g., "US"
            logger.warning(f"Country code not found in IP API response: {data}")
            return "XX" # Unknown
    except httpx.TimeoutException:
        logger.error(f"Timeout when checking IP via proxy {proxy_address} for {settings.IP_API_URL}")
        return "TO" # Timeout
    except httpx.RequestError as e:
        logger.error(f"Request error checking IP via proxy {proxy_address}: {e}")
        return "ER" # Error
    except Exception as e:
        logger.error(f"Unexpected error checking IP via proxy {proxy_address}: {e}")
        return "XX" # Unknown / Exception