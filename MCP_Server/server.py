# ableton_mcp_server.py — AbletonMCP Beta
from mcp.server.fastmcp import FastMCP, Context
import socket
import json
import logging
import time
from dataclasses import dataclass
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Any, List, Union
import uuid
import base64
import struct
import os
import threading
import collections
from datetime import datetime, timezone

# Configure logging
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("AbletonMCP-Beta")

@dataclass
class AbletonConnection:
    host: str
    port: int
    sock: socket.socket = None
    
    def connect(self) -> bool:
        """Connect to the Ableton Remote Script socket server"""
        if self.sock:
            return True

        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(5.0)
            self.sock.connect((self.host, self.port))
            self._recv_buffer = ""  # Clear buffer on new connection
            logger.info(f"Connected to Ableton at {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Ableton: {str(e)}")
            if self.sock:
                try:
                    self.sock.close()
                except Exception:
                    pass
            self.sock = None
            return False
    
    def disconnect(self):
        """Disconnect from the Ableton Remote Script"""
        if self.sock:
            try:
                self.sock.close()
            except Exception as e:
                logger.error(f"Error disconnecting from Ableton: {str(e)}")
            finally:
                self.sock = None

    def __post_init__(self):
        self._recv_buffer = ""

    def receive_full_response(self, sock, buffer_size=8192, timeout=15.0):
        """Receive a complete newline-delimited JSON response and return the parsed object"""
        sock.settimeout(timeout)

        try:
            while True:
                # Check if we already have a complete line in the buffer
                if '\n' in self._recv_buffer:
                    line, self._recv_buffer = self._recv_buffer.split('\n', 1)
                    line = line.strip()
                    if line:
                        result = json.loads(line)
                        logger.info(f"Received complete response ({len(line)} chars)")
                        return result

                try:
                    chunk = sock.recv(buffer_size)
                    if not chunk:
                        raise Exception("Connection closed before receiving any data")

                    self._recv_buffer += chunk.decode('utf-8')
                except socket.timeout:
                    logger.warning("Socket timeout during receive")
                    raise
                except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
                    logger.error(f"Socket connection error during receive: {str(e)}")
                    raise
        except (socket.timeout, json.JSONDecodeError):
            raise
        except Exception as e:
            logger.error(f"Error during receive: {str(e)}")
            raise

    def _reconnect(self) -> bool:
        """Force a fresh reconnection, clearing all state."""
        logger.info("Forcing reconnection to Ableton...")
        self.disconnect()
        self._recv_buffer = ""
        return self.connect()

    def send_command(self, command_type: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Send a command to Ableton and return the response.

        Includes automatic retry: if the first attempt fails due to a
        socket error, the connection is reset and the command is retried once.
        """
        max_attempts = 2

        for attempt in range(1, max_attempts + 1):
            if not self.sock and not self.connect():
                raise ConnectionError("Not connected to Ableton")

            command = {
                "type": command_type,
                "params": params or {}
            }

            try:
                logger.info(f"Sending command: {command_type} (attempt {attempt})")

                # Send the command as newline-delimited JSON
                self.sock.sendall((json.dumps(command) + '\n').encode('utf-8'))

                # Set timeout based on command type
                timeout = 15.0
                # Receive the response (already parsed by receive_full_response)
                response = self.receive_full_response(self.sock, timeout=timeout)
                logger.info(f"Response status: {response.get('status', 'unknown')}")

                if response.get("status") == "error":
                    logger.error(f"Ableton error: {response.get('message')}")
                    raise Exception(response.get("message", "Unknown error from Ableton"))

                return response.get("result", {})

            except Exception as e:
                logger.error(f"Command '{command_type}' attempt {attempt} failed: {str(e)}")
                # Close the broken socket and clear buffer
                self.disconnect()
                self._recv_buffer = ""

                if attempt < max_attempts:
                    # Wait briefly then retry with a fresh connection
                    time.sleep(0.3)
                    if not self.connect():
                        raise ConnectionError("Failed to reconnect to Ableton")
                    logger.info("Reconnected, retrying command...")
                else:
                    raise Exception(f"Command '{command_type}' failed after {max_attempts} attempts: {str(e)}")


@dataclass
class M4LConnection:
    """UDP connection to the Max for Live bridge device.

    The M4L bridge provides deep LOM access for hidden device parameters.
    Communication uses two UDP ports:
      - send_port (9878): MCP server → M4L device (commands)
      - recv_port (9879): M4L device → MCP server (responses)
    """
    send_host: str = "localhost"
    send_port: int = 9878
    recv_port: int = 9879
    send_sock: socket.socket = None
    recv_sock: socket.socket = None
    _connected: bool = False

    def connect(self) -> bool:
        """Set up UDP sockets for M4L communication."""
        try:
            self.send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.recv_sock.bind(("localhost", self.recv_port))
            self.recv_sock.settimeout(5.0)
            self._connected = True
            logger.info(f"M4L UDP sockets ready (send→:{self.send_port}, recv←:{self.recv_port})")
            return True
        except Exception as e:
            logger.error(f"Failed to set up M4L UDP connection: {str(e)}")
            self.disconnect()
            return False

    def disconnect(self):
        """Close UDP sockets."""
        for s in (self.send_sock, self.recv_sock):
            if s:
                try:
                    s.close()
                except Exception:
                    pass
        self.send_sock = None
        self.recv_sock = None
        self._connected = False

    @staticmethod
    def _build_osc_message(address: str, osc_args: list = None) -> bytes:
        """Build an OSC message with typed arguments.

        Each arg is a tuple of (type, value):
          ('i', 42)  — 32-bit int
          ('f', 3.14) — 32-bit float
          ('s', 'hi') — null-terminated padded string
        """
        def _osc_string(s: str) -> bytes:
            b = s.encode("utf-8") + b"\x00"
            b += b"\x00" * ((4 - len(b) % 4) % 4)
            return b

        osc_args = osc_args or []
        msg = _osc_string(address)
        type_tag = "," + "".join(t for t, _ in osc_args)
        msg += _osc_string(type_tag)
        for t, v in osc_args:
            if t == "s":
                msg += _osc_string(str(v))
            elif t == "i":
                msg += struct.pack(">i", int(v))
            elif t == "f":
                msg += struct.pack(">f", float(v))
        return msg

    def _build_osc_packet(self, command_type: str, params: Dict[str, Any], request_id: str) -> bytes:
        """Build the OSC packet for a given command type."""
        if command_type == "ping":
            return self._build_osc_message("/ping", [("s", request_id)])
        elif command_type == "discover_params":
            return self._build_osc_message("/discover_params", [
                ("i", params["track_index"]),
                ("i", params["device_index"]),
                ("s", request_id),
            ])
        elif command_type == "get_hidden_params":
            return self._build_osc_message("/get_hidden_params", [
                ("i", params["track_index"]),
                ("i", params["device_index"]),
                ("s", request_id),
            ])
        elif command_type == "set_hidden_param":
            return self._build_osc_message("/set_hidden_param", [
                ("i", params["track_index"]),
                ("i", params["device_index"]),
                ("i", params["parameter_index"]),
                ("f", params["value"]),
                ("s", request_id),
            ])
        elif command_type == "batch_set_hidden_params":
            params_json = json.dumps(params["parameters"])
            params_b64 = base64.b64encode(params_json.encode("utf-8")).decode("ascii")
            return self._build_osc_message("/batch_set_hidden_params", [
                ("i", params["track_index"]),
                ("i", params["device_index"]),
                ("s", params_b64),
                ("s", request_id),
            ])
        else:
            raise ValueError(f"Unknown M4L command: {command_type}")

    def send_command(self, command_type: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Send a command to the M4L bridge using native OSC messages.

        Includes automatic reconnect: if the send or receive fails, the
        UDP sockets are recreated and the command is retried once.
        """
        params = params or {}
        request_id = str(uuid.uuid4())[:8]
        osc = self._build_osc_packet(command_type, params, request_id)

        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            if not self._connected:
                if not self.connect():
                    raise ConnectionError("Could not establish M4L UDP connection.")

            # Drain any stale data in the recv socket before sending
            self.recv_sock.setblocking(False)
            try:
                while True:
                    self.recv_sock.recvfrom(65535)
            except (BlockingIOError, OSError):
                pass
            self.recv_sock.setblocking(True)
            self.recv_sock.settimeout(5.0)

            try:
                self.send_sock.sendto(osc, (self.send_host, self.send_port))
            except Exception as e:
                logger.error(f"Failed to send UDP command to M4L (attempt {attempt}): {str(e)}")
                if attempt < max_attempts:
                    self.disconnect()
                    time.sleep(0.2)
                    continue
                raise ConnectionError("Failed to send command to M4L bridge.")

            try:
                data, _addr = self.recv_sock.recvfrom(65535)
                return self._parse_m4l_response(data)
            except socket.timeout:
                logger.warning(f"M4L response timeout (attempt {attempt})")
                if attempt < max_attempts:
                    self.disconnect()
                    time.sleep(0.2)
                    continue
                raise Exception("Timeout waiting for M4L bridge response. Is the M4L device loaded?")

    @staticmethod
    def _parse_m4l_response(data: bytes) -> Dict[str, Any]:
        """Parse the response from the M4L bridge.

        Max's udpsend wraps the base64 string as an OSC message:
          [base64_string\\0...padding][,\\0\\0\\0]
        The OSC address (first null-terminated string) contains our
        base64-encoded JSON response.
        """
        # Extract the OSC address = first null-terminated string in the packet
        null_pos = data.find(b"\x00")
        if null_pos > 0:
            osc_address = data[:null_pos].decode("utf-8", errors="replace").strip()
        else:
            osc_address = data.decode("utf-8", errors="replace").strip()

        # The OSC address is our base64-encoded JSON response
        # (udpsend uses the outlet symbol as the OSC address)
        try:
            decoded = base64.b64decode(osc_address).decode("utf-8")
            return json.loads(decoded)
        except Exception:
            pass

        # Fallback: try raw JSON (in case response wasn't base64-encoded)
        try:
            return json.loads(osc_address)
        except (json.JSONDecodeError, ValueError):
            pass

        # Last resort: strip all nulls and try
        cleaned = data.replace(b"\x00", b"").strip()
        text = cleaned.decode("utf-8", errors="replace").strip()
        # Remove trailing comma from OSC type tag
        text = text.rstrip(",").strip()
        try:
            decoded = base64.b64decode(text).decode("utf-8")
            return json.loads(decoded)
        except Exception:
            pass

        raise json.JSONDecodeError("Could not parse M4L response", text, 0)

    def ping(self) -> bool:
        """Check if the M4L bridge device is responding."""
        try:
            result = self.send_command("ping")
            return result.get("status") == "success"
        except Exception:
            return False


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    """Manage server startup and shutdown lifecycle"""
    global _server_start_time
    try:
        logger.info("AbletonMCP Beta server starting up")
        _server_start_time = time.time()

        try:
            ableton = get_ableton_connection()
            logger.info("Successfully connected to Ableton on startup")
        except Exception as e:
            logger.warning(f"Could not connect to Ableton on startup: {str(e)}")
            logger.warning("Make sure the Ableton Remote Script is running")

        # Auto-connect M4L bridge in background (device may need time to init)
        def _m4l_auto_connect():
            """Background thread: create UDP sockets once, retry ping until M4L responds."""
            global _m4l_connection

            # Create sockets once — don't tear them down between retries
            conn = M4LConnection()
            if not conn.connect():
                logger.warning("M4L auto-connect: could not bind UDP sockets")
                return

            _m4l_connection = conn

            # Build a raw OSC ping packet
            ping_id = "autocon"
            ping_osc = M4LConnection._build_osc_message("/ping", [("s", ping_id)])

            for attempt in range(1, 16):  # 15 attempts, ~2s apart
                try:
                    # Drain stale data
                    conn.recv_sock.setblocking(False)
                    try:
                        while True:
                            conn.recv_sock.recvfrom(65535)
                    except (BlockingIOError, OSError):
                        pass
                    conn.recv_sock.setblocking(True)
                    conn.recv_sock.settimeout(2.0)

                    # Send ping
                    conn.send_sock.sendto(ping_osc, (conn.send_host, conn.send_port))

                    # Wait for response
                    data, _ = conn.recv_sock.recvfrom(65535)
                    result = conn._parse_m4l_response(data)
                    if result.get("status") == "success":
                        logger.info(f"M4L bridge auto-connected on attempt {attempt}")
                        _m4l_ping_cache["result"] = True
                        _m4l_ping_cache["timestamp"] = time.time()
                        return
                except socket.timeout:
                    logger.info(f"M4L auto-connect {attempt}/15: no response, retrying...")
                except Exception as e:
                    logger.info(f"M4L auto-connect {attempt}/15: {str(e)}")
                time.sleep(2)
            logger.warning("M4L bridge not available after 15 attempts — will retry when needed")

        threading.Thread(target=_m4l_auto_connect, daemon=True, name="m4l-auto-connect").start()

        # Start web dashboard on background thread
        try:
            _start_dashboard_server()
        except Exception as e:
            logger.warning(f"Dashboard failed to start: {e}")

        yield {}
    finally:
        _stop_dashboard_server()
        global _ableton_connection, _m4l_connection
        if _ableton_connection:
            logger.info("Disconnecting from Ableton on shutdown")
            _ableton_connection.disconnect()
            _ableton_connection = None
        if _m4l_connection:
            logger.info("Disconnecting M4L bridge on shutdown")
            _m4l_connection.disconnect()
            _m4l_connection = None
        logger.info("AbletonMCP Beta server shut down")

# Create the MCP server with lifespan support
mcp = FastMCP(
    "AbletonMCP-Beta",
    lifespan=server_lifespan
)

# Global connections
_ableton_connection = None
_m4l_connection = None

# v1.6.0 feature stores (in-memory, lost on restart)
_snapshot_store: Dict[str, Dict[str, Any]] = {}
_macro_store: Dict[str, Dict[str, Any]] = {}
_param_map_store: Dict[str, Dict[str, Any]] = {}

# Web dashboard state
_server_start_time: float = 0.0
_tool_call_log: collections.deque = collections.deque(maxlen=50)
_tool_call_counts: Dict[str, int] = {}
_tool_call_lock = threading.Lock()
_dashboard_server = None
DASHBOARD_PORT = int(os.environ.get("ABLETON_MCP_DASHBOARD_PORT", "9880"))
_server_log_buffer: collections.deque = collections.deque(maxlen=200)
_server_log_lock = threading.Lock()


class _DashboardLogHandler(logging.Handler):
    """Captures log records into the dashboard ring buffer."""

    def emit(self, record):
        try:
            entry = {
                "ts": datetime.now().strftime("%H:%M:%S"),
                "level": record.levelname,
                "msg": record.getMessage(),
            }
            with _server_log_lock:
                _server_log_buffer.append(entry)
        except Exception:
            pass


_dashboard_log_handler = _DashboardLogHandler()
logging.getLogger().addHandler(_dashboard_log_handler)

# M4L ping cache (avoids 5s UDP timeout on every dashboard refresh)
_m4l_ping_cache = {"result": False, "timestamp": 0.0}
_M4L_PING_CACHE_TTL = 5.0


# ---------------------------------------------------------------------------
# Tool call instrumentation — captures all 81 tool calls for the dashboard
# ---------------------------------------------------------------------------
_original_call_tool = mcp.call_tool


async def _instrumented_call_tool(name: str, arguments: dict) -> Any:
    """Wrap every tool call to record metrics for the dashboard."""
    start = time.time()
    error_msg = None
    try:
        result = await _original_call_tool(name, arguments)
        return result
    except Exception as e:
        error_msg = str(e)
        raise
    finally:
        duration = time.time() - start
        entry = {
            "tool": name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_ms": round(duration * 1000, 1),
            "error": error_msg,
            "args_summary": _summarize_args(arguments),
        }
        with _tool_call_lock:
            _tool_call_log.append(entry)
            _tool_call_counts[name] = _tool_call_counts.get(name, 0) + 1


mcp.call_tool = _instrumented_call_tool


def _summarize_args(args: dict) -> str:
    """Create a short summary of tool arguments for the dashboard log."""
    if not args:
        return ""
    parts = []
    for k, v in list(args.items())[:3]:
        sv = str(v)
        if len(sv) > 40:
            sv = sv[:37] + "..."
        parts.append(f"{k}={sv}")
    suffix = f" +{len(args)-3} more" if len(args) > 3 else ""
    return ", ".join(parts) + suffix


# ---------------------------------------------------------------------------
# Web Status Dashboard
# ---------------------------------------------------------------------------

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AbletonMCP Beta — Dashboard</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace;
    background: #0d1117; color: #c9d1d9; line-height: 1.5;
  }
  .container { max-width: 960px; margin: 0 auto; padding: 24px; }
  h1 { color: #58a6ff; font-size: 1.6rem; margin-bottom: 4px; }
  .subtitle { color: #8b949e; font-size: 0.85rem; margin-bottom: 24px; }
  .grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px; margin-bottom: 24px;
  }
  .card {
    background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px;
  }
  .card-label {
    font-size: 0.75rem; color: #8b949e; text-transform: uppercase; letter-spacing: 0.05em;
  }
  .card-value { font-size: 1.5rem; font-weight: 600; margin-top: 4px; }
  .status-ok  { color: #3fb950; }
  .status-err { color: #f85149; }
  .status-warn { color: #d29922; }
  table { width: 100%; border-collapse: collapse; }
  th {
    text-align: left; color: #8b949e; font-size: 0.75rem; text-transform: uppercase;
    padding: 8px 12px; border-bottom: 1px solid #30363d;
  }
  td { padding: 6px 12px; border-bottom: 1px solid #21262d; font-size: 0.85rem; }
  tr:hover { background: #161b22; }
  .error-cell { color: #f85149; }
  .section {
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    padding: 16px; margin-bottom: 24px;
  }
  .section h2 { font-size: 1rem; color: #58a6ff; margin-bottom: 12px; }
  .refresh-bar {
    display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;
  }
  .refresh-bar span { font-size: 0.75rem; color: #8b949e; }
  #countdown { color: #58a6ff; }
  .bar-row {
    display: flex; align-items: center; margin-bottom: 6px; font-size: 0.8rem;
  }
  .bar-name { width: 240px; color: #8b949e; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .bar-track { flex: 1; background: #21262d; border-radius: 4px; height: 20px; position: relative; }
  .bar-fill { background: #1f6feb; border-radius: 4px; height: 100%; min-width: 2px; }
  .bar-count { position: absolute; top: 0; left: 8px; line-height: 20px; font-size: 0.7rem; color: #c9d1d9; }
  .empty-msg { color: #484f58; font-style: italic; font-size: 0.85rem; }
  .status-banner {
    padding: 10px 16px; border-radius: 8px; margin-bottom: 16px;
    font-size: 0.85rem; font-weight: 500; display: flex; align-items: center; gap: 8px;
  }
  .status-banner .dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
  .banner-ok { background: #0d2818; border: 1px solid #238636; color: #3fb950; }
  .banner-ok .dot { background: #3fb950; }
  .banner-warn { background: #2a1f00; border: 1px solid #9e6a03; color: #d29922; }
  .banner-warn .dot { background: #d29922; }
  .banner-err { background: #2d0a0a; border: 1px solid #da3633; color: #f85149; }
  .banner-err .dot { background: #f85149; }
</style>
</head>
<body>
<div class="container">
  <div class="refresh-bar">
    <div><h1>AbletonMCP Beta</h1><div class="subtitle">Status Dashboard</div></div>
    <span>Refresh in <span id="countdown">3</span>s</span>
  </div>
  <div id="status-banner"></div>
  <div class="grid" id="cards"></div>
  <div class="section" id="top-tools-section"></div>
  <div class="section">
    <h2>Recent Tool Calls</h2>
    <div id="log-area"></div>
  </div>
  <div class="section">
    <h2>Server Log</h2>
    <div id="server-log" style="
      background:#0d1117; border:1px solid #30363d; border-radius:6px;
      padding:12px; max-height:400px; overflow-y:auto; font-family:'Cascadia Code','Fira Code','Consolas',monospace;
      font-size:0.78rem; line-height:1.6;
    "></div>
  </div>
</div>
<script>
const REFRESH_MS = 3000;
let countdown = 3;
function fmtUp(s) {
  const h = Math.floor(s/3600), m = Math.floor((s%3600)/60), sec = Math.floor(s%60);
  return (h>0?h+'h ':'')+(m>0?m+'m ':'')+sec+'s';
}
async function refresh() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    // Status banner
    const sb = document.getElementById('status-banner');
    if (d.ableton_connected && d.m4l_connected) {
      sb.innerHTML = '<div class="status-banner banner-ok"><span class="dot"></span>All systems operational — Ableton + M4L Bridge connected and ready</div>';
    } else if (d.ableton_connected && !d.m4l_connected) {
      sb.innerHTML = '<div class="status-banner banner-warn"><span class="dot"></span>Ableton connected — M4L Bridge '+(d.m4l_sockets_ready?'waiting for device response':'not connected')+'</div>';
    } else {
      sb.innerHTML = '<div class="status-banner banner-err"><span class="dot"></span>Ableton not connected — make sure the Remote Script is loaded</div>';
    }
    document.getElementById('cards').innerHTML = [
      card('Server Version', d.version, ''),
      card('Uptime', fmtUp(d.uptime_seconds), ''),
      card('Ableton', d.ableton_connected?'Connected':'Disconnected',
           d.ableton_connected?'status-ok':'status-err'),
      card('M4L Bridge',
           d.m4l_connected?'Connected':d.m4l_sockets_ready?'Sockets Ready':'Disconnected',
           d.m4l_connected?'status-ok':d.m4l_sockets_ready?'status-warn':'status-err'),
      card('Snapshots', d.store_counts.snapshots, ''),
      card('Macros', d.store_counts.macros, ''),
      card('Param Maps', d.store_counts.param_maps, ''),
      card('Total Tool Calls', d.total_tool_calls, ''),
    ].join('');
    // Top tools
    const tt = document.getElementById('top-tools-section');
    if (d.top_tools.length) {
      const max = d.top_tools[0][1];
      tt.innerHTML = '<h2>Most Used Tools</h2>' + d.top_tools.map(([n,c])=>
        '<div class="bar-row"><span class="bar-name">'+n+'</span>'+
        '<div class="bar-track"><div class="bar-fill" style="width:'+(c/max*100).toFixed(1)+'%"></div>'+
        '<span class="bar-count">'+c+'</span></div></div>'
      ).join('');
    } else { tt.innerHTML = '<h2>Most Used Tools</h2><p class="empty-msg">No tool calls yet</p>'; }
    // Log
    const la = document.getElementById('log-area');
    if (d.recent_calls.length) {
      la.innerHTML = '<table><thead><tr><th>Time</th><th>Tool</th><th>Duration</th><th>Args</th><th>Status</th></tr></thead><tbody>'+
        d.recent_calls.slice().reverse().map(e=>
          '<tr><td>'+(e.timestamp.split('T')[1]||'').slice(0,8)+'</td>'+
          '<td>'+e.tool+'</td><td>'+e.duration_ms+'ms</td>'+
          '<td style="max-width:250px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+(e.args_summary||'')+'</td>'+
          '<td class="'+(e.error?'error-cell':'')+'">'+(e.error||'OK')+'</td></tr>'
        ).join('')+'</tbody></table>';
    } else { la.innerHTML = '<p class="empty-msg">No tool calls yet</p>'; }
    // Server log
    const sl = document.getElementById('server-log');
    if (d.server_logs && d.server_logs.length) {
      const colors = {INFO:'#8b949e',WARNING:'#d29922',ERROR:'#f85149',DEBUG:'#484f58',CRITICAL:'#f85149'};
      sl.innerHTML = d.server_logs.map(e=>{
        const c = colors[e.level]||'#8b949e';
        const lvl = e.level.padEnd(7);
        return '<div><span style="color:#484f58">'+e.ts+'</span> <span style="color:'+c+'">'+
               lvl+'</span> '+escHtml(e.msg)+'</div>';
      }).join('');
      sl.scrollTop = sl.scrollHeight;
    } else { sl.innerHTML = '<div style="color:#484f58;font-style:italic">No log entries yet</div>'; }
  } catch(err) { console.error('Dashboard refresh failed:', err); }
  countdown = REFRESH_MS/1000;
}
function card(label, value, cls) {
  return '<div class="card"><div class="card-label">'+label+'</div>'+
         '<div class="card-value '+(cls||'')+'">'+value+'</div></div>';
}
function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
refresh();
setInterval(refresh, REFRESH_MS);
setInterval(()=>{countdown=Math.max(0,countdown-1);
  document.getElementById('countdown').textContent=countdown;},1000);
</script>
</body>
</html>"""


def _get_server_version() -> str:
    """Get server version from package metadata, with fallback."""
    try:
        from importlib.metadata import version as _pkg_version
        return _pkg_version("ableton-mcp-beta")
    except Exception:
        return "1.6.0"


def _get_m4l_status() -> tuple:
    """Return (sockets_ready, bridge_responding) with cached ping."""
    sockets_ready = bool(_m4l_connection and _m4l_connection._connected)
    if not sockets_ready:
        return False, False

    now = time.time()
    if now - _m4l_ping_cache["timestamp"] < _M4L_PING_CACHE_TTL:
        return sockets_ready, _m4l_ping_cache["result"]

    try:
        result = _m4l_connection.ping()
    except Exception:
        result = False

    _m4l_ping_cache["result"] = result
    _m4l_ping_cache["timestamp"] = now
    return sockets_ready, result


def _build_status_json() -> dict:
    """Collect all dashboard status data into a JSON-serializable dict."""
    ableton_connected = False
    if _ableton_connection and _ableton_connection.sock:
        try:
            _ableton_connection.sock.getpeername()
            ableton_connected = True
        except Exception:
            pass

    m4l_sockets_ready, m4l_connected = _get_m4l_status()

    with _tool_call_lock:
        recent = list(_tool_call_log)
        counts_copy = dict(_tool_call_counts)

    with _server_log_lock:
        server_logs = list(_server_log_buffer)

    total = sum(counts_copy.values())
    top_tools = sorted(counts_copy.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "version": _get_server_version(),
        "uptime_seconds": round(time.time() - _server_start_time, 1) if _server_start_time else 0,
        "ableton_connected": ableton_connected,
        "m4l_connected": m4l_connected,
        "m4l_sockets_ready": m4l_sockets_ready,
        "store_counts": {
            "snapshots": len(_snapshot_store),
            "macros": len(_macro_store),
            "param_maps": len(_param_map_store),
        },
        "total_tool_calls": total,
        "top_tools": top_tools,
        "recent_calls": recent,
        "server_logs": server_logs,
        "tool_count": 81,
    }


def _start_dashboard_server():
    """Start the dashboard HTTP server on a background thread."""
    global _dashboard_server
    from starlette.applications import Starlette
    from starlette.responses import HTMLResponse, JSONResponse
    from starlette.routing import Route
    import uvicorn

    async def dashboard_page(request):
        return HTMLResponse(DASHBOARD_HTML)

    async def api_status(request):
        return JSONResponse(_build_status_json())

    app = Starlette(routes=[
        Route("/", dashboard_page),
        Route("/api/status", api_status),
    ])

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=DASHBOARD_PORT,
        log_level="warning",
        access_log=False,
    )
    _dashboard_server = uvicorn.Server(config)

    def _run():
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_dashboard_server.serve())

    thread = threading.Thread(target=_run, daemon=True, name="dashboard-http")
    thread.start()
    logger.info(f"Dashboard started at http://127.0.0.1:{DASHBOARD_PORT}")


def _stop_dashboard_server():
    """Signal the dashboard server to shut down."""
    global _dashboard_server
    if _dashboard_server:
        _dashboard_server.should_exit = True
        _dashboard_server = None
        logger.info("Dashboard server stopped")


def get_ableton_connection():
    """Get or create a persistent Ableton connection"""
    global _ableton_connection
    
    if _ableton_connection is not None:
        try:
            # Test if the socket is still connected
            if _ableton_connection.sock is None:
                raise ConnectionError("Socket is None")
            _ableton_connection.sock.settimeout(1.0)
            _ableton_connection.sock.getpeername()  # raises if disconnected
            return _ableton_connection
        except Exception as e:
            logger.warning(f"Existing connection is no longer valid: {str(e)}")
            try:
                _ableton_connection.disconnect()
            except Exception:
                pass
            _ableton_connection = None
    
    # Connection doesn't exist or is invalid, create a new one
    if _ableton_connection is None:
        # Try to connect up to 3 times with a short delay between attempts
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                logger.info(f"Connecting to Ableton (attempt {attempt}/{max_attempts})...")
                _ableton_connection = AbletonConnection(host="localhost", port=9877)
                if _ableton_connection.connect():
                    logger.info("Created new persistent connection to Ableton")
                    
                    # Validate connection with a simple command
                    try:
                        # Get session info as a test
                        _ableton_connection.send_command("get_session_info")
                        logger.info("Connection validated successfully")
                        return _ableton_connection
                    except Exception as e:
                        logger.error(f"Connection validation failed: {str(e)}")
                        _ableton_connection.disconnect()
                        _ableton_connection = None
                        # Continue to next attempt
                else:
                    _ableton_connection = None
            except Exception as e:
                logger.error(f"Connection attempt {attempt} failed: {str(e)}")
                if _ableton_connection:
                    _ableton_connection.disconnect()
                    _ableton_connection = None
            
            # Wait before trying again, but only if we have more attempts left
            if attempt < max_attempts:
                time.sleep(1.0)
        
        # If we get here, all connection attempts failed
        if _ableton_connection is None:
            logger.error("Failed to connect to Ableton after multiple attempts")
            raise Exception("Could not connect to Ableton. Make sure the Remote Script is running.")
    
    return _ableton_connection


def get_m4l_connection() -> M4LConnection:
    """Get or create a connection to the M4L bridge device.

    Always attempts a fresh connection if the existing one is dead.
    Includes a ping to verify the M4L device is actually responding.
    """
    global _m4l_connection

    # If we have a connected instance, verify it still works with a ping
    if _m4l_connection is not None and _m4l_connection._connected:
        if _m4l_connection.ping():
            return _m4l_connection
        # Ping failed — tear down and try fresh
        logger.warning("M4L bridge ping failed on existing connection, reconnecting...")
        _m4l_connection.disconnect()
        _m4l_connection = None

    # Create a fresh connection
    _m4l_connection = M4LConnection()
    if not _m4l_connection.connect():
        _m4l_connection = None
        raise ConnectionError(
            "Could not initialise M4L bridge UDP sockets. "
            "Check that port 9879 is not already in use."
        )

    # Quick ping to verify the device is actually responding
    if not _m4l_connection.ping():
        logger.warning("M4L UDP sockets ready but bridge device is not responding.")
        # Keep the sockets open — the device might be loaded later
        # Don't tear down, so the next call can retry the ping
        raise ConnectionError(
            "M4L bridge device is not responding. "
            "Make sure the AbletonMCP_Bridge M4L device is loaded on a track in Ableton."
        )

    logger.info("M4L bridge connection established and verified.")
    return _m4l_connection


# --- Input validation helpers ---

def _validate_index(value: int, name: str) -> None:
    """Validate that an index is a non-negative integer."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer.")
    if value < 0:
        raise ValueError(f"{name} must be a non-negative integer, got {value}.")


def _validate_index_allow_negative(value: int, name: str, min_value: int = -1) -> None:
    """Validate an index that allows a specific negative sentinel (e.g. -1 for 'end')."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer.")
    if value < min_value:
        raise ValueError(f"{name} must be >= {min_value}, got {value}.")


def _validate_range(value: float, name: str, min_val: float, max_val: float) -> None:
    """Validate that a numeric value falls within [min_val, max_val]."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number.")
    if value < min_val or value > max_val:
        raise ValueError(f"{name} must be between {min_val} and {max_val}, got {value}.")


def _validate_notes(notes: list) -> None:
    """Validate the structure of a MIDI notes list."""
    if not isinstance(notes, list):
        raise ValueError("notes must be a list.")
    if len(notes) == 0:
        raise ValueError("notes list must not be empty.")
    required_keys = {"pitch", "start_time", "duration", "velocity"}
    for i, note in enumerate(notes):
        if not isinstance(note, dict):
            raise ValueError(f"Each note must be a dictionary (note at index {i} is not).")
        missing = required_keys - note.keys()
        if missing:
            raise ValueError(
                f"Note at index {i} is missing required keys: {', '.join(sorted(missing))}."
            )
        pitch = note["pitch"]
        if not isinstance(pitch, int) or isinstance(pitch, bool) or pitch < 0 or pitch > 127:
            raise ValueError(
                f"Note at index {i}: pitch must be an integer between 0 and 127, got {pitch}."
            )
        velocity = note["velocity"]
        if not isinstance(velocity, (int, float)) or isinstance(velocity, bool) or velocity < 0 or velocity > 127:
            raise ValueError(
                f"Note at index {i}: velocity must be a number between 0 and 127, got {velocity}."
            )
        duration = note["duration"]
        if not isinstance(duration, (int, float)) or isinstance(duration, bool) or duration <= 0:
            raise ValueError(
                f"Note at index {i}: duration must be a positive number, got {duration}."
            )
        start_time = note["start_time"]
        if not isinstance(start_time, (int, float)) or isinstance(start_time, bool) or start_time < 0:
            raise ValueError(
                f"Note at index {i}: start_time must be a non-negative number, got {start_time}."
            )


def _validate_automation_points(points: list) -> None:
    """Validate the structure of automation points."""
    if not isinstance(points, list):
        raise ValueError("automation_points must be a list.")
    if len(points) == 0:
        raise ValueError("automation_points list must not be empty.")
    for i, point in enumerate(points):
        if not isinstance(point, dict):
            raise ValueError(
                f"Each automation point must be a dictionary (point at index {i} is not)."
            )
        if "time" not in point or "value" not in point:
            raise ValueError(
                f"Automation point at index {i} must have 'time' and 'value' keys."
            )
        time_val = point["time"]
        if not isinstance(time_val, (int, float)) or isinstance(time_val, bool) or time_val < 0:
            raise ValueError(
                f"Automation point at index {i}: time must be a non-negative number, got {time_val}."
            )
        val = point["value"]
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            raise ValueError(
                f"Automation point at index {i}: value must be a number, got {val}."
            )


# Core Tool endpoints

@mcp.tool()
def get_session_info(ctx: Context) -> str:
    """Get detailed information about the current Ableton session"""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_session_info")
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting session info from Ableton: {str(e)}")
        return "Error getting session info. Please check the server logs for details."

@mcp.tool()
def get_track_info(ctx: Context, track_index: int) -> str:
    """
    Get detailed information about a specific track in Ableton.

    Parameters:
    - track_index: The index of the track to get information about
    """
    try:
        _validate_index(track_index, "track_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("get_track_info", {"track_index": track_index})
        return json.dumps(result, indent=2)
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error getting track info from Ableton: {str(e)}")
        return f"Error getting track info: {str(e)}"

@mcp.tool()
def create_midi_track(ctx: Context, index: int = -1) -> str:
    """
    Create a new MIDI track in the Ableton session.

    Parameters:
    - index: The index to insert the track at (-1 = end of list)
    """
    try:
        _validate_index_allow_negative(index, "index", min_value=-1)
        ableton = get_ableton_connection()
        result = ableton.send_command("create_midi_track", {"index": index})
        return f"Created new MIDI track: {result.get('name', 'unknown')}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error creating MIDI track: {str(e)}")
        return "Error creating MIDI track. Please check the server logs for details."

@mcp.tool()
def create_audio_track(ctx: Context, index: int = -1) -> str:
    """
    Create a new audio track in the Ableton session.

    Parameters:
    - index: The index to insert the track at (-1 = end of list)
    """
    try:
        _validate_index_allow_negative(index, "index", min_value=-1)
        ableton = get_ableton_connection()
        result = ableton.send_command("create_audio_track", {"index": index})
        return f"Created new audio track: {result.get('name', 'unknown')}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error creating audio track: {str(e)}")
        return "Error creating audio track. Please check the server logs for details."


@mcp.tool()
def set_track_name(ctx: Context, track_index: int, name: str) -> str:
    """
    Set the name of a track.

    Parameters:
    - track_index: The index of the track to rename
    - name: The new name for the track
    """
    try:
        _validate_index(track_index, "track_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("set_track_name", {"track_index": track_index, "name": name})
        return f"Renamed track to: {result.get('name', name)}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error setting track name: {str(e)}")
        return "Error setting track name. Please check the server logs for details."

@mcp.tool()
def create_clip(ctx: Context, track_index: int, clip_index: int, length: float = 4.0) -> str:
    """
    Create a new MIDI clip in the specified track and clip slot.

    Parameters:
    - track_index: The index of the track to create the clip in
    - clip_index: The index of the clip slot to create the clip in
    - length: The length of the clip in beats (default: 4.0)
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        if not isinstance(length, (int, float)) or isinstance(length, bool) or length <= 0:
            raise ValueError(f"length must be a positive number, got {length}.")
        ableton = get_ableton_connection()
        result = ableton.send_command("create_clip", {
            "track_index": track_index, 
            "clip_index": clip_index, 
            "length": length
        })
        return f"Created new clip at track {track_index}, slot {clip_index} with length {length} beats"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error creating clip: {str(e)}")
        return "Error creating clip. Please check the server logs for details."

@mcp.tool()
def add_notes_to_clip(
    ctx: Context, 
    track_index: int, 
    clip_index: int, 
    notes: List[Dict[str, Union[int, float, bool]]]
) -> str:
    """
    Add MIDI notes to a clip.
    
    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    - notes: List of note dictionaries, each with pitch, start_time, duration, velocity, and mute
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        _validate_notes(notes)
        ableton = get_ableton_connection()
        result = ableton.send_command("add_notes_to_clip", {
            "track_index": track_index,
            "clip_index": clip_index,
            "notes": notes
        })
        return f"Added {len(notes)} notes to clip at track {track_index}, slot {clip_index}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error adding notes to clip: {str(e)}")
        return "Error adding notes to clip. Please check the server logs for details."

@mcp.tool()
def set_clip_name(ctx: Context, track_index: int, clip_index: int, name: str) -> str:
    """
    Set the name of a clip.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    - name: The new name for the clip
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("set_clip_name", {
            "track_index": track_index,
            "clip_index": clip_index,
            "name": name
        })
        return f"Renamed clip at track {track_index}, slot {clip_index} to '{name}'"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error setting clip name: {str(e)}")
        return "Error setting clip name. Please check the server logs for details."

@mcp.tool()
def delete_clip(ctx: Context, track_index: int, clip_index: int) -> str:
    """
    Delete a clip from a clip slot.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("delete_clip", {
            "track_index": track_index,
            "clip_index": clip_index
        })
        return f"Deleted clip at track {track_index}, slot {clip_index}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error deleting clip: {str(e)}")
        return "Error deleting clip. Please check the server logs for details."

@mcp.tool()
def get_clip_notes(ctx: Context, track_index: int, clip_index: int,
                   start_time: float = 0.0, time_span: float = 0.0,
                   start_pitch: int = 0, pitch_span: int = 128) -> str:
    """
    Get MIDI notes from a clip.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    - start_time: Start time in beats (default: 0.0)
    - time_span: Duration in beats to retrieve (default: 0.0 = entire clip)
    - start_pitch: Lowest MIDI pitch to retrieve (default: 0)
    - pitch_span: Range of pitches to retrieve (default: 128 = all pitches)
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        _validate_range(start_pitch, "start_pitch", 0, 127)
        _validate_range(pitch_span, "pitch_span", 1, 128)
        if start_time < 0:
            raise ValueError(f"start_time must be non-negative, got {start_time}.")
        if time_span < 0:
            raise ValueError(f"time_span must be non-negative, got {time_span}.")
        ableton = get_ableton_connection()
        result = ableton.send_command("get_clip_notes", {
            "track_index": track_index,
            "clip_index": clip_index,
            "start_time": start_time,
            "time_span": time_span,
            "start_pitch": start_pitch,
            "pitch_span": pitch_span
        })
        return json.dumps(result, indent=2)
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error getting clip notes: {str(e)}")
        return "Error getting clip notes. Please check the server logs for details."

@mcp.tool()
def set_tempo(ctx: Context, tempo: float) -> str:
    """
    Set the tempo of the Ableton session.
    
    Parameters:
    - tempo: The new tempo in BPM
    """
    try:
        _validate_range(tempo, "tempo", 20.0, 999.0)
        ableton = get_ableton_connection()
        result = ableton.send_command("set_tempo", {"tempo": tempo})
        return f"Set tempo to {tempo} BPM"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error setting tempo: {str(e)}")
        return "Error setting tempo. Please check the server logs for details."


@mcp.tool()
def load_instrument_or_effect(ctx: Context, track_index: int, uri: str) -> str:
    """
    Load an instrument or effect onto a track using its URI.
    
    Parameters:
    - track_index: The index of the track to load the instrument on
    - uri: The URI of the instrument or effect to load (e.g., 'query:Synths#Instrument%20Rack:Bass:FileId_5116')
    """
    try:
        _validate_index(track_index, "track_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("load_browser_item", {
            "track_index": track_index,
            "item_uri": uri
        })

        # Check if the instrument was loaded successfully
        if result.get("loaded", False):
            new_devices = result.get("new_devices", [])
            if new_devices:
                return f"Loaded instrument with URI '{uri}' on track {track_index}. New devices: {', '.join(new_devices)}"
            else:
                devices = result.get("devices_after", [])
                return f"Loaded instrument with URI '{uri}' on track {track_index}. Devices on track: {', '.join(devices)}"
        else:
            return f"Failed to load instrument with URI '{uri}'"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error loading instrument by URI: {str(e)}")
        return "Error loading instrument. Please check the server logs for details."

@mcp.tool()
def fire_clip(ctx: Context, track_index: int, clip_index: int) -> str:
    """
    Start playing a clip.
    
    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("fire_clip", {
            "track_index": track_index,
            "clip_index": clip_index
        })
        return f"Started playing clip at track {track_index}, slot {clip_index}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error firing clip: {str(e)}")
        return "Error firing clip. Please check the server logs for details."

@mcp.tool()
def stop_clip(ctx: Context, track_index: int, clip_index: int) -> str:
    """
    Stop playing a clip.
    
    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("stop_clip", {
            "track_index": track_index,
            "clip_index": clip_index
        })
        return f"Stopped clip at track {track_index}, slot {clip_index}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error stopping clip: {str(e)}")
        return "Error stopping clip. Please check the server logs for details."

@mcp.tool()
def start_playback(ctx: Context) -> str:
    """Start playing the Ableton session."""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("start_playback")
        return "Started playback"
    except Exception as e:
        logger.error(f"Error starting playback: {str(e)}")
        return "Error starting playback. Please check the server logs for details."

@mcp.tool()
def stop_playback(ctx: Context) -> str:
    """Stop playing the Ableton session."""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("stop_playback")
        return "Stopped playback"
    except Exception as e:
        logger.error(f"Error stopping playback: {str(e)}")
        return "Error stopping playback. Please check the server logs for details."

@mcp.tool()
def set_track_volume(ctx: Context, track_index: int, volume: float) -> str:
    """
    Set the volume of a track.

    Parameters:
    - track_index: The index of the track
    - volume: The new volume value (0.0 to 1.0, where 0.85 is approximately 0dB)
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_range(volume, "volume", 0.0, 1.0)
        ableton = get_ableton_connection()
        result = ableton.send_command("set_track_volume", {
            "track_index": track_index,
            "volume": volume
        })
        return f"Set track {track_index} volume to {result.get('volume', volume)}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error setting track volume: {str(e)}")
        return "Error setting track volume. Please check the server logs for details."

@mcp.tool()
def set_track_pan(ctx: Context, track_index: int, pan: float) -> str:
    """
    Set the panning of a track.

    Parameters:
    - track_index: The index of the track
    - pan: The new pan value (-1.0 = full left, 0.0 = center, 1.0 = full right)
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_range(pan, "pan", -1.0, 1.0)
        ableton = get_ableton_connection()
        result = ableton.send_command("set_track_pan", {
            "track_index": track_index,
            "pan": pan
        })
        return f"Set track {track_index} pan to {result.get('pan', pan)}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error setting track pan: {str(e)}")
        return "Error setting track pan. Please check the server logs for details."

@mcp.tool()
def set_track_mute(ctx: Context, track_index: int, mute: bool) -> str:
    """
    Set the mute state of a track.

    Parameters:
    - track_index: The index of the track
    - mute: True to mute, False to unmute
    """
    try:
        _validate_index(track_index, "track_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("set_track_mute", {
            "track_index": track_index,
            "mute": mute
        })
        state = "muted" if result.get('mute', mute) else "unmuted"
        return f"Track {track_index} is now {state}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error setting track mute: {str(e)}")
        return "Error setting track mute. Please check the server logs for details."

@mcp.tool()
def set_track_solo(ctx: Context, track_index: int, solo: bool) -> str:
    """
    Set the solo state of a track.

    Parameters:
    - track_index: The index of the track
    - solo: True to solo, False to unsolo
    """
    try:
        _validate_index(track_index, "track_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("set_track_solo", {
            "track_index": track_index,
            "solo": solo
        })
        state = "soloed" if result.get('solo', solo) else "unsoloed"
        return f"Track {track_index} is now {state}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error setting track solo: {str(e)}")
        return "Error setting track solo. Please check the server logs for details."

@mcp.tool()
def set_track_arm(ctx: Context, track_index: int, arm: bool) -> str:
    """
    Set the arm (record enable) state of a track.

    Parameters:
    - track_index: The index of the track
    - arm: True to arm, False to disarm
    """
    try:
        _validate_index(track_index, "track_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("set_track_arm", {
            "track_index": track_index,
            "arm": arm
        })
        state = "armed" if result.get('arm', arm) else "disarmed"
        return f"Track {track_index} is now {state}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error setting track arm: {str(e)}")
        return "Error setting track arm. Please check the server logs for details."

@mcp.tool()
def delete_device(ctx: Context, track_index: int, device_index: int) -> str:
    """
    Delete a device from a track.

    Parameters:
    - track_index: The index of the track containing the device
    - device_index: The index of the device to delete
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(device_index, "device_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("delete_device", {
            "track_index": track_index,
            "device_index": device_index
        })
        return f"Deleted device '{result.get('device_name', 'unknown')}' from track {track_index}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error deleting device: {str(e)}")
        return "Error deleting device. Please check the server logs for details."

@mcp.tool()
def delete_track(ctx: Context, track_index: int) -> str:
    """
    Delete a track from the session.

    Parameters:
    - track_index: The index of the track to delete
    """
    try:
        _validate_index(track_index, "track_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("delete_track", {"track_index": track_index})
        return f"Deleted track '{result.get('track_name', 'unknown')}' at index {track_index}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error deleting track: {str(e)}")
        return "Error deleting track. Please check the server logs for details."

@mcp.tool()
def delete_scene(ctx: Context, scene_index: int) -> str:
    """
    Delete a scene from the session.

    Parameters:
    - scene_index: The index of the scene to delete
    """
    try:
        _validate_index(scene_index, "scene_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("delete_scene", {"scene_index": scene_index})
        return f"Deleted scene '{result.get('scene_name', 'unknown')}' at index {scene_index}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error deleting scene: {str(e)}")
        return "Error deleting scene. Please check the server logs for details."

@mcp.tool()
def get_clip_info(ctx: Context, track_index: int, clip_index: int) -> str:
    """
    Get detailed information about a clip.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("get_clip_info", {
            "track_index": track_index,
            "clip_index": clip_index
        })
        return json.dumps(result, indent=2)
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error getting clip info: {str(e)}")
        return "Error getting clip info. Please check the server logs for details."

@mcp.tool()
def clear_clip_notes(ctx: Context, track_index: int, clip_index: int) -> str:
    """
    Remove all MIDI notes from a clip without deleting the clip.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("clear_clip_notes", {
            "track_index": track_index,
            "clip_index": clip_index
        })
        return f"Cleared {result.get('notes_removed', 0)} notes from clip at track {track_index}, slot {clip_index}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error clearing clip notes: {str(e)}")
        return "Error clearing clip notes. Please check the server logs for details."

@mcp.tool()
def duplicate_clip(ctx: Context, track_index: int, clip_index: int, target_clip_index: int) -> str:
    """
    Duplicate a clip to another clip slot on the same track.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the source clip slot
    - target_clip_index: The index of the target clip slot
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        _validate_index(target_clip_index, "target_clip_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("duplicate_clip", {
            "track_index": track_index,
            "clip_index": clip_index,
            "target_clip_index": target_clip_index
        })
        return f"Duplicated clip from slot {clip_index} to slot {target_clip_index} on track {track_index}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error duplicating clip: {str(e)}")
        return "Error duplicating clip. Please check the server logs for details."

@mcp.tool()
def duplicate_track(ctx: Context, track_index: int) -> str:
    """
    Duplicate a track with all its devices and clips.

    Parameters:
    - track_index: The index of the track to duplicate
    """
    try:
        _validate_index(track_index, "track_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("duplicate_track", {"track_index": track_index})
        return f"Duplicated track '{result.get('source_name', 'unknown')}' to new track '{result.get('new_name', 'unknown')}' at index {result.get('new_index', 'unknown')}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error duplicating track: {str(e)}")
        return "Error duplicating track. Please check the server logs for details."

@mcp.tool()
def quantize_clip_notes(ctx: Context, track_index: int, clip_index: int, grid_size: float = 0.25) -> str:
    """
    Quantize MIDI notes in a clip to snap to a grid.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    - grid_size: The grid size in beats (0.25 = 16th notes, 0.5 = 8th notes, 1.0 = quarter notes)
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        if not isinstance(grid_size, (int, float)) or isinstance(grid_size, bool) or grid_size <= 0:
            raise ValueError(f"grid_size must be a positive number, got {grid_size}.")
        ableton = get_ableton_connection()
        result = ableton.send_command("quantize_clip_notes", {
            "track_index": track_index,
            "clip_index": clip_index,
            "grid_size": grid_size
        })
        return f"Quantized {result.get('notes_quantized', 0)} notes to {grid_size} beat grid in clip at track {track_index}, slot {clip_index}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error quantizing clip notes: {str(e)}")
        return "Error quantizing clip notes. Please check the server logs for details."

@mcp.tool()
def transpose_clip_notes(ctx: Context, track_index: int, clip_index: int, semitones: int) -> str:
    """
    Transpose all MIDI notes in a clip by a number of semitones.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    - semitones: The number of semitones to transpose (positive = up, negative = down)
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        _validate_range(semitones, "semitones", -127, 127)
        ableton = get_ableton_connection()
        result = ableton.send_command("transpose_clip_notes", {
            "track_index": track_index,
            "clip_index": clip_index,
            "semitones": semitones
        })
        direction = "up" if semitones > 0 else "down"
        return f"Transposed {result.get('notes_transposed', 0)} notes {direction} by {abs(semitones)} semitones in clip at track {track_index}, slot {clip_index}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error transposing clip notes: {str(e)}")
        return "Error transposing clip notes. Please check the server logs for details."

@mcp.tool()
def set_clip_looping(ctx: Context, track_index: int, clip_index: int, looping: bool) -> str:
    """
    Set the looping state of a clip.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    - looping: True to enable looping, False to disable
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("set_clip_looping", {
            "track_index": track_index,
            "clip_index": clip_index,
            "looping": looping
        })
        state = "enabled" if result.get('looping', looping) else "disabled"
        return f"Looping {state} for clip at track {track_index}, slot {clip_index}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error setting clip looping: {str(e)}")
        return "Error setting clip looping. Please check the server logs for details."

@mcp.tool()
def set_clip_loop_points(ctx: Context, track_index: int, clip_index: int,
                          loop_start: float, loop_end: float) -> str:
    """
    Set the loop start and end points of a clip.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    - loop_start: The loop start position in beats
    - loop_end: The loop end position in beats
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        if loop_start < 0:
            raise ValueError(f"loop_start must be non-negative, got {loop_start}.")
        if loop_end < 0:
            raise ValueError(f"loop_end must be non-negative, got {loop_end}.")
        if loop_end <= loop_start:
            raise ValueError(f"loop_end ({loop_end}) must be greater than loop_start ({loop_start}).")
        ableton = get_ableton_connection()
        result = ableton.send_command("set_clip_loop_points", {
            "track_index": track_index,
            "clip_index": clip_index,
            "loop_start": loop_start,
            "loop_end": loop_end
        })
        return f"Set loop points for clip at track {track_index}, slot {clip_index}: start={result.get('loop_start', loop_start)}, end={result.get('loop_end', loop_end)}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error setting clip loop points: {str(e)}")
        return "Error setting clip loop points. Please check the server logs for details."

@mcp.tool()
def set_clip_color(ctx: Context, track_index: int, clip_index: int, color_index: int) -> str:
    """
    Set the color of a clip.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    - color_index: The color index (0-69, Ableton's color palette)
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        _validate_range(color_index, "color_index", 0, 69)
        ableton = get_ableton_connection()
        result = ableton.send_command("set_clip_color", {
            "track_index": track_index,
            "clip_index": clip_index,
            "color_index": color_index
        })
        return f"Set color index to {result.get('color_index', color_index)} for clip at track {track_index}, slot {clip_index}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error setting clip color: {str(e)}")
        return "Error setting clip color. Please check the server logs for details."

@mcp.tool()
def get_scenes(ctx: Context) -> str:
    """Get information about all scenes in the session."""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_scenes")
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting scenes: {str(e)}")
        return "Error getting scenes. Please check the server logs for details."

@mcp.tool()
def fire_scene(ctx: Context, scene_index: int) -> str:
    """
    Fire (launch) a scene to start all clips in that row.

    Parameters:
    - scene_index: The index of the scene to fire
    """
    try:
        _validate_index(scene_index, "scene_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("fire_scene", {"scene_index": scene_index})
        return f"Fired scene {scene_index}: {result.get('scene_name', 'unknown')}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error firing scene: {str(e)}")
        return "Error firing scene. Please check the server logs for details."

@mcp.tool()
def create_scene(ctx: Context, index: int = -1) -> str:
    """
    Create a new scene in the session.

    Parameters:
    - index: The index to insert the scene at (-1 = end of list)
    """
    try:
        _validate_index_allow_negative(index, "index", min_value=-1)
        ableton = get_ableton_connection()
        result = ableton.send_command("create_scene", {"index": index})
        return f"Created new scene: {result.get('name', 'unknown')}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error creating scene: {str(e)}")
        return "Error creating scene. Please check the server logs for details."

@mcp.tool()
def set_scene_name(ctx: Context, scene_index: int, name: str) -> str:
    """
    Set the name of a scene.

    Parameters:
    - scene_index: The index of the scene to rename
    - name: The new name for the scene
    """
    try:
        _validate_index(scene_index, "scene_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("set_scene_name", {
            "scene_index": scene_index,
            "name": name
        })
        return f"Renamed scene to: {result.get('name', name)}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error setting scene name: {str(e)}")
        return "Error setting scene name. Please check the server logs for details."

@mcp.tool()
def get_return_tracks(ctx: Context) -> str:
    """Get information about all return tracks."""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_return_tracks")
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting return tracks: {str(e)}")
        return "Error getting return tracks. Please check the server logs for details."

@mcp.tool()
def get_return_track_info(ctx: Context, return_track_index: int) -> str:
    """
    Get detailed information about a specific return track.

    Parameters:
    - return_track_index: The index of the return track (0 = A, 1 = B, etc.)
    """
    try:
        _validate_index(return_track_index, "return_track_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("get_return_track_info", {
            "return_track_index": return_track_index
        })
        return json.dumps(result, indent=2)
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error getting return track info: {str(e)}")
        return "Error getting return track info. Please check the server logs for details."

@mcp.tool()
def set_return_track_volume(ctx: Context, return_track_index: int, volume: float) -> str:
    """
    Set the volume of a return track.

    Parameters:
    - return_track_index: The index of the return track (0 = A, 1 = B, etc.)
    - volume: The new volume value (0.0 to 1.0)
    """
    try:
        _validate_index(return_track_index, "return_track_index")
        _validate_range(volume, "volume", 0.0, 1.0)
        ableton = get_ableton_connection()
        result = ableton.send_command("set_return_track_volume", {
            "return_track_index": return_track_index,
            "volume": volume
        })
        return f"Set return track {return_track_index} volume to {result.get('volume', volume)}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error setting return track volume: {str(e)}")
        return "Error setting return track volume. Please check the server logs for details."

