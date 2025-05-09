import base64
import asyncio
import yaml
import json
import httpx
import logging
import urllib.parse
from typing import List, Dict, Any, Optional
from backend.app.core.config import settings
from backend.app.core.ip_checker import get_exit_ip_country
from backend.app.core.proxy_manager import run_proxy_core, stop_proxy_core, monitor_process_output

logger = logging.getLogger(__name__)

# --- Node Parsing (Simplified Examples) ---
def parse_vmess_link(vmess_link: str) -> Optional[Dict[str, Any]]:
    if not vmess_link.startswith("vmess://"):
        return None
    try:
        decoded_json_str = base64.b64decode(vmess_link[8:]).decode('utf-8')
        data = json.loads(decoded_json_str)
        # Basic structure, more fields might be needed (tls, sni, etc.)
        return {
            "name": data.get("ps", "Unnamed VMess"),
            "type": "vmess",
            "server": data.get("add"),
            "port": int(data.get("port", 0)),
            "uuid": data.get("id"),
            "alterId": int(data.get("aid", 0)),
            "cipher": data.get("scy", "auto"), # protocol 'security' in clash
            "network": data.get("net"), # ws, tcp, etc.
            "ws-opts": {"path": data.get("path", "/"), "headers": {"Host": data.get("host", "")}} if data.get("net") == "ws" else None,
            "tls": data.get("tls") == "tls",
            "sni": data.get("sni", data.get("host", "")) if data.get("tls") == "tls" else None,
            # Store original link for potential later use or debugging
            "_original_link": vmess_link
        }
    except Exception as e:
        logger.error(f"Failed to parse VMess link {vmess_link}: {e}")
        return None

def parse_trojan_link(trojan_link: str) -> Optional[Dict[str, Any]]:
    # trojan://password@server:port#remarks
    # trojan://password@server:port?sni=example.com&allowInsecure=0#remarks
    if not trojan_link.startswith("trojan://"):
        return None
    try:
        parts = urllib.parse.urlparse(trojan_link)
        password = parts.username
        server = parts.hostname
        port = parts.port
        remarks = urllib.parse.unquote(parts.fragment) if parts.fragment else f"{server}:{port}"
        
        query_params = urllib.parse.parse_qs(parts.query)
        sni = query_params.get('sni', [None])[0]
        allow_insecure = query_params.get('allowInsecure', ['0'])[0] == '1' # Clash uses skip-cert-verify
        # Other params like peer, alpn, etc. could be here

        return {
            "name": remarks,
            "type": "trojan",
            "server": server,
            "port": int(port),
            "password": password,
            "sni": sni if sni else server, # SNI is important for Trojan
            "skip-cert-verify": allow_insecure, # For Clash naming
            # "allowInsecure": allow_insecure, # For Sing-box naming
            "_original_link": trojan_link
        }
    except Exception as e:
        logger.error(f"Failed to parse Trojan link {trojan_link}: {e}")
        return None

# Add parsers for Shadowsocks (ss://), VLESS, Hysteria2 as needed.
# Hysteria2 is complex, often provided as JSON snippet.