@mcp.tool()
def set_return_track_pan(ctx: Context, return_track_index: int, pan: float) -> str:
    """
    Set the panning of a return track.

    Parameters:
    - return_track_index: The index of the return track (0 = A, 1 = B, etc.)
    - pan: The new pan value (-1.0 = full left, 0.0 = center, 1.0 = full right)
    """
    try:
        _validate_index(return_track_index, "return_track_index")
        _validate_range(pan, "pan", -1.0, 1.0)
        ableton = get_ableton_connection()
        result = ableton.send_command("set_return_track_pan", {
            "return_track_index": return_track_index,
            "pan": pan
        })
        return f"Set return track {return_track_index} pan to {result.get('pan', pan)}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error setting return track pan: {str(e)}")
        return "Error setting return track pan. Please check the server logs for details."

@mcp.tool()
def set_return_track_mute(ctx: Context, return_track_index: int, mute: bool) -> str:
    """
    Set the mute state of a return track.

    Parameters:
    - return_track_index: The index of the return track (0 = A, 1 = B, etc.)
    - mute: True to mute, False to unmute
    """
    try:
        _validate_index(return_track_index, "return_track_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("set_return_track_mute", {
            "return_track_index": return_track_index,
            "mute": mute
        })
        state = "muted" if result.get('mute', mute) else "unmuted"
        return f"Return track {return_track_index} is now {state}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error setting return track mute: {str(e)}")
        return "Error setting return track mute. Please check the server logs for details."

@mcp.tool()
def set_return_track_solo(ctx: Context, return_track_index: int, solo: bool) -> str:
    """
    Set the solo state of a return track.

    Parameters:
    - return_track_index: The index of the return track (0 = A, 1 = B, etc.)
    - solo: True to solo, False to unsolo
    """
    try:
        _validate_index(return_track_index, "return_track_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("set_return_track_solo", {
            "return_track_index": return_track_index,
            "solo": solo
        })
        state = "soloed" if result.get('solo', solo) else "unsoloed"
        return f"Return track {return_track_index} is now {state}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error setting return track solo: {str(e)}")
        return "Error setting return track solo. Please check the server logs for details."

@mcp.tool()
def set_track_send(ctx: Context, track_index: int, send_index: int, value: float) -> str:
    """
    Set the send level from a track to a return track.

    Parameters:
    - track_index: The index of the source track
    - send_index: The index of the send (0 = Send A, 1 = Send B, etc.)
    - value: The send level (0.0 to 1.0)
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(send_index, "send_index")
        _validate_range(value, "value", 0.0, 1.0)
        ableton = get_ableton_connection()
        result = ableton.send_command("set_track_send", {
            "track_index": track_index,
            "send_index": send_index,
            "value": value
        })
        return f"Set track {track_index} send {send_index} to {result.get('value', value)}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error setting track send: {str(e)}")
        return "Error setting track send. Please check the server logs for details."

@mcp.tool()
def get_master_track_info(ctx: Context) -> str:
    """Get detailed information about the master track, including volume, panning, and devices."""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_master_track_info")
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting master track info: {str(e)}")
        return "Error getting master track info. Please check the server logs for details."

@mcp.tool()
def set_master_volume(ctx: Context, volume: float) -> str:
    """
    Set the volume of the master track.

    Parameters:
    - volume: The new volume value (0.0 to 1.0, where 0.85 is approximately 0dB)
    """
    try:
        _validate_range(volume, "volume", 0.0, 1.0)
        ableton = get_ableton_connection()
        result = ableton.send_command("set_master_volume", {"volume": volume})
        return f"Set master volume to {result.get('volume', volume)}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error setting master volume: {str(e)}")
        return "Error setting master volume. Please check the server logs for details."

@mcp.tool()
def get_browser_tree(ctx: Context, category_type: str = "all") -> str:
    """
    Get a hierarchical tree of browser categories from Ableton.
    
    Parameters:
    - category_type: Type of categories to get ('all', 'instruments', 'sounds', 'drums', 'audio_effects', 'midi_effects')
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_browser_tree", {
            "category_type": category_type
        })
        
        # Check if we got any categories
        if "available_categories" in result and len(result.get("categories", [])) == 0:
            available_cats = result.get("available_categories", [])
            return (f"No categories found for '{category_type}'. "
                   f"Available browser categories: {', '.join(available_cats)}")
        
        # Format the tree in a more readable way
        total_folders = result.get("total_folders", 0)
        formatted_output = f"Browser tree for '{category_type}' (showing {total_folders} folders):\n\n"
        
        def format_tree(item, indent=0):
            output = ""
            if item:
                prefix = "  " * indent
                name = item.get("name", "Unknown")
                path = item.get("path", "")
                has_more = item.get("has_more", False)
                
                # Add this item
                output += f"{prefix}• {name}"
                if path:
                    output += f" (path: {path})"
                if has_more:
                    output += " [...]"
                output += "\n"
                
                # Add children
                for child in item.get("children", []):
                    output += format_tree(child, indent + 1)
            return output
        
        # Format each category
        for category in result.get("categories", []):
            formatted_output += format_tree(category)
            formatted_output += "\n"
        
        return formatted_output
    except Exception as e:
        error_msg = str(e)
        if "Browser is not available" in error_msg:
            logger.error(f"Browser is not available in Ableton: {error_msg}")
            return f"Error: The Ableton browser is not available. Make sure Ableton Live is fully loaded and try again."
        elif "Could not access Live application" in error_msg:
            logger.error(f"Could not access Live application: {error_msg}")
            return f"Error: Could not access the Ableton Live application. Make sure Ableton Live is running and the Remote Script is loaded."
        else:
            logger.error(f"Error getting browser tree: {error_msg}")
            return "Error getting browser tree. Please check the server logs for details."