async def fetch_and_parse_subscription(url: str) -> List[Dict[str, Any]]:
    nodes = []
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            content = response.text

        # Try to determine content type (Base64 blob, Clash YAML, SingBox JSON)
        if "proxies:" in content and ("Proxy" in content or "proxy-groups" in content): # Basic Clash YAML check
            logger.info(f"Parsing {url} as Clash YAML")
            clash_config = yaml.safe_load(content)
            proxies_data = clash_config.get('proxies', [])
            for proxy_data in proxies_data:
                # Convert Clash proxy dict to our internal common format
                # This needs to be comprehensive to map all fields
                nodes.append({**proxy_data, "_source_format": "clash_dict"})
            logger.info(f"Parsed {len(nodes)} nodes from Clash YAML.")

        elif content.startswith("{") and "\"outbounds\"" in content : # Basic SingBox JSON check
            logger.info(f"Parsing {url} as SingBox JSON")
            sb_config = json.loads(content)
            # SingBox outbounds are more complex. Need to map them.
            # For simplicity, assuming we can extract relevant fields.
            for outbound in sb_config.get("outbounds", []):
                if outbound.get("type") in ["vmess", "trojan", "shadowsocks", "vless", "hysteria2"]:
                     nodes.append({**outbound, "_source_format": "singbox_dict"}) # Simplification
            logger.info(f"Parsed {len(nodes)} nodes from SingBox JSON.")

        else: # Assume Base64 encoded list of links (common for V2RayN, Shadowrocket)
            logger.info(f"Parsing {url} as Base64 encoded links")
            try:
                decoded_content = base64.b64decode(content).decode('utf-8')
                links = decoded_content.splitlines()
                for link in links:
                    link = link.strip()
                    if link.startswith("vmess://"):
                        node = parse_vmess_link(link)
                        if node: nodes.append(node)
                    elif link.startswith("trojan://"):
                        node = parse_trojan_link(link)
                        if node: nodes.append(node)
                    # Add elif for ss://, vless:// etc.
                logger.info(f"Parsed {len(nodes)} nodes from Base64 link list.")
            except Exception as e:
                logger.error(f"Failed to decode or parse Base64 content from {url}: {e}")
                # Could try parsing line by line if not base64
                raw_links = content.splitlines()
                for link in raw_links:
                    link = link.strip()
                    if link.startswith("vmess://"):
                        node = parse_vmess_link(link)
                        if node: nodes.append(node)
                    elif link.startswith("trojan://"):
                        node = parse_trojan_link(link)
                        if node: nodes.append(node)


    except httpx.RequestError as e:
        logger.error(f"Failed to fetch subscription from {url}: {e}")
    except yaml.YAMLError as e:
        logger.error(f"Failed to parse YAML from {url}: {e}")
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON from {url}: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred processing {url}: {e}")
    return nodes


async def test_and_rename_node(node: Dict[str, Any], use_clash: bool = True) -> Dict[str, Any]:
    """
    Tests a single node by creating a temporary config for Clash/Singbox,
    running it, querying IP API, and then stopping it.
    Modifies node['name'] with country prefix.
    """
    original_name = node.get("name", "Unnamed Node")
    logger.info(f"Testing node: {original_name} (type: {node.get('type')})")

    # 1. Generate temporary minimal proxy config
    temp_config_content = None
    core_type_to_run = "clash" if use_clash else "singbox" # Default to Clash for now
    temp_config_path = settings.TEMP_CLASH_CONFIG_PATH if use_clash else settings.TEMP_SINGBOX_CONFIG_PATH
    proxy_core_path = settings.CLASH_CORE_PATH if use_clash else settings.SINGBOX_CORE_PATH
    
    # Ensure the selected core binary exists
    if not os.path.exists(proxy_core_path) or os.path.getsize(proxy_core_path) == 0:
        logger.error(f"{core_type_to_run} core binary missing or empty at {proxy_core_path}. Skipping test for {original_name}")
        node["name"] = f"[ER-CORE] {original_name}"
        return node

    if use_clash:
        # Create a minimal Clash config with only this proxy
        # Important: Clash needs 'proxies' and 'proxy-groups'
        # For global mode, we'll point a group to this single proxy and select it.
        clash_proxy_config = {**node} # Make a copy
        # Remove helper fields not part of clash spec, or convert them
        clash_proxy_config.pop("_original_link", None)
        clash_proxy_config.pop("_source_format", None)
        # Ensure all necessary fields for the specific type are present and correctly named for Clash
        # e.g. vmess `security` field in Clash is `cipher` in some URI schemes.

        temp_config_content = {
            "port": settings.TEMP_PROXY_PORT, # HTTP proxy port
            "socks-port": settings.TEMP_PROXY_PORT + 1, # SOCKS proxy port
            "allow-lan": True,
            "mode": "global", # Use global mode for testing
            "log-level": "silent", # "info" or "debug" for verbose logs
            "external-controller": f"127.0.0.1:{settings.TEMP_PROXY_PORT+2}", # Not strictly needed for this use case
            "proxies": [clash_proxy_config],
            "proxy-groups": [
                {
                    "name": "GLOBAL",
                    "type": "select",
                    "proxies": [node["name"]] # Use the node's name
                }
            ],
            "rules": ["MATCH,GLOBAL"] # Ensure global routing
        }
        with open(temp_config_path, 'w', encoding='utf-8') as f:
            yaml.dump(temp_config_content, f, allow_unicode=True)
    else: # Singbox
        # Create a minimal Singbox config
        # Singbox needs "log", "inbounds", "outbounds"
        # The node itself is an outbound.
        # Inbound type "mixed" can listen for HTTP and SOCKS5
        singbox_outbound_config = {**node} # Make a copy
        singbox_outbound_config.pop("_original_link", None)
        singbox_outbound_config.pop("_source_format", None)
        singbox_outbound_config["tag"] = "proxy" # Required field for outbounds

        temp_config_content = {
            "log": {"level": "warn", "output": "stderr"},
            "inbounds": [
                {
                    "type": "mixed",
                    "tag": "mixed-in",
                    "listen": "127.0.0.1",
                    "listen_port": settings.TEMP_PROXY_PORT
                }
            ],
            "outbounds": [
                singbox_outbound_config,
                { # Default direct outbound for DNS or other needs if any
                    "type": "direct",
                    "tag": "direct"
                }
            ],
             "route": { # Route all traffic to the "proxy" outbound
                "rules": [{"outbound": "proxy"}],
                "final": "proxy" # Fallback to proxy
            }
        }
        with open(temp_config_path, 'w', encoding='utf-8') as f:
            json.dump(temp_config_content, f, indent=2)

    # 2. Run proxy core
    process = None
    country_code = "XX" # Default unknown
    try:
        process = await run_proxy_core(proxy_core_path, temp_config_path, core_type_to_run)
        # Start monitoring output in the background (optional, for debugging)
        # output_monitor_task = asyncio.create_task(monitor_process_output(process, core_type_to_run))
        
        # Allow some time for the proxy to fully initialize and listen
        await asyncio.sleep(3) # Adjust if proxy takes longer

        # Check if process is still running
        if process.returncode is not None:
            logger.error(f"{core_type_to_run} for node {original_name} exited prematurely with code {process.returncode}.")
            # Read stderr if available
            if process.stderr:
                error_output = await process.stderr.read()
                logger.error(f"Stderr from {core_type_to_run}: {error_output.decode(errors='ignore')}")
            country_code = "FL" # Failed to start
        else:
            # 3. Query IP API through the proxy
            # HTTP proxy is what we need for httpx typically
            local_proxy_url = f"http://127.0.0.1:{settings.TEMP_PROXY_PORT}"
            country_code = await get_exit_ip_country(local_proxy_url)
            logger.info(f"Node {original_name} tested. Country: {country_code}")

    except FileNotFoundError:
        logger.error(f"{core_type_to_run} core not found. Cannot test {original_name}.")
        country_code = "NC" # No Core
    except Exception as e:
        logger.error(f"Error during testing node {original_name} with {core_type_to_run}: {e}", exc_info=True)
        country_code = "TE" # Test Error
    finally:
        # 4. Stop proxy core
        if process:
            await stop_proxy_core(process, core_type_to_run)
        # if 'output_monitor_task' in locals() and not output_monitor_task.done():
        #     output_monitor_task.cancel()
        
        # Clean up temp config file
        try:
            if os.path.exists(temp_config_path):
                os.remove(temp_config_path)
        except Exception as e:
            logger.warning(f"Could not remove temp config {temp_config_path}: {e}")


    node["name"] = f"[{country_code}] {original_name}"
    return node