@mcp.tool()
def get_browser_items_at_path(ctx: Context, path: str) -> str:
    """
    Get browser items at a specific path in Ableton's browser.
    
    Parameters:
    - path: Path in the format "category/folder/subfolder"
            where category is one of the available browser categories in Ableton
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_browser_items_at_path", {
            "path": path
        })
        
        # Check if there was an error with available categories
        if "error" in result and "available_categories" in result:
            error = result.get("error", "")
            available_cats = result.get("available_categories", [])
            return (f"Error: {error}\n"
                   f"Available browser categories: {', '.join(available_cats)}")
        
        return json.dumps(result, indent=2)
    except Exception as e:
        error_msg = str(e)
        if "Browser is not available" in error_msg:
            logger.error(f"Browser is not available in Ableton: {error_msg}")
            return f"Error: The Ableton browser is not available. Make sure Ableton Live is fully loaded and try again."
        elif "Could not access Live application" in error_msg:
            logger.error(f"Could not access Live application: {error_msg}")
            return f"Error: Could not access the Ableton Live application. Make sure Ableton Live is running and the Remote Script is loaded."
        elif "Unknown or unavailable category" in error_msg:
            logger.error(f"Invalid browser category: {error_msg}")
            return "Error: Invalid browser category. Please check the available categories using get_browser_tree."
        elif "Path part" in error_msg and "not found" in error_msg:
            logger.error(f"Path not found: {error_msg}")
            return "Error: The specified path was not found. Please check the path and try again."
        else:
            logger.error(f"Error getting browser items at path: {error_msg}")
            return "Error getting browser items at path. Please check the server logs for details."

@mcp.tool()
def get_device_parameters(ctx: Context, track_index: int, device_index: int) -> str:
    """
    Get all parameters and their current values for a device on a track.

    Parameters:
    - track_index: The index of the track containing the device
    - device_index: The index of the device on the track
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(device_index, "device_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("get_device_parameters", {
            "track_index": track_index,
            "device_index": device_index
        })
        return json.dumps(result, indent=2)
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error getting device parameters: {str(e)}")
        return "Error getting device parameters. Please check the server logs for details."

@mcp.tool()
def set_device_parameter(ctx: Context, track_index: int, device_index: int,
                          parameter_name: str, value: float) -> str:
    """
    Set a device parameter value.

    Parameters:
    - track_index: The index of the track containing the device
    - device_index: The index of the device on the track
    - parameter_name: The name of the parameter to set
    - value: The new value for the parameter (will be clamped to min/max)
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(device_index, "device_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("set_device_parameter", {
            "track_index": track_index,
            "device_index": device_index,
            "parameter_name": parameter_name,
            "value": value
        })
        if result.get("clamped", False):
            return f"Set parameter '{result.get('parameter')}' to {result.get('value')} (value was clamped to valid range)"
        return f"Set parameter '{result.get('parameter')}' to {result.get('value')}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error setting device parameter: {str(e)}")
        return "Error setting device parameter. Please check the server logs for details."

@mcp.tool()
def get_user_library(ctx: Context) -> str:
    """
    Get the user library browser tree, including user folders and samples.
    Returns the browser structure for user-added content.
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_user_library")
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting user library: {str(e)}")
        return "Error getting user library. Please check the server logs for details."

@mcp.tool()
def get_user_folders(ctx: Context) -> str:
    """
    Get user-configured sample folders from Ableton's browser.
    Note: Returns browser items (URIs), not raw filesystem paths.
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_user_folders")
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting user folders: {str(e)}")
        return "Error getting user folders. Please check the server logs for details."

@mcp.tool()
def load_sample(ctx: Context, track_index: int, sample_uri: str) -> str:
    """
    Load an audio sample onto a track from the browser.

    Parameters:
    - track_index: The index of the track to load the sample onto
    - sample_uri: The URI of the sample from the browser (use get_user_library to find URIs)
    """
    try:
        _validate_index(track_index, "track_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("load_sample", {
            "track_index": track_index,
            "sample_uri": sample_uri
        })
        if result.get("loaded", False):
            return f"Loaded sample '{result.get('sample_name', 'unknown')}' onto track {track_index}"
        return f"Failed to load sample"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error loading sample: {str(e)}")
        return "Error loading sample. Please check the server logs for details."

@mcp.tool()
def create_clip_automation(ctx: Context, track_index: int, clip_index: int,
                            parameter_name: str, automation_points: List[Dict[str, float]]) -> str:
    """
    Create automation for a parameter within a clip.

    Parameters:
    - track_index: The index of the track
    - clip_index: The index of the clip slot
    - parameter_name: Name of the parameter to automate (e.g., "Device On", "Volume")
    - automation_points: List of {time: float, value: float} dictionaries

    Note: Limited support - works best with MIDI clips and basic device parameters.
    Arrangement automation is not supported via the API.
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        _validate_automation_points(automation_points)
        ableton = get_ableton_connection()
        result = ableton.send_command("create_clip_automation", {
            "track_index": track_index,
            "clip_index": clip_index,
            "parameter_name": parameter_name,
            "automation_points": automation_points
        })
        if result.get("created", False):
            return f"Created automation with {result.get('point_count', 0)} points for parameter '{parameter_name}'"
        return f"Failed to create automation: {result.get('error', 'Unknown error')}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error creating clip automation: {str(e)}")
        return "Error creating clip automation. Please check the server logs for details."

# ======================================================================
# Arrangement View Workflow
# ======================================================================

@mcp.tool()
def get_song_transport(ctx: Context) -> str:
    """
    Get the current transport/arrangement state of the Ableton session.

    Returns: current playback time, playing state, tempo, time signature,
    loop bracket settings, record mode, and song length.
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_song_transport", {})
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting song transport: {str(e)}")
        return "Error getting song transport. Please check the server logs for details."

@mcp.tool()
def set_song_time(ctx: Context, time: float) -> str:
    """
    Set the playback position (arrangement playhead).

    Parameters:
    - time: The position in beats to jump to (0.0 = start of song)
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("set_song_time", {"time": time})
        return f"Playhead set to beat {result.get('current_time', time)}"
    except Exception as e:
        logger.error(f"Error setting song time: {str(e)}")
        return "Error setting song time. Please check the server logs for details."

@mcp.tool()
def set_song_loop(ctx: Context, enabled: bool = None, start: float = None, length: float = None) -> str:
    """
    Control the arrangement loop bracket.

    Parameters:
    - enabled: True to enable looping, False to disable (optional)
    - start: Loop start position in beats (optional)
    - length: Loop length in beats (optional)
    """
    try:
        params = {}
        if enabled is not None:
            params["enabled"] = enabled
        if start is not None:
            params["start"] = start
        if length is not None:
            params["length"] = length
        ableton = get_ableton_connection()
        result = ableton.send_command("set_song_loop", params)
        state = "enabled" if result.get("loop_enabled") else "disabled"
        return f"Loop {state}: start={result.get('loop_start', 0)}, length={result.get('loop_length', 0)} beats"
    except Exception as e:
        logger.error(f"Error setting song loop: {str(e)}")
        return "Error setting song loop. Please check the server logs for details."

@mcp.tool()
def duplicate_clip_to_arrangement(ctx: Context, track_index: int, clip_index: int, time: float) -> str:
    """
    Copy a session clip to the arrangement timeline at a given beat position.

    This is the primary arrangement workflow tool — build clips in session view,
    then place them on the arrangement timeline.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    - time: The beat position on the arrangement timeline to place the clip
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("duplicate_clip_to_arrangement", {
            "track_index": track_index,
            "clip_index": clip_index,
            "time": time,
        })
        return (f"Placed clip '{result.get('clip_name', '')}' on arrangement at beat {result.get('placed_at', time)} "
                f"(track {track_index}, length {result.get('clip_length', '?')} beats)")
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error duplicating clip to arrangement: {str(e)}")
        return "Error duplicating clip to arrangement. Please check the server logs for details."

# ======================================================================
# Advanced Clip Operations
# ======================================================================

@mcp.tool()
def crop_clip(ctx: Context, track_index: int, clip_index: int) -> str:
    """
    Trim a clip to its current loop region, discarding content outside.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("crop_clip", {
            "track_index": track_index,
            "clip_index": clip_index,
        })
        return f"Cropped clip '{result.get('clip_name', '')}' — new length: {result.get('new_length', '?')} beats"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error cropping clip: {str(e)}")
        return "Error cropping clip. Please check the server logs for details."

@mcp.tool()
def duplicate_clip_loop(ctx: Context, track_index: int, clip_index: int) -> str:
    """
    Double the loop content of a clip (e.g., 4 bars becomes 8 bars with content repeated).

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("duplicate_clip_loop", {
            "track_index": track_index,
            "clip_index": clip_index,
        })
        return (f"Doubled loop of clip '{result.get('clip_name', '')}' — "
                f"{result.get('old_length', '?')} → {result.get('new_length', '?')} beats")
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error duplicating clip loop: {str(e)}")
        return "Error duplicating clip loop. Please check the server logs for details."

@mcp.tool()
def set_clip_start_end(ctx: Context, track_index: int, clip_index: int,
                       start_marker: float = None, end_marker: float = None) -> str:
    """
    Set clip start_marker and end_marker positions (controls playback region without changing notes).

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    - start_marker: The new start marker position in beats (optional)
    - end_marker: The new end marker position in beats (optional)
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        params = {"track_index": track_index, "clip_index": clip_index}
        if start_marker is not None:
            params["start_marker"] = start_marker
        if end_marker is not None:
            params["end_marker"] = end_marker
        ableton = get_ableton_connection()
        result = ableton.send_command("set_clip_start_end", params)
        return (f"Clip '{result.get('clip_name', '')}' markers set — "
                f"start: {result.get('start_marker', '?')}, end: {result.get('end_marker', '?')}")
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error setting clip start/end: {str(e)}")
        return "Error setting clip start/end markers. Please check the server logs for details."

# ======================================================================
# Advanced MIDI Note Editing
# ======================================================================

@mcp.tool()
def add_notes_extended(ctx: Context, track_index: int, clip_index: int,
                       notes: List[Dict]) -> str:
    """
    Add MIDI notes with Live 11+ extended properties.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    - notes: List of note dictionaries with:
        - pitch (int): MIDI note number (0-127)
        - start_time (float): Start position in beats
        - duration (float): Note duration in beats
        - velocity (int): Note velocity (1-127)
        - mute (bool): Whether the note is muted (optional, default false)
        - probability (float): Note trigger probability 0.0-1.0 (Live 11+, optional)
        - velocity_deviation (float): Random velocity range -127 to 127 (Live 11+, optional)
        - release_velocity (int): Note release velocity 0-127 (Live 11+, optional)
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        if not notes:
            return "No notes provided"
        ableton = get_ableton_connection()
        result = ableton.send_command("add_notes_extended", {
            "track_index": track_index,
            "clip_index": clip_index,
            "notes": notes,
        })
        ext = " (with extended properties)" if result.get("extended") else ""
        return f"Added {result.get('note_count', 0)} notes to clip{ext}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error adding extended notes: {str(e)}")
        return "Error adding extended notes. Please check the server logs for details."

@mcp.tool()
def get_notes_extended(ctx: Context, track_index: int, clip_index: int,
                       start_time: float = 0.0, time_span: float = 0.0) -> str:
    """
    Get MIDI notes with Live 11+ extended properties (probability, velocity_deviation, release_velocity).

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    - start_time: Start time in beats (default: 0.0)
    - time_span: Duration in beats to retrieve (default: 0.0 = entire clip)
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("get_notes_extended", {
            "track_index": track_index,
            "clip_index": clip_index,
            "start_time": start_time,
            "time_span": time_span,
        })
        return json.dumps(result, indent=2)
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error getting extended notes: {str(e)}")
        return "Error getting extended notes. Please check the server logs for details."