async def process_subscriptions(urls: List[str], output_format: str = "clash") -> str:
    all_nodes = []
    for url in urls:
        logger.info(f"Fetching and parsing subscription: {url}")
        nodes = await fetch_and_parse_subscription(url)
        all_nodes.extend(nodes)

    logger.info(f"Total nodes collected: {len(all_nodes)}. Now testing...")
    
    # You might want to use Clash for certain node types and Singbox for others,
    # or make it configurable. For now, defaulting to Clash for testing.
    # Hysteria2 is better supported in Singbox usually.
    # If a node is already in Singbox format, maybe test with Singbox.
    
    # Limit concurrent tests to avoid overwhelming the system or IP API rate limits
    semaphore = asyncio.Semaphore(5) # Max 5 concurrent tests
    tested_nodes_tasks = []

    for node in all_nodes:
        # Basic decision: if node type is hysteria2 or quic, prefer singbox
        # Or if source format was singbox.
        node_type = node.get("type", "").lower()
        is_hysteria = "hysteria" in node_type or "hy2" in node_type
        is_quic_based = "quic" in node.get("network", "").lower()
        # For now, use Clash by default for wide compatibility of other types
        # This logic can be expanded.
        # For this example, let's try to use Clash more broadly if its core is available
        # because its single-node config is often simpler to craft.
        use_clash_for_test = True
        if (is_hysteria or is_quic_based) and os.path.exists(settings.SINGBOX_CORE_PATH) and os.path.getsize(settings.SINGBOX_CORE_PATH) > 0 :
            use_clash_for_test = False
        elif not (os.path.exists(settings.CLASH_CORE_PATH) and os.path.getsize(settings.CLASH_CORE_PATH) > 0):
            # If Clash core is missing, but Singbox is there, try Singbox
            if os.path.exists(settings.SINGBOX_CORE_PATH) and os.path.getsize(settings.SINGBOX_CORE_PATH) > 0:
                use_clash_for_test = False
            else:
                logger.warning(f"No suitable proxy core (Clash or Singbox) found for testing node {node.get('name')}. Skipping test.")
                node["name"] = f"[SKP-CORE] {node.get('name', 'Unnamed')}"
                # No async task for this node, just append it as is (or with error prefix)
                # We'll add it to a list of already processed/skipped nodes.
                # For simplicity, let's just create a task that returns the node as is.
                async def return_node_as_is(n): return n
                task = return_node_as_is(node)
                tested_nodes_tasks.append(task)
                continue


        async def _acquire_and_test(n, use_clash_flag):
            async with semaphore:
                return await test_and_rename_node(n, use_clash=use_clash_flag)
        
        tested_nodes_tasks.append(_acquire_and_test(node, use_clash_for_test))

    modified_nodes = await asyncio.gather(*tested_nodes_tasks)
    
    # Filter out nodes that completely failed or couldn't be processed if needed
    # modified_nodes = [n for n in modified_nodes if not n["name"].startswith("[ER-")]

    logger.info(f"Finished testing. Generating output in {output_format} format.")

    # --- Output Generation ---
    # This also needs to be robust, converting internal node structure to target format
    if output_format == "clash":
        # Construct a valid Clash YAML
        clash_output = {
            "proxies": [],
            "proxy-groups": [ # Add some basic groups for usability
                {
                    "name": "自动选择", "type": "url-test", "proxies": [],
                    "url": "http://www.gstatic.com/generate_204", "interval": 300
                },
                {
                    "name": "手动选择", "type": "select", "proxies": []
                }
            ],
            "rules": [ # Basic rules
                "DOMAIN-SUFFIX,google.com,自动选择",
                "MATCH,手动选择"
            ]
        }
        node_names_for_groups = []
        for node_data in modified_nodes:
            # Convert our internal representation back to Clash proxy dict
            # This is inverse of parsing logic. Ensure all fields are correct.
            clash_node = {k: v for k, v in node_data.items() if not k.startswith('_')}
            clash_output["proxies"].append(clash_node)
            node_names_for_groups.append(node_data["name"])
        
        if not node_names_for_groups: # Handle case with no valid nodes
             clash_output["proxies"] = [{"name":"NO-NODES-VALID","type":"direct"}] # Placeholder
             node_names_for_groups = ["NO-NODES-VALID"]


        clash_output["proxy-groups"][0]["proxies"] = node_names_for_groups
        clash_output["proxy-groups"][1]["proxies"] = ["自动选择"] + node_names_for_groups # Manually select auto or individual

        return yaml.dump(clash_output, allow_unicode=True, sort_keys=False)

    elif output_format == "singbox":
        # Construct a valid Singbox JSON
        # Needs more complex structure (log, dns, inbounds, outbounds, route)
        sb_output = {
            "log": {"level": "info", "timestamp": True},
            "dns": {"servers": [{"address": "8.8.8.8"}, {"address": "1.1.1.1"}]}, # Basic DNS
            "inbounds": [ # Example inbounds
                {"type": "mixed", "tag": "mixed-in", "listen": "::", "listen_port": 2080},
            ],
            "outbounds": [],
            "route": { "rules": [], "final": "DIRECT" } # Default to DIRECT if no rules match
        }
        default_outbounds = [{"type": "direct", "tag": "DIRECT"}] # Must have a DIRECT

        processed_outbounds = []
        for node_data in modified_nodes:
            # Convert internal to Singbox outbound dict
            sb_node = {k: v for k, v in node_data.items() if not k.startswith('_')}
            sb_node["tag"] = node_data["name"] # Use new name as tag
            processed_outbounds.append(sb_node)
        
        if not processed_outbounds: # Handle no valid nodes
            sb_output["outbounds"] = default_outbounds
        else:
            sb_output["outbounds"] = processed_outbounds + default_outbounds
            # Add a simple rule to use the first available proxy as default for demo
            sb_output["route"]["rules"].append({"outbound": processed_outbounds[0]["tag"]})
            sb_output["route"]["final"] = processed_outbounds[0]["tag"]


        return json.dumps(sb_output, indent=2, ensure_ascii=False)

    elif output_format == "v2rayn": # Base64 list of links
        output_links = []
        for node_data in modified_nodes:
            # Reconstruct the original link type if stored, or try to generate
            # This is tricky if not all original info was preserved or if it was parsed from YAML/JSON
            if "_original_link" in node_data and node_data["name"].startswith("["): # Successfully tested
                # How to update name in original link?
                # For VMess, need to update "ps" in base64 part.
                # For Trojan, update #remark part.
                # This is non-trivial. A simpler approach for now:
                # If we can re-generate a link from node_data:
                if node_data.get("type") == "vmess":
                    vmess_obj = {
                        "v": "2", "ps": node_data["name"], "add": node_data["server"],
                        "port": str(node_data["port"]), "id": node_data["uuid"],
                        "aid": str(node_data.get("alterId", 0)), "net": node_data.get("network", "tcp"),
                        "type": "none", # http obfs type, usually none for ws/tcp
                        "host": node_data.get("ws-opts", {}).get("headers", {}).get("Host", "") if node_data.get("network") == "ws" else "",
                        "path": node_data.get("ws-opts", {}).get("path", "") if node_data.get("network") == "ws" else "",
                        "tls": "tls" if node_data.get("tls") else "",
                        "sni": node_data.get("sni", "") if node_data.get("tls") else "",
                        "scy": node_data.get("cipher", "auto")
                    }
                    # Remove empty fields that might break some clients
                    vmess_obj_clean = {k: v for k, v in vmess_obj.items() if v}
                    link = "vmess://" + base64.b64encode(json.dumps(vmess_obj_clean).encode()).decode()
                    output_links.append(link)
                elif node_data.get("type") == "trojan":
                    # trojan://password@server:port?sni=example.com#NewName
                    query_params = {}
                    if node_data.get("sni"): query_params["sni"] = node_data["sni"]
                    if node_data.get("skip-cert-verify"): query_params["allowInsecure"] = "1"
                    # ... other params if any
                    query_string = urllib.parse.urlencode(query_params)
                    
                    link = f"trojan://{node_data['password']}@{node_data['server']}:{node_data['port']}"
                    if query_string:
                        link += f"?{query_string}"
                    link += f"#{urllib.parse.quote(node_data['name'])}"
                    output_links.append(link)

                # Add other types (SS, VLESS) link generation
            elif "_original_link" in node_data: # Test failed or skipped, return original
                 output_links.append(node_data["_original_link"])


        return base64.b64encode("\n".join(output_links).encode()).decode()

    return "Error: Unsupported output format"