@mcp.tool()
def remove_notes_range(ctx: Context, track_index: int, clip_index: int,
                       from_time: float = 0.0, time_span: float = 0.0,
                       from_pitch: int = 0, pitch_span: int = 128) -> str:
    """
    Selectively remove MIDI notes within a specific time and pitch range.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    - from_time: Start time in beats (default: 0.0)
    - time_span: Time range in beats (default: 0.0 = entire clip)
    - from_pitch: Lowest MIDI pitch to remove (default: 0)
    - pitch_span: Range of pitches to remove (default: 128 = all)
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("remove_notes_range", {
            "track_index": track_index,
            "clip_index": clip_index,
            "from_time": from_time,
            "time_span": time_span,
            "from_pitch": from_pitch,
            "pitch_span": pitch_span,
        })
        return f"Removed {result.get('notes_removed', 0)} notes from range (time={from_time}-{from_time+time_span}, pitch={from_pitch}-{from_pitch+pitch_span})"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error removing notes range: {str(e)}")
        return "Error removing notes range. Please check the server logs for details."

# ======================================================================
# Automation Reading & Editing
# ======================================================================

@mcp.tool()
def get_clip_automation(ctx: Context, track_index: int, clip_index: int,
                        parameter_name: str) -> str:
    """
    Read existing automation from a clip for a specific parameter.

    Samples the automation envelope at 64 evenly-spaced points across the clip length.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    - parameter_name: Name of the parameter (e.g., "Volume", "Pan", or any device parameter name)
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("get_clip_automation", {
            "track_index": track_index,
            "clip_index": clip_index,
            "parameter_name": parameter_name,
        })
        if not result.get("has_automation"):
            reason = result.get("reason", "No automation found")
            return f"No automation for '{parameter_name}': {reason}"
        return json.dumps(result, indent=2)
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error getting clip automation: {str(e)}")
        return "Error getting clip automation. Please check the server logs for details."

@mcp.tool()
def clear_clip_automation(ctx: Context, track_index: int, clip_index: int,
                          parameter_name: str) -> str:
    """
    Clear automation for a specific parameter in a clip.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    - parameter_name: Name of the parameter to clear automation for
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("clear_clip_automation", {
            "track_index": track_index,
            "clip_index": clip_index,
            "parameter_name": parameter_name,
        })
        if result.get("cleared"):
            return f"Cleared automation for '{parameter_name}'"
        return f"Could not clear automation for '{parameter_name}': {result.get('reason', 'Unknown')}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error clearing clip automation: {str(e)}")
        return "Error clearing clip automation. Please check the server logs for details."

@mcp.tool()
def list_clip_automated_parameters(ctx: Context, track_index: int, clip_index: int) -> str:
    """
    List all parameters that have automation in a given clip.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("list_clip_automated_params", {
            "track_index": track_index,
            "clip_index": clip_index,
        })
        params = result.get("automated_parameters", [])
        if not params:
            return "No automated parameters found in this clip"
        output = f"Found {len(params)} automated parameter(s):\n\n"
        for p in params:
            source = p.get("source", "Unknown")
            output += f"• {p.get('name', '?')} (source: {source})"
            if "device_index" in p:
                output += f" [device {p['device_index']}]"
            output += "\n"
        return output
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error listing automated parameters: {str(e)}")
        return "Error listing automated parameters. Please check the server logs for details."

@mcp.tool()
def search_browser(ctx: Context, query: str, category: str = "all") -> str:
    """
    Search the Ableton browser for items matching a query.

    Parameters:
    - query: Search string to find items (searches by name)
    - category: Limit search to category ('all', 'instruments', 'sounds', 'drums', 'audio_effects', 'midi_effects')
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("search_browser", {
            "query": query,
            "category": category
        })

        results = result.get("results", [])
        if not results:
            return f"No results found for '{query}' in category '{category}'"

        formatted_output = f"Found {len(results)} results for '{query}':\n\n"
        for item in results:
            loadable = " [loadable]" if item.get("is_loadable", False) else ""
            formatted_output += f"• {item.get('name', 'Unknown')}{loadable}\n"
            if item.get("uri"):
                formatted_output += f"  URI: {item.get('uri')}\n"

        return formatted_output
    except Exception as e:
        logger.error(f"Error searching browser: {str(e)}")
        return "Error searching browser. Please check the server logs for details."

@mcp.tool()
def load_drum_kit(ctx: Context, track_index: int, rack_uri: str, kit_path: str) -> str:
    """
    Load a drum rack and then load a specific drum kit into it.
    
    Parameters:
    - track_index: The index of the track to load on
    - rack_uri: The URI of the drum rack to load (e.g., 'Drums/Drum Rack')
    - kit_path: Path to the drum kit inside the browser (e.g., 'drums/acoustic/kit1')
    """
    try:
        _validate_index(track_index, "track_index")
        ableton = get_ableton_connection()

        # Step 1: Load the drum rack
        result = ableton.send_command("load_browser_item", {
            "track_index": track_index,
            "item_uri": rack_uri
        })
        
        if not result.get("loaded", False):
            return f"Failed to load drum rack with URI '{rack_uri}'"
        
        # Step 2: Get the drum kit items at the specified path
        kit_result = ableton.send_command("get_browser_items_at_path", {
            "path": kit_path
        })
        
        if "error" in kit_result:
            return f"Loaded drum rack but failed to find drum kit: {kit_result.get('error')}"
        
        # Step 3: Find a loadable drum kit
        kit_items = kit_result.get("items", [])
        loadable_kits = [item for item in kit_items if item.get("is_loadable", False)]
        
        if not loadable_kits:
            return f"Loaded drum rack but no loadable drum kits found at '{kit_path}'"
        
        # Step 4: Load the first loadable kit
        kit_uri = loadable_kits[0].get("uri")
        load_result = ableton.send_command("load_browser_item", {
            "track_index": track_index,
            "item_uri": kit_uri
        })
        
        return f"Loaded drum rack and kit '{loadable_kits[0].get('name')}' on track {track_index}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error loading drum kit: {str(e)}")
        return "Error loading drum kit. Please check the server logs for details."


# --- Max for Live Bridge Tools (optional, require M4L device) ---

@mcp.tool()
def m4l_status(ctx: Context) -> str:
    """Check if the AbletonMCP Max for Live bridge device is loaded and responsive.

    The M4L bridge is an optional device that provides access to hidden/non-automatable
    device parameters via the Live Object Model (LOM). All standard MCP tools work
    without it; only the hidden-parameter tools require it.
    """
    try:
        m4l = get_m4l_connection()
        result = m4l.send_command("ping")
        if result.get("status") == "success":
            version = result.get("result", {}).get("version", "unknown")
            return f"M4L bridge connected (v{version})."
        return "M4L bridge responded but returned unexpected status."
    except ConnectionError as e:
        return f"M4L bridge not connected: {e}"
    except Exception as e:
        logger.error(f"Error checking M4L status: {str(e)}")
        return "Error checking M4L bridge status. Please check the server logs for details."


@mcp.tool()
def discover_device_params(ctx: Context, track_index: int, device_index: int) -> str:
    """Discover ALL parameters for a device including hidden/non-automatable ones.

    Uses the M4L bridge to enumerate every parameter exposed by the Live Object Model,
    which typically includes parameters not visible through the standard Remote Script API.
    Works with any Ableton device (Operator, Wavetable, Simpler, Analog, Drift, etc.).

    Requires the AbletonMCP_Bridge M4L device to be loaded on any track.

    Compare the results with get_device_parameters() to see which parameters are hidden.
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(device_index, "device_index")

        m4l = get_m4l_connection()
        result = m4l.send_command("discover_params", {
            "track_index": track_index,
            "device_index": device_index
        })

        if result.get("status") == "success":
            return json.dumps(result.get("result", {}), indent=2)
        return f"M4L bridge error: {result.get('message', 'Unknown error')}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except ConnectionError as e:
        return f"M4L bridge not available: {e}"
    except Exception as e:
        logger.error(f"Error discovering device params via M4L: {str(e)}")
        return "Error discovering device parameters. Please check the server logs for details."


@mcp.tool()
def get_device_hidden_parameters(ctx: Context, track_index: int, device_index: int) -> str:
    """Get ALL parameters for a device including hidden/non-automatable ones.

    This is similar to get_device_parameters() but uses the M4L bridge to access
    the full Live Object Model parameter tree, which exposes parameters that the
    standard API hides. Works with any Ableton device.

    Requires the AbletonMCP_Bridge M4L device to be loaded on any track.
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(device_index, "device_index")

        m4l = get_m4l_connection()
        result = m4l.send_command("get_hidden_params", {
            "track_index": track_index,
            "device_index": device_index
        })

        if result.get("status") == "success":
            data = result.get("result", {})
            device_name = data.get("device_name", "Unknown")
            device_class = data.get("device_class", "Unknown")
            params = data.get("parameters", [])

            output = f"Device: {device_name} ({device_class})\n"
            output += f"Total LOM parameters: {len(params)}\n\n"

            for p in params:
                quant = " [quantized]" if p.get("is_quantized") else ""
                output += (
                    f"  [{p.get('index', '?')}] {p.get('name', '?')}: "
                    f"{p.get('value', '?')} "
                    f"(range: {p.get('min', '?')} – {p.get('max', '?')}){quant}\n"
                )
                if p.get("value_items"):
                    output += f"       options: {p.get('value_items')}\n"

            return output
        return f"M4L bridge error: {result.get('message', 'Unknown error')}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except ConnectionError as e:
        return f"M4L bridge not available: {e}"
    except Exception as e:
        logger.error(f"Error getting hidden parameters via M4L: {str(e)}")
        return "Error getting hidden device parameters. Please check the server logs for details."


@mcp.tool()
def set_device_hidden_parameter(
    ctx: Context,
    track_index: int,
    device_index: int,
    parameter_index: int,
    value: float
) -> str:
    """Set a device parameter by its LOM index, including hidden/non-automatable ones.

    Use discover_device_params() first to find the parameter index you want to change.
    The value will be clamped to the parameter's valid range.
    Works with any Ableton device.

    Requires the AbletonMCP_Bridge M4L device to be loaded on any track.
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(device_index, "device_index")
        _validate_index(parameter_index, "parameter_index")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError("value must be a number.")

        m4l = get_m4l_connection()
        result = m4l.send_command("set_hidden_param", {
            "track_index": track_index,
            "device_index": device_index,
            "parameter_index": parameter_index,
            "value": value
        })

        if result.get("status") == "success":
            data = result.get("result", {})
            name = data.get("parameter_name", "Unknown")
            actual = data.get("actual_value", "?")
            clamped = data.get("was_clamped", False)
            msg = f"Set parameter [{parameter_index}] '{name}' to {actual}"
            if clamped:
                msg += f" (clamped from requested {value})"
            return msg
        return f"M4L bridge error: {result.get('message', 'Unknown error')}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except ConnectionError as e:
        return f"M4L bridge not available: {e}"
    except Exception as e:
        logger.error(f"Error setting hidden parameter via M4L: {str(e)}")
        return "Error setting hidden device parameter. Please check the server logs for details."


# --- VST/AU Workaround Tool ---

@mcp.tool()
def list_instrument_rack_presets(ctx: Context) -> str:
    """List Instrument Rack presets saved in the user library.

    This is the recommended workaround for loading VST/AU plugins, since
    Ableton's API does not support loading third-party plugins directly.

    Workflow:
      1. Load your VST/AU plugin manually in Ableton
      2. Group it into an Instrument Rack (Cmd+G / Ctrl+G)
      3. Save the rack to your User Library
      4. Use this tool to find it, then load_instrument_or_effect() to load it

    This tool searches the user library for saved device presets (.adg files)
    that can be loaded onto tracks.
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_user_library")

        if not result:
            return "Could not retrieve user library."

        # Recursively collect loadable items from the user library
        presets = []

        def collect_loadable(items, path=""):
            if isinstance(items, list):
                for item in items:
                    collect_loadable(item, path)
            elif isinstance(items, dict):
                name = items.get("name", "")
                is_loadable = items.get("is_loadable", False)
                uri = items.get("uri", "")
                current_path = f"{path}/{name}" if path else name

                if is_loadable and uri:
                    presets.append({
                        "name": name,
                        "path": current_path,
                        "uri": uri
                    })

                # Recurse into children
                children = items.get("children", [])
                if children:
                    collect_loadable(children, current_path)

        collect_loadable(result)

        if not presets:
            return (
                "No loadable presets found in the user library.\n\n"
                "To create a VST/AU wrapper preset:\n"
                "  1. Load your VST/AU plugin manually in Ableton\n"
                "  2. Group it into an Instrument Rack (Cmd+G / Ctrl+G)\n"
                "  3. Save the rack to your User Library (Ctrl+S / Cmd+S on the rack)\n"
                "  4. Run this tool again to find it"
            )

        output = f"Found {len(presets)} loadable preset(s) in user library:\n\n"
        for p in presets:
            output += f"  - {p['name']}\n"
            output += f"    Path: {p['path']}\n"
            output += f"    URI: {p['uri']}\n"
            output += f"    Load with: load_instrument_or_effect(track_index, \"{p['uri']}\")\n\n"

        return output
    except Exception as e:
        logger.error(f"Error listing instrument rack presets: {str(e)}")
        return "Error listing presets. Please check the server logs for details."


# ==========================================================================
# v1.6.0 Feature Tools — Layer 0: Core Primitives
# ==========================================================================

@mcp.tool()
def batch_set_hidden_parameters(
    ctx: Context,
    track_index: int,
    device_index: int,
    parameters: List[Dict[str, float]]
) -> str:
    """Set multiple device parameters at once by their LOM indices (including hidden ones).

    Much faster than calling set_device_hidden_parameter() in a loop —
    all parameters are set in a single round-trip to the M4L bridge.

    Parameters:
    - track_index: The index of the track containing the device
    - device_index: The index of the device on the track
    - parameters: List of {"index": parameter_index, "value": target_value} dicts

    Use discover_device_params() first to find parameter indices.
    Values will be clamped to each parameter's valid range.

    Requires the AbletonMCP_Bridge M4L device to be loaded on any track.
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(device_index, "device_index")
        if not isinstance(parameters, list) or len(parameters) == 0:
            raise ValueError("parameters must be a non-empty list.")
        for i, p in enumerate(parameters):
            if not isinstance(p, dict):
                raise ValueError(f"Parameter at index {i} must be a dictionary.")
            if "index" not in p or "value" not in p:
                raise ValueError(f"Parameter at index {i} must have 'index' and 'value' keys.")

        m4l = get_m4l_connection()
        result = m4l.send_command("batch_set_hidden_params", {
            "track_index": track_index,
            "device_index": device_index,
            "parameters": parameters
        })

        if result.get("status") == "success":
            data = result.get("result", {})
            total = data.get("total_requested", 0)
            ok = data.get("params_set", 0)
            failed = data.get("params_failed", 0)
            return f"Batch set complete: {ok}/{total} parameters set successfully ({failed} failed)."
        return f"M4L bridge error: {result.get('message', 'Unknown error')}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except ConnectionError as e:
        return f"M4L bridge not available: {e}"
    except Exception as e:
        logger.error(f"Error batch setting hidden parameters via M4L: {str(e)}")
        return f"Error batch setting parameters: {str(e)}"


@mcp.tool()
def snapshot_device_state(
    ctx: Context,
    track_index: int,
    device_index: int,
    snapshot_name: str = ""
) -> str:
    """Capture the complete state of a device (all parameters including hidden ones).

    Stores the snapshot in memory with a unique ID for later recall.
    Use restore_device_snapshot() to restore a saved state.
    Use list_snapshots() to see all stored snapshots.

    Parameters:
    - track_index: The index of the track containing the device
    - device_index: The index of the device on the track
    - snapshot_name: Optional human-readable name for the snapshot

    Requires the AbletonMCP_Bridge M4L device to be loaded on any track.
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(device_index, "device_index")

        m4l = get_m4l_connection()
        result = m4l.send_command("discover_params", {
            "track_index": track_index,
            "device_index": device_index
        })

        if result.get("status") != "success":
            return f"M4L bridge error: {result.get('message', 'Unknown error')}"

        data = result.get("result", {})
        snapshot_id = str(uuid.uuid4())[:8]
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

        snapshot = {
            "id": snapshot_id,
            "name": snapshot_name or f"{data.get('device_name', 'Unknown')}_{snapshot_id}",
            "timestamp": timestamp,
            "track_index": track_index,
            "device_index": device_index,
            "device_name": data.get("device_name", "Unknown"),
            "device_class": data.get("device_class", "Unknown"),
            "parameter_count": data.get("parameter_count", 0),
            "parameters": data.get("parameters", [])
        }

        _snapshot_store[snapshot_id] = snapshot

        return (
            f"Snapshot saved: '{snapshot['name']}' (ID: {snapshot_id})\n"
            f"Device: {snapshot['device_name']} ({snapshot['device_class']})\n"
            f"Parameters captured: {snapshot['parameter_count']}\n"
            f"Timestamp: {timestamp}"
        )
    except ValueError as e:
        return f"Invalid input: {e}"
    except ConnectionError as e:
        return f"M4L bridge not available: {e}"
    except Exception as e:
        logger.error(f"Error snapshotting device state: {str(e)}")
        return f"Error capturing device snapshot: {str(e)}"


@mcp.tool()
def restore_device_snapshot(
    ctx: Context,
    snapshot_id: str,
    track_index: int = -1,
    device_index: int = -1
) -> str:
    """Restore a previously captured device state from a snapshot.

    Applies all parameter values from the snapshot to the device using batch set.
    By default restores to the same track/device the snapshot was taken from.
    Optionally specify different track_index/device_index to apply to a different device.

    Parameters:
    - snapshot_id: The ID of the snapshot to restore (from snapshot_device_state or list_snapshots)
    - track_index: Override target track (-1 = use original track from snapshot)
    - device_index: Override target device (-1 = use original device from snapshot)

    Requires the AbletonMCP_Bridge M4L device to be loaded on any track.
    """
    try:
        if snapshot_id not in _snapshot_store:
            return f"Snapshot '{snapshot_id}' not found. Use list_snapshots() to see available snapshots."

        snapshot = _snapshot_store[snapshot_id]
        target_track = track_index if track_index >= 0 else snapshot["track_index"]
        target_device = device_index if device_index >= 0 else snapshot["device_index"]

        params_to_set = [{"index": p["index"], "value": p["value"]} for p in snapshot["parameters"]]

        if not params_to_set:
            return "Snapshot contains no parameters to restore."

        m4l = get_m4l_connection()
        result = m4l.send_command("batch_set_hidden_params", {
            "track_index": target_track,
            "device_index": target_device,
            "parameters": params_to_set
        })

        if result.get("status") == "success":
            data = result.get("result", {})
            ok = data.get("params_set", 0)
            failed = data.get("params_failed", 0)
            return (
                f"Restored snapshot '{snapshot['name']}' (ID: {snapshot_id})\n"
                f"Target: track {target_track}, device {target_device}\n"
                f"Parameters restored: {ok}/{len(params_to_set)} ({failed} failed)"
            )
        return f"M4L bridge error: {result.get('message', 'Unknown error')}"
    except ConnectionError as e:
        return f"M4L bridge not available: {e}"
    except Exception as e:
        logger.error(f"Error restoring device snapshot: {str(e)}")
        return f"Error restoring device snapshot: {str(e)}"


@mcp.tool()
def list_snapshots(ctx: Context) -> str:
    """List all stored device state snapshots.

    Shows snapshot IDs, names, device info, and timestamps.
    Use snapshot IDs with restore_device_snapshot() to recall states.
    """
    non_group = {k: v for k, v in _snapshot_store.items() if v.get("type") != "group"}
    if not non_group:
        return "No snapshots stored. Use snapshot_device_state() to capture a device state."

    output = f"Stored snapshots ({len(non_group)}):\n\n"
    for sid, snap in non_group.items():
        output += (
            f"  ID: {sid}\n"
            f"  Name: {snap['name']}\n"
            f"  Device: {snap.get('device_name', '?')} ({snap.get('device_class', '?')})\n"
            f"  Location: track {snap.get('track_index', '?')}, device {snap.get('device_index', '?')}\n"
            f"  Parameters: {snap.get('parameter_count', '?')}\n"
            f"  Captured: {snap.get('timestamp', '?')}\n\n"
        )
    return output


@mcp.tool()
def delete_snapshot(ctx: Context, snapshot_id: str) -> str:
    """Delete a stored device state snapshot.

    Parameters:
    - snapshot_id: The ID of the snapshot to delete
    """
    if snapshot_id not in _snapshot_store:
        return f"Snapshot '{snapshot_id}' not found."
    name = _snapshot_store[snapshot_id].get("name", snapshot_id)
    del _snapshot_store[snapshot_id]
    return f"Deleted snapshot '{name}' (ID: {snapshot_id})."


@mcp.tool()
def get_snapshot_details(ctx: Context, snapshot_id: str) -> str:
    """Get the full parameter details of a stored snapshot.

    Parameters:
    - snapshot_id: The ID of the snapshot to inspect
    """
    if snapshot_id not in _snapshot_store:
        return f"Snapshot '{snapshot_id}' not found."

    snap = _snapshot_store[snapshot_id]
    output = (
        f"Snapshot: {snap.get('name', snapshot_id)} (ID: {snapshot_id})\n"
        f"Device: {snap.get('device_name', '?')} ({snap.get('device_class', '?')})\n"
        f"Location: track {snap.get('track_index', '?')}, device {snap.get('device_index', '?')}\n"
        f"Captured: {snap.get('timestamp', '?')}\n"
        f"Parameters ({snap.get('parameter_count', 0)}):\n\n"
    )
    for p in snap.get("parameters", []):
        quant = " [quantized]" if p.get("is_quantized") else ""
        output += (
            f"  [{p.get('index', '?')}] {p.get('name', '?')}: "
            f"{p.get('value', '?')} "
            f"(range: {p.get('min', '?')} - {p.get('max', '?')}){quant}\n"
        )
    return output


@mcp.tool()
def delete_all_snapshots(ctx: Context) -> str:
    """Delete all stored snapshots, macros, and parameter maps.

    Clears all in-memory feature data. This cannot be undone.
    """
    global _snapshot_store, _macro_store, _param_map_store
    count = len(_snapshot_store) + len(_macro_store) + len(_param_map_store)
    _snapshot_store = {}
    _macro_store = {}
    _param_map_store = {}
    return f"Cleared all feature data: {count} items deleted."


# ==========================================================================
# v1.6.0 Feature Tools — Feature 5: Device State Versioning & Undo
# ==========================================================================

@mcp.tool()
def snapshot_all_devices(
    ctx: Context,
    track_indices: List[int],
    snapshot_name: str = ""
) -> str:
    """Snapshot the state of all devices across one or more tracks.

    Captures every device on the specified tracks into a group of snapshots
    that can be restored together with restore_group_snapshot().

    Parameters:
    - track_indices: List of track indices to snapshot
    - snapshot_name: Optional name for the group snapshot

    Requires the AbletonMCP_Bridge M4L device to be loaded on any track.
    """
    try:
        if not isinstance(track_indices, list) or len(track_indices) == 0:
            raise ValueError("track_indices must be a non-empty list of integers.")
        for ti in track_indices:
            _validate_index(ti, "track_index")

        m4l = get_m4l_connection()
        ableton = get_ableton_connection()
        group_id = str(uuid.uuid4())[:8]
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        snapshot_ids = []
        device_count = 0

        for ti in track_indices:
            track_info = ableton.send_command("get_track_info", {"track_index": ti})
            devices = track_info.get("devices", [])

            for di, dev in enumerate(devices):
                result = m4l.send_command("discover_params", {
                    "track_index": ti,
                    "device_index": di
                })

                if result.get("status") != "success":
                    continue

                data = result.get("result", {})
                snap_id = str(uuid.uuid4())[:8]

                _snapshot_store[snap_id] = {
                    "id": snap_id,
                    "group_id": group_id,
                    "name": f"{data.get('device_name', 'Unknown')}_t{ti}_d{di}",
                    "timestamp": timestamp,
                    "track_index": ti,
                    "device_index": di,
                    "device_name": data.get("device_name", "Unknown"),
                    "device_class": data.get("device_class", "Unknown"),
                    "parameter_count": data.get("parameter_count", 0),
                    "parameters": data.get("parameters", [])
                }
                snapshot_ids.append(snap_id)
                device_count += 1

        group_name = snapshot_name or f"group_{group_id}"

        _snapshot_store[f"group_{group_id}"] = {
            "id": f"group_{group_id}",
            "type": "group",
            "name": group_name,
            "timestamp": timestamp,
            "track_indices": track_indices,
            "snapshot_ids": snapshot_ids,
            "device_count": device_count
        }

        return (
            f"Group snapshot '{group_name}' saved (ID: group_{group_id})\n"
            f"Tracks: {track_indices}\n"
            f"Devices captured: {device_count}\n"
            f"Individual snapshot IDs: {', '.join(snapshot_ids)}"
        )
    except ValueError as e:
        return f"Invalid input: {e}"
    except ConnectionError as e:
        return f"M4L bridge not available: {e}"
    except Exception as e:
        logger.error(f"Error snapshotting all devices: {str(e)}")
        return f"Error capturing group snapshot: {str(e)}"


@mcp.tool()
def restore_group_snapshot(ctx: Context, group_id: str) -> str:
    """Restore all device states from a group snapshot.

    Restores every device captured in a snapshot_all_devices() call.

    Parameters:
    - group_id: The group snapshot ID (starts with 'group_')

    Requires the AbletonMCP_Bridge M4L device to be loaded on any track.
    """
    try:
        if group_id not in _snapshot_store:
            return f"Group snapshot '{group_id}' not found."

        group = _snapshot_store[group_id]
        if group.get("type") != "group":
            return f"'{group_id}' is not a group snapshot. Use restore_device_snapshot() instead."

        m4l = get_m4l_connection()
        total_devices = 0
        total_params = 0
        total_failed = 0

        for snap_id in group.get("snapshot_ids", []):
            if snap_id not in _snapshot_store:
                continue

            snap = _snapshot_store[snap_id]
            params_to_set = [{"index": p["index"], "value": p["value"]} for p in snap.get("parameters", [])]

            if not params_to_set:
                continue

            result = m4l.send_command("batch_set_hidden_params", {
                "track_index": snap["track_index"],
                "device_index": snap["device_index"],
                "parameters": params_to_set
            })

            if result.get("status") == "success":
                data = result.get("result", {})
                total_params += data.get("params_set", 0)
                total_failed += data.get("params_failed", 0)
            total_devices += 1

        return (
            f"Restored group snapshot '{group['name']}'\n"
            f"Devices restored: {total_devices}\n"
            f"Parameters restored: {total_params} ({total_failed} failed)"
        )
    except ConnectionError as e:
        return f"M4L bridge not available: {e}"
    except Exception as e:
        logger.error(f"Error restoring group snapshot: {str(e)}")
        return f"Error restoring group snapshot: {str(e)}"


@mcp.tool()
def compare_snapshots(ctx: Context, snapshot_a_id: str, snapshot_b_id: str) -> str:
    """Compare two device snapshots and show parameter differences.

    Useful for understanding what changed between two states.

    Parameters:
    - snapshot_a_id: First snapshot ID
    - snapshot_b_id: Second snapshot ID
    """
    if snapshot_a_id not in _snapshot_store:
        return f"Snapshot '{snapshot_a_id}' not found."
    if snapshot_b_id not in _snapshot_store:
        return f"Snapshot '{snapshot_b_id}' not found."

    snap_a = _snapshot_store[snapshot_a_id]
    snap_b = _snapshot_store[snapshot_b_id]

    a_by_index = {p["index"]: p for p in snap_a.get("parameters", [])}
    b_by_index = {p["index"]: p for p in snap_b.get("parameters", [])}

    all_indices = sorted(set(a_by_index.keys()) | set(b_by_index.keys()))

    changed = []
    unchanged = 0

    for idx in all_indices:
        in_a = idx in a_by_index
        in_b = idx in b_by_index

        if in_a and in_b:
            val_a = a_by_index[idx]["value"]
            val_b = b_by_index[idx]["value"]
            if abs(val_a - val_b) > 0.001:
                changed.append({
                    "index": idx,
                    "name": a_by_index[idx].get("name", "?"),
                    "value_a": val_a,
                    "value_b": val_b,
                    "delta": val_b - val_a
                })
            else:
                unchanged += 1
        else:
            unchanged += 1

    output = (
        f"Comparison: '{snap_a.get('name', snapshot_a_id)}' vs '{snap_b.get('name', snapshot_b_id)}'\n"
        f"Changed: {len(changed)} | Unchanged: {unchanged}\n\n"
    )

    if changed:
        output += "Changed parameters:\n"
        for c in changed:
            direction = "+" if c["delta"] > 0 else ""
            output += (
                f"  [{c['index']}] {c['name']}: "
                f"{c['value_a']:.4f} -> {c['value_b']:.4f} "
                f"({direction}{c['delta']:.4f})\n"
            )
    else:
        output += "No parameter differences found.\n"

    return output


# ==========================================================================
# v1.6.0 Feature Tools — Feature 4: Preset Morph Engine
# ==========================================================================

@mcp.tool()
def morph_between_snapshots(
    ctx: Context,
    snapshot_a_id: str,
    snapshot_b_id: str,
    position: float,
    track_index: int = -1,
    device_index: int = -1
) -> str:
    """Morph between two device snapshots by interpolating all parameters.

    Takes two previously captured snapshots and smoothly blends between them.
    Position 0.0 = fully snapshot A, position 1.0 = fully snapshot B.
    Quantized parameters (e.g. waveform selectors) snap at the midpoint.

    Parameters:
    - snapshot_a_id: ID of the first snapshot (position 0.0)
    - snapshot_b_id: ID of the second snapshot (position 1.0)
    - position: Morph position (0.0 to 1.0)
    - track_index: Override target track (-1 = use snapshot A's track)
    - device_index: Override target device (-1 = use snapshot A's device)

    Requires the AbletonMCP_Bridge M4L device to be loaded on any track.
    """
    try:
        _validate_range(position, "position", 0.0, 1.0)

        if snapshot_a_id not in _snapshot_store:
            return f"Snapshot A '{snapshot_a_id}' not found."
        if snapshot_b_id not in _snapshot_store:
            return f"Snapshot B '{snapshot_b_id}' not found."

        snap_a = _snapshot_store[snapshot_a_id]
        snap_b = _snapshot_store[snapshot_b_id]

        target_track = track_index if track_index >= 0 else snap_a["track_index"]
        target_device = device_index if device_index >= 0 else snap_a["device_index"]

        b_by_index = {p["index"]: p for p in snap_b.get("parameters", [])}

        params_to_set = []
        skipped = 0
        for p_a in snap_a.get("parameters", []):
            idx = p_a["index"]
            if idx not in b_by_index:
                skipped += 1
                continue

            p_b = b_by_index[idx]
            val_a = p_a["value"]
            val_b = p_b["value"]

            if p_a.get("is_quantized", False):
                interpolated = val_a if position < 0.5 else val_b
            else:
                interpolated = val_a + (val_b - val_a) * position

            params_to_set.append({"index": idx, "value": interpolated})

        if not params_to_set:
            return "No matching parameters found between the two snapshots."

        m4l = get_m4l_connection()
        result = m4l.send_command("batch_set_hidden_params", {
            "track_index": target_track,
            "device_index": target_device,
            "parameters": params_to_set
        })

        if result.get("status") == "success":
            data = result.get("result", {})
            ok = data.get("params_set", 0)
            return (
                f"Morph at position {position:.2f} "
                f"('{snap_a.get('name', snapshot_a_id)}' -> '{snap_b.get('name', snapshot_b_id)}')\n"
                f"Interpolated {ok} parameters, skipped {skipped} (unmatched)\n"
                f"Target: track {target_track}, device {target_device}"
            )
        return f"M4L bridge error: {result.get('message', 'Unknown error')}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except ConnectionError as e:
        return f"M4L bridge not available: {e}"
    except Exception as e:
        logger.error(f"Error morphing between snapshots: {str(e)}")
        return f"Error during morph: {str(e)}"


# ==========================================================================
# v1.6.0 Feature Tools — Feature 2: Smart Macro Controller
# ==========================================================================

@mcp.tool()
def create_macro_controller(
    ctx: Context,
    name: str,
    mappings: List[Dict[str, Any]]
) -> str:
    """Create a macro controller that links multiple device parameters together.

    A macro controller maps a single 0.0-1.0 value to multiple device parameters,
    each with their own range mapping.

    Parameters:
    - name: Human-readable name for the macro (e.g., "Brightness", "Intensity")
    - mappings: List of parameter mappings, each with:
        - track_index: int
        - device_index: int
        - parameter_index: int (LOM index from discover_device_params)
        - min_value: float (parameter value when macro = 0.0)
        - max_value: float (parameter value when macro = 1.0)

    After creation, use set_macro_value() to control all linked parameters at once.

    Requires the AbletonMCP_Bridge M4L device to be loaded on any track.
    """
    try:
        if not isinstance(mappings, list) or len(mappings) == 0:
            raise ValueError("mappings must be a non-empty list.")
        required = {"track_index", "device_index", "parameter_index", "min_value", "max_value"}
        for i, m in enumerate(mappings):
            if not isinstance(m, dict):
                raise ValueError(f"Mapping at index {i} must be a dictionary.")
            missing = required - m.keys()
            if missing:
                raise ValueError(f"Mapping at index {i} missing keys: {', '.join(sorted(missing))}")

        macro_id = str(uuid.uuid4())[:8]
        _macro_store[macro_id] = {
            "id": macro_id,
            "name": name,
            "mappings": mappings,
            "current_value": 0.0,
            "created": time.strftime("%Y-%m-%d %H:%M:%S")
        }

        output = (
            f"Macro controller '{name}' created (ID: {macro_id})\n"
            f"Linked parameters: {len(mappings)}\n"
            f"Use set_macro_value('{macro_id}', value) to control (0.0-1.0)\n\n"
            f"Mappings:\n"
        )
        for m in mappings:
            output += (
                f"  - Track {m['track_index']}, Device {m['device_index']}, "
                f"Param [{m['parameter_index']}]: "
                f"{m['min_value']} -> {m['max_value']}\n"
            )

        return output
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error creating macro controller: {str(e)}")
        return f"Error creating macro controller: {str(e)}"


@mcp.tool()
def set_macro_value(ctx: Context, macro_id: str, value: float) -> str:
    """Set the value of a macro controller, updating all linked parameters.

    Interpolates the macro value (0.0-1.0) across all mapped parameters
    and applies them via batch set.

    Parameters:
    - macro_id: The ID of the macro controller
    - value: The macro value (0.0 to 1.0)

    Requires the AbletonMCP_Bridge M4L device to be loaded on any track.
    """
    try:
        if macro_id not in _macro_store:
            return f"Macro '{macro_id}' not found. Use list_macros() to see available macros."
        _validate_range(value, "value", 0.0, 1.0)

        macro = _macro_store[macro_id]
        macro["current_value"] = value

        grouped: Dict[tuple, list] = {}
        for m in macro["mappings"]:
            key = (m["track_index"], m["device_index"])
            interpolated = m["min_value"] + (m["max_value"] - m["min_value"]) * value
            if key not in grouped:
                grouped[key] = []
            grouped[key].append({"index": m["parameter_index"], "value": interpolated})

        m4l = get_m4l_connection()
        total_set = 0
        total_failed = 0

        for (ti, di), params in grouped.items():
            result = m4l.send_command("batch_set_hidden_params", {
                "track_index": ti,
                "device_index": di,
                "parameters": params
            })
            if result.get("status") == "success":
                data = result.get("result", {})
                total_set += data.get("params_set", 0)
                total_failed += data.get("params_failed", 0)

        return (
            f"Macro '{macro['name']}' set to {value:.2f}\n"
            f"Updated {total_set} parameters across {len(grouped)} device(s) "
            f"({total_failed} failed)"
        )
    except ValueError as e:
        return f"Invalid input: {e}"
    except ConnectionError as e:
        return f"M4L bridge not available: {e}"
    except Exception as e:
        logger.error(f"Error setting macro value: {str(e)}")
        return f"Error setting macro value: {str(e)}"


@mcp.tool()
def list_macros(ctx: Context) -> str:
    """List all created macro controllers.

    Shows macro IDs, names, number of linked parameters, and current values.
    """
    if not _macro_store:
        return "No macro controllers created. Use create_macro_controller() to create one."

    output = f"Macro controllers ({len(_macro_store)}):\n\n"
    for mid, macro in _macro_store.items():
        output += (
            f"  ID: {mid}\n"
            f"  Name: {macro['name']}\n"
            f"  Linked params: {len(macro['mappings'])}\n"
            f"  Current value: {macro['current_value']:.2f}\n"
            f"  Created: {macro['created']}\n\n"
        )
    return output


@mcp.tool()
def delete_macro(ctx: Context, macro_id: str) -> str:
    """Delete a macro controller.

    Parameters:
    - macro_id: The ID of the macro to delete
    """
    if macro_id not in _macro_store:
        return f"Macro '{macro_id}' not found."
    name = _macro_store[macro_id]["name"]
    del _macro_store[macro_id]
    return f"Deleted macro controller '{name}' (ID: {macro_id})."


# ==========================================================================
# v1.6.0 Feature Tools — Feature 1: Intelligent Preset Generator
# ==========================================================================

@mcp.tool()
def generate_preset(
    ctx: Context,
    track_index: int,
    device_index: int,
    description: str,
    variation_count: int = 1
) -> str:
    """Generate an intelligent preset for a device based on a text description.

    Discovers all parameters on the target device and returns them so Claude can
    intelligently set values based on the description (e.g., "bright bass",
    "warm pad", "aggressive lead"). The current state is auto-saved as a snapshot
    for easy revert.

    After calling this tool, use batch_set_hidden_parameters() to apply the preset.
    Use restore_device_snapshot() with the revert snapshot ID to undo.

    Parameters:
    - track_index: The index of the track containing the device
    - device_index: The index of the device on the track
    - description: Text description of the desired sound (e.g., "bright plucky bass")
    - variation_count: How many variations to suggest (default: 1)

    Requires the AbletonMCP_Bridge M4L device to be loaded on any track.
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(device_index, "device_index")
        if variation_count < 1 or variation_count > 5:
            raise ValueError("variation_count must be between 1 and 5.")

        m4l = get_m4l_connection()
        result = m4l.send_command("discover_params", {
            "track_index": track_index,
            "device_index": device_index
        })

        if result.get("status") != "success":
            return f"M4L bridge error: {result.get('message', 'Unknown error')}"

        data = result.get("result", {})
        device_name = data.get("device_name", "Unknown")
        device_class = data.get("device_class", "Unknown")
        params = data.get("parameters", [])

        # Auto-snapshot current state for revert
        snapshot_id = str(uuid.uuid4())[:8]
        _snapshot_store[snapshot_id] = {
            "id": snapshot_id,
            "name": f"pre_preset_{device_name}_{snapshot_id}",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "track_index": track_index,
            "device_index": device_index,
            "device_name": device_name,
            "device_class": device_class,
            "parameter_count": len(params),
            "parameters": params
        }

        output = (
            f"PRESET GENERATION for: '{description}'\n"
            f"Device: {device_name} ({device_class}) on track {track_index}, device {device_index}\n"
            f"Variations requested: {variation_count}\n"
            f"Revert snapshot ID: {snapshot_id} (use restore_device_snapshot to undo)\n\n"
            f"Device has {len(params)} parameters:\n\n"
        )

        for p in params:
            quant = " [quantized]" if p.get("is_quantized") else ""
            items = f" options: {p.get('value_items')}" if p.get("value_items") else ""
            output += (
                f"  [{p['index']}] {p.get('name', '?')}: "
                f"current={p.get('value', '?')} "
                f"(range: {p.get('min', '?')}-{p.get('max', '?')}"
                f", default={p.get('default_value', '?')}){quant}{items}\n"
            )

        output += (
            f"\nNow calculate appropriate values for each parameter based on the description "
            f"'{description}' and device type '{device_class}'. Then call "
            f"batch_set_hidden_parameters(track_index={track_index}, device_index={device_index}, "
            f"parameters=[...]) with the calculated values."
        )

        return output
    except ValueError as e:
        return f"Invalid input: {e}"
    except ConnectionError as e:
        return f"M4L bridge not available: {e}"
    except Exception as e:
        logger.error(f"Error in preset generation: {str(e)}")
        return f"Error during preset generation: {str(e)}"


# ==========================================================================
# v1.6.0 Feature Tools — Feature 3: VST/AU Parameter Mapper
# ==========================================================================

@mcp.tool()
def create_parameter_map(
    ctx: Context,
    track_index: int,
    device_index: int,
    friendly_names: List[Dict[str, Any]]
) -> str:
    """Create a custom parameter map with friendly names for a device's parameters.

    Stores a mapping from cryptic parameter names/indices to human-readable names.
    Particularly useful for VST/AU plugins with obscure parameter names.

    Parameters:
    - track_index: The index of the track containing the device
    - device_index: The index of the device on the track
    - friendly_names: List of mappings, each with:
        - parameter_index: int (LOM index)
        - original_name: str (the parameter's actual name)
        - friendly_name: str (human-readable name)
        - category: str (optional grouping like "Filter", "Oscillator", "Envelope")

    Requires the AbletonMCP_Bridge M4L device to be loaded on any track.
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(device_index, "device_index")
        if not isinstance(friendly_names, list) or len(friendly_names) == 0:
            raise ValueError("friendly_names must be a non-empty list.")

        m4l = get_m4l_connection()
        result = m4l.send_command("discover_params", {
            "track_index": track_index,
            "device_index": device_index
        })

        device_name = "Unknown"
        device_class = "Unknown"
        if result.get("status") == "success":
            data = result.get("result", {})
            device_name = data.get("device_name", "Unknown")
            device_class = data.get("device_class", "Unknown")

        map_id = str(uuid.uuid4())[:8]
        _param_map_store[map_id] = {
            "id": map_id,
            "track_index": track_index,
            "device_index": device_index,
            "device_name": device_name,
            "device_class": device_class,
            "mappings": friendly_names,
            "created": time.strftime("%Y-%m-%d %H:%M:%S")
        }

        output = (
            f"Parameter map created for '{device_name}' (ID: {map_id})\n"
            f"Mapped parameters: {len(friendly_names)}\n\n"
        )

        categories: Dict[str, list] = {}
        for fn in friendly_names:
            cat = fn.get("category", "Uncategorized")
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(fn)

        for cat, maps in categories.items():
            output += f"  [{cat}]\n"
            for m in maps:
                output += (
                    f"    [{m.get('parameter_index', '?')}] "
                    f"'{m.get('original_name', '?')}' -> "
                    f"'{m.get('friendly_name', '?')}'\n"
                )
            output += "\n"

        return output
    except ValueError as e:
        return f"Invalid input: {e}"
    except ConnectionError as e:
        return f"M4L bridge not available: {e}"
    except Exception as e:
        logger.error(f"Error creating parameter map: {str(e)}")
        return f"Error creating parameter map: {str(e)}"


@mcp.tool()
def get_parameter_map(ctx: Context, map_id: str) -> str:
    """Retrieve a stored parameter map with friendly names.

    Parameters:
    - map_id: The ID of the parameter map to retrieve
    """
    if map_id not in _param_map_store:
        return f"Parameter map '{map_id}' not found."
    return json.dumps(_param_map_store[map_id], indent=2)


@mcp.tool()
def list_parameter_maps(ctx: Context) -> str:
    """List all stored parameter maps."""
    if not _param_map_store:
        return "No parameter maps stored. Use create_parameter_map() to create one."

    output = f"Parameter maps ({len(_param_map_store)}):\n\n"
    for mid, pmap in _param_map_store.items():
        output += (
            f"  ID: {mid}\n"
            f"  Device: {pmap.get('device_name', '?')} ({pmap.get('device_class', '?')})\n"
            f"  Location: track {pmap.get('track_index', '?')}, device {pmap.get('device_index', '?')}\n"
            f"  Mapped params: {len(pmap.get('mappings', []))}\n"
            f"  Created: {pmap.get('created', '?')}\n\n"
        )
    return output


@mcp.tool()
def delete_parameter_map(ctx: Context, map_id: str) -> str:
    """Delete a stored parameter map.

    Parameters:
    - map_id: The ID of the parameter map to delete
    """
    if map_id not in _param_map_store:
        return f"Parameter map '{map_id}' not found."
    name = _param_map_store[map_id].get("device_name", map_id)
    del _param_map_store[map_id]
    return f"Deleted parameter map for '{name}' (ID: {map_id})."


# Main execution
def main():
    """Run the MCP server"""
    mcp.run()

if __name__ == "__main__":
    main()