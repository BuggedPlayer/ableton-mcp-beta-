# ableton_mcp_server.py — AbletonMCP Beta
from mcp.server.fastmcp import FastMCP, Context
import socket
import json
import logging
import time
from dataclasses import dataclass
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Any, List, Optional, Union
import uuid
import base64
import struct
import os
import threading
from collections import deque
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
            logger.info("Connected to Ableton at %s:%s", self.host, self.port)
            return True
        except Exception as e:
            logger.error("Failed to connect to Ableton: %s", e)
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
                logger.error("Error disconnecting from Ableton: %s", e)
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
                        logger.debug("Received complete response (%d chars)", len(line))
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
                    logger.error("Socket connection error during receive: %s", e)
                    raise
        except (socket.timeout, json.JSONDecodeError):
            raise
        except Exception as e:
            logger.error("Error during receive: %s", e)
            raise

    def _reconnect(self) -> bool:
        """Force a fresh reconnection, clearing all state."""
        logger.info("Forcing reconnection to Ableton...")
        self.disconnect()
        self._recv_buffer = ""
        return self.connect()

    # Commands that modify Ableton state (need extra delays for stability)
    _MODIFYING_COMMANDS = frozenset([
        "create_midi_track", "create_audio_track", "set_track_name",
        "create_clip", "add_notes_to_clip", "set_clip_name",
        "set_tempo", "fire_clip", "stop_clip", "set_device_parameter", "set_device_parameters_batch",
        "start_playback", "stop_playback", "load_instrument_or_effect",
        "load_sample", "load_drum_kit",
        "arm_track", "disarm_track", "set_arrangement_overdub",
        "start_arrangement_recording", "stop_arrangement_recording",
        "set_loop_start", "set_loop_end", "set_loop_length", "set_playback_position",
        "create_scene", "delete_scene", "duplicate_scene", "fire_scene", "set_scene_name",
        "set_track_color", "set_clip_color",
        "quantize_clip_notes", "transpose_clip_notes", "duplicate_clip",
        "group_tracks", "set_track_volume", "set_track_pan", "set_track_mute",
        "set_track_solo", "set_track_arm", "set_track_send",
        "set_warp_mode", "set_clip_warp", "crop_clip", "reverse_clip",
        "set_clip_loop_points", "set_clip_start_end", "set_clip_looping",
        "duplicate_clip_to_arrangement", "create_clip_automation", "clear_clip_automation",
        "create_track_automation", "clear_track_automation",
        "delete_time", "duplicate_time", "insert_silence",
        "delete_clip", "set_metronome", "tap_tempo", "capture_midi", "apply_groove",
        "freeze_track", "unfreeze_track",
        "create_return_track", "delete_track", "duplicate_track",
        "delete_device", "set_return_track_volume", "set_return_track_pan",
        "set_return_track_mute", "set_return_track_solo", "set_master_volume",
        "clear_clip_notes", "add_notes_extended", "remove_notes_range",
        "duplicate_clip_loop", "set_song_loop", "set_song_time",
    ])

    def send_command(self, command_type: str, params: Dict[str, Any] = None, timeout: float = None) -> Dict[str, Any]:
        """Send a command to Ableton and return the response.

        Includes automatic retry: if the first attempt fails due to a
        socket error, the connection is reset and the command is retried once.
        Adds small delays around modifying commands for stability.
        """
        max_attempts = 2
        is_modifying = command_type in self._MODIFYING_COMMANDS

        for attempt in range(1, max_attempts + 1):
            if not self.sock and not self.connect():
                raise ConnectionError("Not connected to Ableton")

            command = {
                "type": command_type,
                "params": params or {}
            }

            try:
                logger.debug("Sending command: %s (attempt %d)", command_type, attempt)

                # Send the command as newline-delimited JSON
                self.sock.sendall((json.dumps(command) + '\n').encode('utf-8'))

                # Add a small delay after sending modifying commands
                # to give Ableton time to process before we read the response
                if is_modifying:
                    time.sleep(0.1)

                # Set timeout based on command type (caller override takes priority)
                if timeout is None:
                    timeout = 15.0 if is_modifying else 10.0
                # Receive the response (already parsed by receive_full_response)
                response = self.receive_full_response(self.sock, timeout=timeout)
                logger.debug("Response status: %s", response.get('status', 'unknown'))

                if response.get("status") == "error":
                    logger.error("Ableton error: %s", response.get('message'))
                    raise Exception(response.get("message", "Unknown error from Ableton"))

                # Add a small delay after modifying commands complete
                # to let Ableton settle before the next command
                if is_modifying:
                    time.sleep(0.1)

                return response.get("result", {})

            except Exception as e:
                logger.error("Command '%s' attempt %d failed: %s", command_type, attempt, e)
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
                    raise Exception(f"Command '{command_type}' failed after {max_attempts} attempts: {e}")


@dataclass
class M4LConnection:
    """UDP connection to the Max for Live bridge device.

    The M4L bridge provides deep LOM access for hidden device parameters.
    Communication uses two UDP ports:
      - send_port (9878): MCP server → M4L device (commands)
      - recv_port (9879): M4L device → MCP server (responses)
    """
    send_host: str = "127.0.0.1"
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
            # Use exclusive binding — prevents a second instance from sharing this port
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                self.recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            self.recv_sock.bind(("127.0.0.1", self.recv_port))
            self.recv_sock.settimeout(5.0)
            self._connected = True
            logger.info("M4L UDP sockets ready (send→:%d, recv←:%d)", self.send_port, self.recv_port)
            return True
        except Exception as e:
            logger.error("Failed to set up M4L UDP connection: %s", e)
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
            # Use compact JSON (no spaces) + URL-safe base64 without padding.
            # Max's OSC/symbol handling mangles +, /, and = characters.
            params_json = json.dumps(params["parameters"], separators=(",", ":"))
            params_b64 = base64.urlsafe_b64encode(params_json.encode("utf-8")).decode("ascii").rstrip("=")
            return self._build_osc_message("/batch_set_hidden_params", [
                ("i", params["track_index"]),
                ("i", params["device_index"]),
                ("s", params_b64),
                ("s", request_id),
            ])
        # --- Phase 2: Chain navigation ---
        elif command_type == "discover_chains":
            extra_path = params.get("chain_path", "") or ""
            return self._build_osc_message("/discover_chains", [
                ("i", params["track_index"]),
                ("i", params["device_index"]),
                ("s", extra_path),
                ("s", request_id),
            ])
        elif command_type == "get_chain_device_params":
            return self._build_osc_message("/get_chain_device_params", [
                ("i", params["track_index"]),
                ("i", params["device_index"]),
                ("i", params["chain_index"]),
                ("i", params["chain_device_index"]),
                ("s", request_id),
            ])
        elif command_type == "set_chain_device_param":
            return self._build_osc_message("/set_chain_device_param", [
                ("i", params["track_index"]),
                ("i", params["device_index"]),
                ("i", params["chain_index"]),
                ("i", params["chain_device_index"]),
                ("i", params["parameter_index"]),
                ("f", params["value"]),
                ("s", request_id),
            ])
        # --- Phase 3: Simpler/Sample ---
        elif command_type == "get_simpler_info":
            return self._build_osc_message("/get_simpler_info", [
                ("i", params["track_index"]),
                ("i", params["device_index"]),
                ("s", request_id),
            ])
        elif command_type == "set_simpler_sample_props":
            props_json = json.dumps(params["properties"], separators=(",", ":"))
            props_b64 = base64.urlsafe_b64encode(props_json.encode("utf-8")).decode("ascii").rstrip("=")
            return self._build_osc_message("/set_simpler_sample_props", [
                ("i", params["track_index"]),
                ("i", params["device_index"]),
                ("s", props_b64),
                ("s", request_id),
            ])
        elif command_type == "simpler_slice":
            osc_args = [
                ("i", params["track_index"]),
                ("i", params["device_index"]),
                ("s", params["action"]),
            ]
            if params.get("slice_time") is not None:
                osc_args.append(("f", params["slice_time"]))
            osc_args.append(("s", request_id))
            return self._build_osc_message("/simpler_slice", osc_args)
        # --- Phase 4: Wavetable ---
        elif command_type == "get_wavetable_info":
            return self._build_osc_message("/get_wavetable_info", [
                ("i", params["track_index"]),
                ("i", params["device_index"]),
                ("s", request_id),
            ])
        elif command_type == "set_wavetable_modulation":
            return self._build_osc_message("/set_wavetable_modulation", [
                ("i", params["track_index"]),
                ("i", params["device_index"]),
                ("i", params["target_index"]),
                ("i", params["source_index"]),
                ("f", params["amount"]),
                ("s", request_id),
            ])
        elif command_type == "set_wavetable_props":
            props_json = json.dumps(params["properties"], separators=(",", ":"))
            props_b64 = base64.urlsafe_b64encode(props_json.encode("utf-8")).decode("ascii").rstrip("=")
            return self._build_osc_message("/set_wavetable_props", [
                ("i", params["track_index"]),
                ("i", params["device_index"]),
                ("s", props_b64),
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

        # Chunked operations (batch set, param discovery) use deferred
        # callbacks in the M4L device and need longer timeouts.
        if command_type == "batch_set_hidden_params":
            param_count = len(params.get("parameters", []))
            # ~150ms per param (chunk delay + LOM overhead), minimum 10s
            timeout = max(10.0, param_count * 0.15)
        elif command_type in ("discover_params", "get_hidden_params", "get_chain_device_params"):
            # Chunked discovery: ~50ms per 4-param chunk + LOM overhead
            # 93 params → ~24 chunks → ~1.2s + overhead. Use 10s for safety.
            timeout = 10.0
        else:
            timeout = 5.0

        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            if not self._connected:
                if not self.connect():
                    raise ConnectionError("Could not establish M4L UDP connection.")

            # Drain any stale data in the recv socket before sending
            self.recv_sock.setblocking(False)
            try:
                for _ in range(100):
                    self.recv_sock.recvfrom(65535)
            except (BlockingIOError, OSError):
                pass
            self.recv_sock.setblocking(True)
            self.recv_sock.settimeout(timeout)

            try:
                self.send_sock.sendto(osc, (self.send_host, self.send_port))
            except Exception as e:
                logger.error("Failed to send UDP command to M4L (attempt %d): %s", attempt, e)
                if attempt < max_attempts:
                    self.disconnect()
                    time.sleep(0.2)
                    continue
                raise ConnectionError("Failed to send command to M4L bridge.")

            try:
                data, _addr = self.recv_sock.recvfrom(65535)
                osc_str = self._extract_osc_address(data)
                result = self._decode_m4l_payload(osc_str)

                # Check for chunked response (Rev 3: chunk metadata inside JSON)
                if isinstance(result, dict) and "_c" in result and "_t" in result:
                    result = self._reassemble_chunked_response(result, timeout)

                # Verify request_id matches (warn on mismatch but don't fail)
                resp_id = result.get("id", "")
                if resp_id and resp_id != request_id:
                    logger.warning("M4L response id mismatch: expected %s, got %s", request_id, resp_id)
                return result
            except socket.timeout:
                logger.warning("M4L response timeout (attempt %d)", attempt)
                if attempt < max_attempts:
                    self.disconnect()
                    time.sleep(0.2)
                    continue
                raise Exception("Timeout waiting for M4L bridge response. Is the M4L device loaded?")

    @staticmethod
    def _extract_osc_address(data: bytes) -> str:
        """Extract the OSC address string from a raw UDP packet.

        Max's udpsend wraps the outlet symbol as an OSC message:
          [symbol\\0...padding][,\\0\\0\\0]
        """
        null_pos = data.find(b"\x00")
        if null_pos > 0:
            return data[:null_pos].decode("utf-8", errors="replace").strip()
        return data.decode("utf-8", errors="replace").strip()

    @staticmethod
    def _decode_m4l_payload(osc_str: str) -> Dict[str, Any]:
        """Decode a single (non-chunked) M4L response payload."""
        # Try base64 decode first
        try:
            decoded = base64.b64decode(osc_str).decode("utf-8")
            return json.loads(decoded)
        except Exception:
            pass

        # Fallback: raw JSON
        try:
            return json.loads(osc_str)
        except (json.JSONDecodeError, ValueError):
            pass

        # Last resort: strip trailing comma from OSC type tag
        text = osc_str.rstrip(",").strip()
        try:
            decoded = base64.b64decode(text).decode("utf-8")
            return json.loads(decoded)
        except Exception:
            pass

        raise json.JSONDecodeError("Could not parse M4L response", osc_str, 0)

    def _reassemble_chunked_response(self, first_chunk: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        """Reassemble a multi-part chunked response from the M4L bridge.

        Rev 3 protocol — chunk metadata is embedded inside the JSON:
          {"_c": chunk_index, "_t": total_chunks, "_d": "<piece of original json>"}
        Each chunk is base64-encoded independently, so every outlet() call
        sends pure valid base64 (no custom prefixes that break Max's OSC).
        """
        total_chunks = int(first_chunk["_t"])
        chunks: Dict[int, str] = {}
        chunks[int(first_chunk["_c"])] = first_chunk["_d"]
        logger.debug("M4L chunked response (Rev3): chunk %d/%d", int(first_chunk["_c"]) + 1, total_chunks)

        # Receive remaining chunks
        while len(chunks) < total_chunks:
            try:
                data, _addr = self.recv_sock.recvfrom(65535)
                osc_str = self._extract_osc_address(data)
                chunk = self._decode_m4l_payload(osc_str)

                if isinstance(chunk, dict) and "_c" in chunk and "_t" in chunk:
                    idx = int(chunk["_c"])
                    chunks[idx] = chunk["_d"]
                    logger.debug("M4L chunked response (Rev3): chunk %d/%d", idx + 1, total_chunks)
                else:
                    logger.warning("Expected chunk packet, got non-chunk data; ignoring")
                    continue
            except socket.timeout:
                raise Exception(
                    f"Timeout waiting for M4L chunked response: received {len(chunks)}/{total_chunks} chunks"
                )

        # Reassemble: concatenate _d values in order → full original JSON string
        full_json_str = "".join(chunks[i] for i in range(total_chunks))
        logger.debug("M4L chunked response reassembled: %d chars JSON", len(full_json_str))

        return json.loads(full_json_str)

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
    global _server_start_time, _singleton_lock_sock
    try:
        # Singleton guard — prevent duplicate server instances
        try:
            _singleton_lock_sock = _acquire_singleton_lock()
        except RuntimeError as e:
            logger.error(str(e))
            logger.error("Exiting to avoid conflicts.")
            import sys
            sys.exit(1)

        logger.info("AbletonMCP Beta server starting up")
        _server_start_time = time.time()

        try:
            ableton = get_ableton_connection()
            logger.info("Successfully connected to Ableton on startup")
        except Exception as e:
            logger.warning("Could not connect to Ableton on startup: %s", e)
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
                        for _ in range(100):
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
                        logger.info("M4L bridge auto-connected on attempt %d", attempt)
                        _m4l_ping_cache["result"] = True
                        _m4l_ping_cache["timestamp"] = time.time()
                        return
                except socket.timeout:
                    logger.info("M4L auto-connect %d/15: no response, retrying...", attempt)
                except Exception as e:
                    logger.info("M4L auto-connect %d/15: %s", attempt, e)
                time.sleep(2)
            logger.warning("M4L bridge not available after 15 attempts — will retry when needed")

        threading.Thread(target=_m4l_auto_connect, daemon=True, name="m4l-auto-connect").start()

        # Start web dashboard on background thread
        try:
            _start_dashboard_server()
        except Exception as e:
            logger.warning("Dashboard failed to start: %s", e)

        # Pre-populate browser cache in background (so search_browser is instant)
        def _browser_cache_warmup():
            """Background thread: load disk cache instantly, then refresh user categories only."""
            # Step 1: Load from disk (instant, works even before Ableton connects)
            disk_loaded = _load_browser_cache_from_disk()
            if disk_loaded:
                # Check if user cache needs refresh
                with _browser_cache_lock:
                    has_user = any(cat in _browser_cache_by_category
                                  for _, cat in _BROWSER_CATEGORIES_USER)
                if has_user:
                    user_age = _get_user_cache_age()
                    if user_age < 43200:  # 12 hours
                        logger.info("Browser cache ready (user cache %.0f min old, skipping rescan)", user_age / 60)
                        return
                    logger.info("User cache stale (%.1f hrs), will rescan user categories only", user_age / 3600)
                else:
                    logger.info("No user cache found, will scan Plug-ins & User Library")

            # Step 2: Wait for Ableton, then scan user categories only
            time.sleep(5)  # let Ableton & Remote Script fully settle
            for _ in range(20):  # poll up to 10s more for Ableton connection
                if _ableton_connection and _ableton_connection.sock:
                    break
                time.sleep(0.5)
            try:
                _populate_browser_cache(categories=_BROWSER_CATEGORIES_USER)
            except Exception as e:
                logger.warning("Browser cache warmup failed: %s", e)

        threading.Thread(target=_browser_cache_warmup, daemon=True, name="browser-cache-warmup").start()

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
        _release_singleton_lock(_singleton_lock_sock)
        _singleton_lock_sock = None
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
_tool_call_log: deque = deque(maxlen=50)
_tool_call_counts: Dict[str, int] = {}
_tool_call_lock = threading.Lock()
_dashboard_server = None
DASHBOARD_PORT = int(os.environ.get("ABLETON_MCP_DASHBOARD_PORT", "9880"))
SINGLETON_LOCK_PORT = int(os.environ.get("ABLETON_MCP_LOCK_PORT", "9881"))
_singleton_lock_sock: socket.socket = None
_server_log_buffer: deque = deque(maxlen=200)
_server_log_lock = threading.Lock()

def _resolve_device_uri(uri_or_name: str) -> str:
    """Resolve a device name or URI to a loadable URI.

    If the input already looks like a URI (contains ':' or '#'), return as-is.
    Otherwise, look up the name in the dynamic device URI map built from
    the browser cache.  Waits for the warmup thread if the map is empty.
    """
    if ":" in uri_or_name or "#" in uri_or_name:
        return uri_or_name

    name_lower = uri_or_name.strip().lower()

    # Fast O(1) lookup in the dynamic device URI map
    with _browser_cache_lock:
        resolved = _device_uri_map.get(name_lower)
    if resolved:
        logger.info("Resolved device name '%s' to URI '%s'", uri_or_name, resolved)
        return resolved

    # Map is empty — wait for warmup thread to populate it (don't trigger a second scan)
    logger.info("Device map empty, waiting for browser cache warmup...")
    for _ in range(120):  # 120 * 0.5s = 60s max
        time.sleep(0.5)
        with _browser_cache_lock:
            resolved = _device_uri_map.get(name_lower)
        if resolved:
            logger.info("Resolved device name '%s' to URI '%s'", uri_or_name, resolved)
            return resolved
        # Stop waiting if cache is populated but name wasn't found
        with _browser_cache_lock:
            if _browser_cache_flat and not _browser_cache_populating:
                break

    # Fallback: linear scan for exact name match
    for item in _browser_cache_flat:
        if item.get("search_name") == name_lower and item.get("is_loadable") and item.get("uri"):
            resolved = item["uri"]
            logger.info("Resolved device name '%s' via cache scan to URI '%s'", uri_or_name, resolved)
            return resolved

    logger.warning("Could not resolve '%s' to a known URI, passing through as-is", uri_or_name)
    return uri_or_name


def _acquire_singleton_lock() -> socket.socket:
    """Acquire an exclusive TCP port lock to prevent duplicate server instances.

    Returns the bound socket (caller must keep it alive for the server's lifetime).
    Raises RuntimeError if another instance already holds the lock.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", SINGLETON_LOCK_PORT))
        sock.listen(1)
        logger.info("Singleton lock acquired on port %d", SINGLETON_LOCK_PORT)
        return sock
    except OSError as e:
        sock.close()
        raise RuntimeError(
            f"Another AbletonMCP server instance is already running "
            f"(port {SINGLETON_LOCK_PORT} is in use). "
            f"Stop the other instance first."
        ) from e


def _release_singleton_lock(sock: socket.socket):
    """Release the singleton lock by closing the lock socket."""
    if sock:
        try:
            sock.close()
            logger.info("Singleton lock released")
        except Exception:
            pass


class _DashboardLogHandler(logging.Handler):
    """Captures log records into the dashboard ring buffer.

    Stores lightweight tuples (created_float, level_str, message_str) to
    avoid formatting timestamps on every log message.  Timestamps are
    formatted only when the dashboard is actually viewed.
    """

    def emit(self, record):
        try:
            with _server_log_lock:
                _server_log_buffer.append(
                    (record.created, record.levelname, record.getMessage())
                )
        except Exception:
            pass


_dashboard_log_handler = _DashboardLogHandler()
logging.getLogger().addHandler(_dashboard_log_handler)

# M4L ping cache (avoids 5s UDP timeout on every dashboard refresh)
_m4l_ping_cache = {"result": False, "timestamp": 0.0}
_M4L_PING_CACHE_TTL = 5.0

# Browser cache — scans Ableton's browser tree and caches all items for instant search
_browser_cache_flat: List[Dict[str, Any]] = []  # flat list for fast substring search
_browser_cache_by_category: Dict[str, List[Dict[str, Any]]] = {}  # display_name -> items (index for filtered search)
_browser_cache_timestamp: float = 0.0
_BROWSER_CACHE_TTL = 300.0  # 5 minutes
_browser_cache_lock = threading.Lock()
_browser_cache_populating = False  # prevents duplicate scans
_BROWSER_DISK_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".ableton-mcp")
_BROWSER_DISK_CACHE_STOCK_PATH = os.path.join(_BROWSER_DISK_CACHE_DIR, "browser_cache_stock.json")
_BROWSER_DISK_CACHE_USER_PATH = os.path.join(_BROWSER_DISK_CACHE_DIR, "browser_cache_user.json")
_BROWSER_DISK_CACHE_PATH = os.path.join(_BROWSER_DISK_CACHE_DIR, "browser_cache.json")  # legacy, for migration
_BROWSER_DISK_CACHE_MAX_AGE = 86400.0  # 24 hours — user cache ignored if older

# Dynamic device URI map — built from browser cache after each scan.
# Maps lowercase device name -> correct URI from Ableton's LOM.
_device_uri_map: Dict[str, str] = {}

# Category priority for resolving name collisions in _device_uri_map.
# Lower number = higher priority (stock devices beat preset folders).
_CATEGORY_PRIORITY: Dict[str, int] = {
    "Instruments": 0,
    "Audio Effects": 1,
    "MIDI Effects": 2,
    "Max for Live": 3,
    "Plug-ins": 4,
    "Sounds": 5,
    "Drums": 6,
    "Clips": 7,
    "Samples": 8,
    "Packs": 9,
    "User Library": 10,
}

# Root browser categories: (path_root, display_name)
# path_root uses the lowercase attribute name so paths work directly with
# get_browser_items_at_path (which lowercases the first component).
#
# Stock = Ableton's built-in content (never changes, never rescanned).
# User  = 3rd-party plug-ins & user library (may change, rescanned periodically).
_BROWSER_CATEGORIES_STOCK = [
    ("instruments", "Instruments"),
    ("drums", "Drums"),
    ("audio_effects", "Audio Effects"),
    ("midi_effects", "MIDI Effects"),
    ("max_for_live", "Max for Live"),
]

_BROWSER_CATEGORIES_USER = [
    ("plugins", "Plug-ins"),
    ("user_library", "User Library"),
]

_BROWSER_CATEGORIES = _BROWSER_CATEGORIES_STOCK + _BROWSER_CATEGORIES_USER

_BROWSER_CACHE_MAX_DEPTH = 3   # category/device/subcategory (skip preset files)
_BROWSER_CACHE_MAX_ITEMS = 1500

# Maps category keys to display names (used by search_browser and get_browser_tree)
_CATEGORY_DISPLAY = {
    "instruments": "Instruments",
    "sounds": "Sounds",
    "drums": "Drums",
    "audio_effects": "Audio Effects",
    "midi_effects": "MIDI Effects",
    "max_for_live": "Max for Live",
    "plugins": "Plug-ins",
    "clips": "Clips",
    "samples": "Samples",
    "packs": "Packs",
    "user_library": "User Library",
}


def _build_device_uri_map(flat_items: List[Dict[str, Any]]) -> Dict[str, str]:
    """Build a lowercase-name -> URI lookup from the flat browser cache.

    Only includes loadable items with a non-empty URI.
    For duplicate names, prefers is_device=True items, then higher-priority
    categories (Instruments > Audio Effects > MIDI Effects > Sounds > Drums).
    """
    uri_map: Dict[str, str] = {}
    quality_map: Dict[str, tuple] = {}

    for item in flat_items:
        if not item.get("is_loadable") or not item.get("uri"):
            continue

        name_lower = item.get("search_name", item.get("name", "").lower())
        if not name_lower:
            continue

        is_device = item.get("is_device", False)
        cat_priority = _CATEGORY_PRIORITY.get(item.get("category", ""), 99)
        new_quality = (is_device, -cat_priority)

        if name_lower not in uri_map or new_quality > quality_map[name_lower]:
            uri_map[name_lower] = item["uri"]
            quality_map[name_lower] = new_quality

    return uri_map


def _try_load_cache_file(path: str, label: str, check_age: bool = False) -> Optional[Dict[str, Any]]:
    """Try to load a cache file from disk.

    Returns a dict with 'flat', 'by_category', 'timestamp' on success, or None.
    If check_age is True, returns None when the file is older than _BROWSER_DISK_CACHE_MAX_AGE.
    """
    try:
        if not os.path.exists(path):
            return None

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict) or data.get("version") != 1:
            logger.warning("%s has unknown format, skipping", label)
            return None

        flat = data.get("flat", [])
        by_cat = data.get("by_category", {})
        disk_timestamp = data.get("timestamp", 0.0)

        if not flat:
            return None

        if check_age:
            age = time.time() - disk_timestamp
            if age > _BROWSER_DISK_CACHE_MAX_AGE:
                logger.info("%s is %.1f hours old (max %.1f), skipping",
                            label, age / 3600, _BROWSER_DISK_CACHE_MAX_AGE / 3600)
                return None

        logger.info("Loaded %s: %d items, %d categories (%.1f min old)",
                    label, len(flat), len(by_cat), (time.time() - disk_timestamp) / 60)
        return {"flat": flat, "by_category": by_cat, "timestamp": disk_timestamp}

    except Exception as e:
        logger.warning("Failed to load %s: %s", label, e)
        return None


def _write_cache_file(path: str, flat: list, by_cat: dict, timestamp: float) -> bool:
    """Atomically write a cache tier to disk.

    For the stock cache file, refuses to overwrite with fewer items than
    the existing file (protects against partial-scan corruption).
    """
    try:
        # Protect stock cache from being overwritten with fewer items
        if path == _BROWSER_DISK_CACHE_STOCK_PATH:
            try:
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        old = json.load(f)
                    old_count = len(old.get("flat", []))
                    if len(flat) < old_count:
                        logger.warning("Skipping stock cache save: new %d < existing %d items",
                                       len(flat), old_count)
                        return False
            except Exception:
                pass

        os.makedirs(_BROWSER_DISK_CACHE_DIR, exist_ok=True)
        data = {
            "version": 1,
            "timestamp": timestamp,
            "flat": flat,
            "by_category": by_cat,
        }
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
        os.replace(tmp_path, path)
        logger.info("Saved cache to %s (%d items)", os.path.basename(path), len(flat))
        return True
    except Exception as e:
        logger.warning("Failed to save cache to %s: %s", path, e)
        return False


def _migrate_old_cache():
    """Migrate the old single-file browser_cache.json to two-tier stock+user files.

    Splits items by category, writes to the two new files, then deletes the old file.
    Only runs once — if the old file exists and the new files don't.
    """
    if not os.path.exists(_BROWSER_DISK_CACHE_PATH):
        return  # nothing to migrate

    # Don't migrate if new files already exist
    if os.path.exists(_BROWSER_DISK_CACHE_STOCK_PATH) and os.path.exists(_BROWSER_DISK_CACHE_USER_PATH):
        # Clean up old file
        try:
            os.remove(_BROWSER_DISK_CACHE_PATH)
            logger.info("Removed legacy browser_cache.json (already migrated)")
        except Exception:
            pass
        return

    try:
        with open(_BROWSER_DISK_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict) or data.get("version") != 1:
            return

        flat = data.get("flat", [])
        by_cat = data.get("by_category", {})
        ts = data.get("timestamp", 0.0)

        if not flat:
            return

        stock_cat_names = {dn for _, dn in _BROWSER_CATEGORIES_STOCK}

        stock_flat = [i for i in flat if i.get("category") in stock_cat_names]
        user_flat = [i for i in flat if i.get("category") not in stock_cat_names]
        stock_by_cat = {k: v for k, v in by_cat.items() if k in stock_cat_names}
        user_by_cat = {k: v for k, v in by_cat.items() if k not in stock_cat_names}

        migrated = False
        if stock_flat:
            migrated |= _write_cache_file(_BROWSER_DISK_CACHE_STOCK_PATH, stock_flat, stock_by_cat, ts)
        if user_flat:
            migrated |= _write_cache_file(_BROWSER_DISK_CACHE_USER_PATH, user_flat, user_by_cat, ts)

        if migrated:
            os.remove(_BROWSER_DISK_CACHE_PATH)
            logger.info("Migrated legacy cache: %d stock + %d user items", len(stock_flat), len(user_flat))

    except Exception as e:
        logger.warning("Failed to migrate old browser cache: %s", e)


def _save_browser_cache_to_disk() -> bool:
    """Persist the in-memory browser cache to separate stock + user JSON files."""
    stock_cat_names = {dn for _, dn in _BROWSER_CATEGORIES_STOCK}

    with _browser_cache_lock:
        if not _browser_cache_flat:
            return False
        all_items = list(_browser_cache_flat)
        by_cat = dict(_browser_cache_by_category)
        ts = _browser_cache_timestamp

    # Split items by tier
    stock_flat = [i for i in all_items if i.get("category") in stock_cat_names]
    user_flat = [i for i in all_items if i.get("category") not in stock_cat_names]
    stock_by_cat = {k: v for k, v in by_cat.items() if k in stock_cat_names}
    user_by_cat = {k: v for k, v in by_cat.items() if k not in stock_cat_names}

    saved = False
    if stock_flat:
        saved |= _write_cache_file(_BROWSER_DISK_CACHE_STOCK_PATH, stock_flat, stock_by_cat, ts)
    if user_flat:
        saved |= _write_cache_file(_BROWSER_DISK_CACHE_USER_PATH, user_flat, user_by_cat, ts)
    return saved


def _get_user_cache_age() -> float:
    """Return age in seconds of the user cache file, or infinity if missing."""
    try:
        if os.path.exists(_BROWSER_DISK_CACHE_USER_PATH):
            with open(_BROWSER_DISK_CACHE_USER_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return time.time() - data.get("timestamp", 0.0)
    except Exception:
        pass
    return float("inf")


def _load_browser_cache_from_disk() -> bool:
    """Load browser cache from disk using two-tier approach.

    Stock tier: loaded from browser_cache_stock.json (or bundled seed), never expires.
    User tier: loaded from browser_cache_user.json, age-checked (24hr max).

    Migrates old single browser_cache.json on first run.
    Returns True if any cache was loaded.
    """
    global _browser_cache_flat, _browser_cache_by_category, _browser_cache_timestamp, _device_uri_map

    # Migrate old single-file cache to two-tier
    _migrate_old_cache()

    flat_items: List[Dict[str, Any]] = []
    by_category: Dict[str, List[Dict[str, Any]]] = {}
    stock_loaded = False
    user_loaded = False
    user_timestamp = 0.0

    # --- Load stock tier (never expires) ---
    seed_path = os.path.join(os.path.dirname(__file__), "browser_cache_seed.json")
    for path, label in [
        (_BROWSER_DISK_CACHE_STOCK_PATH, "stock disk cache"),
        (seed_path, "bundled seed"),
    ]:
        data = _try_load_cache_file(path, label, check_age=False)
        if data:
            flat_items.extend(data["flat"])
            by_category.update(data["by_category"])
            stock_loaded = True
            break

    # --- Load user tier (age-checked) ---
    data = _try_load_cache_file(_BROWSER_DISK_CACHE_USER_PATH, "user disk cache", check_age=True)
    if data:
        flat_items.extend(data["flat"])
        by_category.update(data["by_category"])
        user_loaded = True
        user_timestamp = data["timestamp"]

    if not flat_items:
        logger.info("No browser cache available (first run)")
        return False

    # Build device URI map from merged data
    device_map = _build_device_uri_map(flat_items)

    # Use user timestamp if available, otherwise use current time (stock-only = fresh enough)
    effective_timestamp = user_timestamp if user_loaded else time.time()

    with _browser_cache_lock:
        _browser_cache_flat = flat_items
        _browser_cache_by_category = by_category
        _device_uri_map = device_map
        _browser_cache_timestamp = effective_timestamp

    logger.info("Loaded cache: %d items (%s stock, %s user), %d device URIs",
                len(flat_items),
                "with" if stock_loaded else "no",
                "with" if user_loaded else "no",
                len(device_map))
    return True


def _populate_browser_cache(force: bool = False, categories=None) -> bool:
    """Scan Ableton's browser tree and cache items for instant search.

    By default scans all categories.  Pass categories=_BROWSER_CATEGORIES_USER
    to only scan Plug-ins + User Library (the normal warmup path).

    Uses a breadth-first walk up to depth 3 per category.
    Each command is rate-limited (50ms gap) to avoid overwhelming Ableton's
    socket handler.  Items are capped at 1500 per category.

    Uses a **dedicated TCP connection** to avoid corrupting the shared global
    connection when the BFS scan sends many rapid commands.

    Scanned results are **merged** with existing cache — unscanned categories
    are preserved.  This protects stock content when only user categories are
    rescanned.
    """
    global _browser_cache_flat, _browser_cache_by_category, _browser_cache_timestamp, _device_uri_map, _browser_cache_populating

    if categories is None:
        categories = _BROWSER_CATEGORIES

    now = time.time()
    with _browser_cache_lock:
        if not force and _browser_cache_flat and (now - _browser_cache_timestamp) < _BROWSER_CACHE_TTL:
            return True  # cache is still fresh
        if _browser_cache_populating:
            return True  # another thread is already scanning
        _browser_cache_populating = True

    # Use a dedicated connection so rapid BFS commands don't corrupt the
    # shared global socket (which other tools need concurrently).
    ableton = AbletonConnection(host="localhost", port=9877)

    try:
        try:
            if not ableton.connect():
                logger.warning("Browser cache: cannot connect to Ableton")
                return False
        except Exception as e:
            logger.warning("Browser cache: cannot connect to Ableton: %s", e)
            return False

        cat_names = [dn for _, dn in categories]
        logger.info("Browser cache: starting scan (%s)...", ", ".join(cat_names))
        flat_items: List[Dict[str, Any]] = []
        by_display: Dict[str, List[Dict[str, Any]]] = {}
        total = 0

        for path_root, display_name in categories:
            category_items: List[Dict[str, Any]] = []
            cat_count = 0

            # BFS queue: (browser_path, depth)
            queue = deque([(path_root, 0)])

            while queue and cat_count < _BROWSER_CACHE_MAX_ITEMS:
                current_path, depth = queue.popleft()

                try:
                    result = ableton.send_command("get_browser_items_at_path", {"path": current_path}, timeout=60.0)
                except Exception as e:
                    logger.warning("Browser cache: failed to read '%s': %s", current_path, e)
                    # Try to re-establish connection with escalating delays
                    reconnected = False
                    for retry in range(3):
                        wait = 5 * (retry + 1)  # 5s, 10s, 15s
                        logger.info("Browser cache: reconnect attempt %d, waiting %ds...", retry + 1, wait)
                        time.sleep(wait)
                        try:
                            ableton.disconnect()
                            if ableton.connect():
                                reconnected = True
                                logger.info("Browser cache: reconnected on attempt %d", retry + 1)
                                break
                        except Exception:
                            pass
                    if not reconnected:
                        logger.warning("Browser cache: lost connection, skipping '%s'", display_name)
                        break
                    continue

                if "error" in result:
                    continue

                for item in result.get("items", []):
                    if cat_count >= _BROWSER_CACHE_MAX_ITEMS:
                        break

                    name = item.get("name", "")
                    if not name:
                        continue

                    item_path = f"{current_path}/{name}"
                    entry = {
                        "name": name,
                        "search_name": name.lower(),
                        "uri": item.get("uri", ""),
                        "is_loadable": item.get("is_loadable", False),
                        "is_folder": item.get("is_folder", False),
                        "is_device": item.get("is_device", False),
                        "category": display_name,
                        "path": item_path,
                    }
                    category_items.append(entry)
                    flat_items.append(entry)
                    cat_count += 1
                    total += 1

                    # Enqueue folders for deeper scanning
                    if item.get("is_folder", False) and depth < _BROWSER_CACHE_MAX_DEPTH:
                        queue.append((item_path, depth + 1))

                # Rate-limit to avoid overwhelming Ableton's socket handler
                time.sleep(0.05)

            by_display[display_name] = category_items
            logger.info("Browser cache: '%s' — %d items", display_name, len(category_items))

        # Merge scanned items with existing cache (don't destroy unscanned categories)
        scanned_cat_names = {dn for _, dn in categories}

        with _browser_cache_lock:
            existing_flat = list(_browser_cache_flat)
            existing_by_cat = dict(_browser_cache_by_category)

        # Keep items from unscanned categories, replace scanned ones
        merged_flat = [i for i in existing_flat if i.get("category") not in scanned_cat_names]
        merged_flat.extend(flat_items)

        merged_by_cat = {k: v for k, v in existing_by_cat.items() if k not in scanned_cat_names}
        merged_by_cat.update(by_display)

        device_map = _build_device_uri_map(merged_flat)

        with _browser_cache_lock:
            _browser_cache_flat = merged_flat
            _browser_cache_by_category = merged_by_cat
            _device_uri_map = device_map
            _browser_cache_timestamp = time.time()

        logger.info("Browser cache: scanned %d items (%s), total %d items, %d device names mapped",
                     total, ", ".join(cat_names), len(merged_flat), len(device_map))
        _save_browser_cache_to_disk()
        return True

    finally:
        with _browser_cache_lock:
            _browser_cache_populating = False
        # Always close the dedicated connection when done
        try:
            ableton.disconnect()
        except Exception:
            pass


def _get_browser_cache() -> List[Dict[str, Any]]:
    """Get the flat browser cache, populating if needed."""
    with _browser_cache_lock:
        if _browser_cache_flat and (time.time() - _browser_cache_timestamp) < _BROWSER_CACHE_TTL:
            return _browser_cache_flat
    _populate_browser_cache()
    return _browser_cache_flat


# ---------------------------------------------------------------------------
# Tool call instrumentation — captures all 131 tool calls for the dashboard
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
        return _pkg_version("ableton-mcp-stable")
    except Exception:
        return "1.9.0"


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
        total = sum(_tool_call_counts.values())
        top_tools = sorted(_tool_call_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    with _server_log_lock:
        # Format timestamps from stored tuples (created_float, level, msg)
        server_logs = [
            {"ts": datetime.fromtimestamp(ts).strftime("%H:%M:%S"), "level": lvl, "msg": msg}
            for ts, lvl, msg in _server_log_buffer
        ]

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
        "tool_count": 131,
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
    logger.info("Dashboard started at http://127.0.0.1:%d", DASHBOARD_PORT)


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
            logger.warning("Existing connection is no longer valid: %s", e)
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
                logger.info("Connecting to Ableton (attempt %d/%d)...", attempt, max_attempts)
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
                        logger.error("Connection validation failed: %s", e)
                        _ableton_connection.disconnect()
                        _ableton_connection = None
                        # Continue to next attempt
                else:
                    _ableton_connection = None
            except Exception as e:
                logger.error("Connection attempt %d failed: %s", attempt, e)
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


def _m4l_batch_set_params(
    m4l: M4LConnection,
    track_index: int,
    device_index: int,
    parameters: List[Dict],
) -> Dict[str, Any]:
    """Set multiple hidden parameters by sending individual set_hidden_param
    commands sequentially.  More reliable than the base64-encoded batch OSC
    approach which can fail with longer payloads in Max.

    Returns a dict with keys: params_set, params_failed, total_requested, errors.
    """
    ok = 0
    failed = 0
    errors: List[str] = []
    for p in parameters:
        try:
            result = m4l.send_command("set_hidden_param", {
                "track_index": track_index,
                "device_index": device_index,
                "parameter_index": int(p["index"]),
                "value": float(p["value"]),
            })
            if result.get("status") == "success":
                ok += 1
            else:
                failed += 1
                errors.append(f"[{p['index']}]: {result.get('message', '?')}")
        except Exception as e:
            failed += 1
            errors.append(f"[{p['index']}]: {str(e)}")
        # Small delay to let Ableton breathe when setting many params
        if len(parameters) > 6:
            time.sleep(0.05)
    return {
        "params_set": ok,
        "params_failed": failed,
        "total_requested": ok + failed,
        "errors": errors,
    }


def _tcp_batch_restore_params(
    ableton: AbletonConnection,
    track_index: int,
    device_index: int,
    params: List[Dict],
) -> Dict[str, Any]:
    """Restore device parameters via TCP using name-based set_device_parameters_batch.

    params: list of dicts with 'name' and 'value' keys.
    Returns a dict with keys: params_set, params_failed.
    """
    param_list = [{"name": p["name"], "value": p["value"]} for p in params if p.get("name")]
    if not param_list:
        return {"params_set": 0, "params_failed": 0}
    try:
        result = ableton.send_command("set_device_parameters_batch", {
            "track_index": track_index,
            "device_index": device_index,
            "parameters": param_list,
        })
        set_count = len(result.get("results", []))
        return {"params_set": set_count, "params_failed": len(param_list) - set_count}
    except Exception as e:
        logger.error(f"TCP batch restore failed: {e}")
        return {"params_set": 0, "params_failed": len(param_list)}


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
    Load an instrument or effect onto a track using its URI or device name.

    Parameters:
    - track_index: The index of the track to load the instrument on
    - uri: The URI of the instrument/effect, OR a device name (resolved automatically).

    You can pass any Ableton instrument, audio effect, or MIDI effect name
    directly — no need to call search_browser first.  The server resolves the
    name to the correct URI using the browser cache.

    Common examples:
      Instruments: Analog, Drift, Operator, Sampler, Simpler, Wavetable
      Audio Effects: Reverb, Compressor, EQ Eight, Delay, Auto Filter, Limiter
      MIDI Effects: Arpeggiator, Chord, Scale, Velocity

    Examples:
      load_instrument_or_effect(track_index=0, uri="Analog")
      load_instrument_or_effect(track_index=2, uri="Reverb")
      load_instrument_or_effect(track_index=1, uri="Compressor")

    For presets or third-party items, use search_browser() to find the full URI.
    """
    try:
        _validate_index(track_index, "track_index")
        uri = _resolve_device_uri(uri)
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

    Uses cached browser data when available for richer results with URIs.

    Parameters:
    - category_type: Type of categories to get ('all', 'instruments', 'sounds', 'drums', 'audio_effects', 'midi_effects')
    """
    try:
        # Try to serve from cache first (richer data with URIs)
        cache = _get_browser_cache()
        if cache:
            # Filter categories
            if category_type == "all":
                show_categories = list(_CATEGORY_DISPLAY.values())
            else:
                show_categories = [_CATEGORY_DISPLAY.get(category_type, category_type)]

            formatted_output = f"Browser tree for '{category_type}':\n\n"
            for cat_display in show_categories:
                # Use category index for O(1) lookup instead of scanning all items
                cat_items = _browser_cache_by_category.get(cat_display, [])
                # Top-level items have paths like "sounds/Operator" (2 segments)
                top_items = [
                    item for item in cat_items
                    if item.get("path", "").count("/") == 1
                ]
                if not top_items:
                    continue

                formatted_output += f"**{cat_display}** ({len(top_items)} items):\n"
                for item in sorted(top_items, key=lambda x: x.get("name", "")):
                    loadable = " [loadable]" if item.get("is_loadable", False) else ""
                    folder = " [+]" if item.get("is_folder", False) else ""
                    formatted_output += f"  • {item['name']}{loadable}{folder}"
                    if item.get("uri"):
                        formatted_output += f"  (URI: {item['uri']})"
                    formatted_output += "\n"
                formatted_output += "\n"

            return formatted_output

        # Fallback: fetch from Ableton directly
        ableton = get_ableton_connection()
        result = ableton.send_command("get_browser_tree", {
            "category_type": category_type
        })

        if "available_categories" in result and len(result.get("categories", [])) == 0:
            available_cats = result.get("available_categories", [])
            return (f"No categories found for '{category_type}'. "
                   f"Available browser categories: {', '.join(available_cats)}")

        total_folders = result.get("total_folders", 0)
        formatted_output = f"Browser tree for '{category_type}' (showing {total_folders} folders):\n\n"

        def format_tree(item, indent=0):
            output = ""
            if item:
                prefix = "  " * indent
                name = item.get("name", "Unknown")
                path = item.get("path", "")
                has_more = item.get("has_more", False)
                output += f"{prefix}• {name}"
                if path:
                    output += f" (path: {path})"
                if has_more:
                    output += " [...]"
                output += "\n"
                for child in item.get("children", []):
                    output += format_tree(child, indent + 1)
            return output

        for category in result.get("categories", []):
            formatted_output += format_tree(category)
            formatted_output += "\n"

        return formatted_output
    except Exception as e:
        error_msg = str(e)
        if "Browser is not available" in error_msg:
            return "Error: The Ableton browser is not available. Make sure Ableton Live is fully loaded and try again."
        elif "Could not access Live application" in error_msg:
            return "Error: Could not access the Ableton Live application. Make sure Ableton Live is running and the Remote Script is loaded."
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
def get_device_parameters(ctx: Context, track_index: int, device_index: int,
                           track_type: str = "track") -> str:
    """
    Get all parameters and their current values for a device on a track.

    Parameters:
    - track_index: The index of the track containing the device
    - device_index: The index of the device on the track
    - track_type: Type of track: "track" (default), "return", or "master"
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(device_index, "device_index")
        if track_type not in ("track", "return", "master"):
            return "Error: track_type must be 'track', 'return', or 'master'"
        ableton = get_ableton_connection()
        result = ableton.send_command("get_device_parameters", {
            "track_index": track_index,
            "device_index": device_index,
            "track_type": track_type,
        })
        return json.dumps(result, indent=2)
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error getting device parameters: {str(e)}")
        return "Error getting device parameters. Please check the server logs for details."

@mcp.tool()
def set_device_parameter(ctx: Context, track_index: int, device_index: int,
                          parameter_name: str, value: str,
                          track_type: str = "track") -> str:
    """
    Set a device parameter value.

    IMPORTANT: Always call get_device_parameters first to see exact parameter
    names and their current display_value before setting. Do NOT guess parameter names.

    Parameters:
    - track_index: The index of the track containing the device
    - device_index: The index of the device on the track
    - parameter_name: The name of the parameter to set
    - value: The value to set. Can be a number (e.g. 0.5) or a display string
      (e.g. "1/4", "Synced", "Bandpass", "1/2"). Display strings work for ALL
      parameters — both quantized (value_items) and non-quantized (like LFO Rate
      where 0-21 maps to note values "8", "4", "2", "1", "1/2", "1/4", "1/8" etc).
      ALWAYS prefer display strings over raw numbers when you know the desired
      display value — the server resolves them automatically.
    - track_type: Type of track: "track" (default), "return", or "master"
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(device_index, "device_index")
        if track_type not in ("track", "return", "master"):
            return "Error: track_type must be 'track', 'return', or 'master'"
        ableton = get_ableton_connection()

        # Detect if value is numeric or a display string
        cmd_params = {
            "track_index": track_index,
            "device_index": device_index,
            "parameter_name": parameter_name,
            "track_type": track_type,
        }
        try:
            cmd_params["value"] = float(value)
        except (ValueError, TypeError):
            # Display string like "1/4", "Sync", "Bandpass"
            cmd_params["value"] = 0.0
            cmd_params["value_display"] = str(value)

        result = ableton.send_command("set_device_parameter", cmd_params)
        pname = result.get('parameter', parameter_name)
        display = result.get("display_value")
        display_str = f" ({display})" if display else ""
        if result.get("clamped", False):
            return f"Set parameter '{pname}' to {result.get('value')}{display_str} (value was clamped to valid range)"
        return f"Set parameter '{pname}' to {result.get('value')}{display_str}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error setting device parameter: {str(e)}")
        return "Error setting device parameter. Please check the server logs for details."

@mcp.tool()
def set_device_parameters(ctx: Context, track_index: int, device_index: int,
                           parameters: str, track_type: str = "track") -> str:
    """
    Set multiple device parameters in a single call (much faster than setting one at a time).

    IMPORTANT: Always call get_device_parameters first to see exact parameter
    names and their current display_value before setting. Do NOT guess parameter names.
    ALWAYS prefer this over calling set_device_parameter multiple times.

    Parameters:
    - track_index: The index of the track containing the device
    - device_index: The index of the device on the track
    - parameters: JSON string of parameter list. Each entry needs "name" and either
      "value" (numeric) or "value_display" (display string). Display strings work for
      ALL parameters — quantized and non-quantized. ALWAYS prefer value_display over
      raw numbers when you know the desired display value.
      e.g. '[{"name": "Filter Freq", "value": 0.5}, {"name": "LFO Rate", "value_display": "1/4"},
             {"name": "LFO T Mode", "value_display": "Synced"}]'
    - track_type: Type of track: "track" (default), "return", or "master"
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(device_index, "device_index")
        if track_type not in ("track", "return", "master"):
            return "Error: track_type must be 'track', 'return', or 'master'"

        params_list = json.loads(parameters) if isinstance(parameters, str) else parameters
        if not isinstance(params_list, list) or not params_list:
            return "Error: parameters must be a non-empty JSON array of {name, value} objects"

        ableton = get_ableton_connection()

        # --- Try batch command first (single round-trip) ---
        try:
            result = ableton.send_command("set_device_parameters_batch", {
                "track_index": track_index,
                "device_index": device_index,
                "parameters": params_list,
                "track_type": track_type,
            })
            device_name = result.get("device_name", "?")
            results = result.get("results", [])
            ok = [r for r in results if "error" not in r]
            errs = [r for r in results if "error" in r]
            summary = f"Set {len(ok)} parameters on '{device_name}'"
            if errs:
                summary += f" ({len(errs)} not found: {', '.join(r['name'] for r in errs)})"
            details = []
            for r in ok:
                display = r.get("display_value")
                if display:
                    details.append(f"  {r['name']}: {r['value']} ({display})")
                else:
                    details.append(f"  {r['name']}: {r['value']}")
            if details:
                summary += "\n" + "\n".join(details)
            return summary
        except Exception as batch_err:
            logger.warning("Batch set_device_parameters failed, falling back to sequential: %s", batch_err)

        # --- Fallback: set parameters one at a time ---
        ok_count = 0
        err_names = []
        details = []
        device_name = "?"
        for entry in params_list:
            pname = entry.get("name", "")
            pvalue = entry.get("value", 0.0)
            value_display = entry.get("value_display")
            cmd_params = {
                "track_index": track_index,
                "device_index": device_index,
                "parameter_name": pname,
                "value": pvalue,
                "track_type": track_type,
            }
            if value_display is not None:
                cmd_params["value_display"] = value_display
            try:
                result = ableton.send_command("set_device_parameter", cmd_params)
                device_name = result.get("device_name", device_name)
                display = result.get("display_value")
                if display:
                    details.append(f"  {pname}: {result.get('value')} ({display})")
                else:
                    details.append(f"  {pname}: {result.get('value')}")
                ok_count += 1
            except Exception:
                err_names.append(pname)

        summary = f"Set {ok_count} parameters on '{device_name}'"
        if err_names:
            summary += f" ({len(err_names)} failed: {', '.join(err_names)})"
        if details:
            summary += "\n" + "\n".join(details)
        return summary

    except json.JSONDecodeError:
        return "Error: parameters must be a valid JSON array"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error("Error in set_device_parameters: %s", e)
        return f"Error setting device parameters: {e}"


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

    Supports mixer parameters (Volume, Pan, Sends) and all device parameters
    on both MIDI and audio clips. For arrangement-level automation, use create_track_automation.
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
        pts = result.get("points_added", len(automation_points))
        return f"Created automation with {pts} points for parameter '{parameter_name}'"
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
        ableton.send_command("set_song_time", {"time": time})
        return f"Playhead set to beat {time}"
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
        # Use the values we sent, with result as fallback
        state = "enabled" if (enabled if enabled is not None else result.get("loop_enabled")) else "disabled"
        s = start if start is not None else result.get('loop_start', 0)
        l = length if length is not None else result.get('loop_length', 0)
        return f"Loop {state}: start={s}, length={l} beats"
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

    Uses a cached browser index for instant results. The cache is built
    automatically on first use and refreshed every 5 minutes.

    Parameters:
    - query: Search string to find items (searches by name)
    - category: Limit search to category ('all', 'instruments', 'sounds', 'drums', 'audio_effects', 'midi_effects', 'max_for_live', 'plugins', 'clips', 'samples', 'packs', 'user_library')
    """
    try:
        cache = _get_browser_cache()
        if not cache:
            return "Browser cache is empty. Make sure Ableton is running and try again."

        query_lower = query.lower()

        # Use category index for filtered search (smaller list to scan)
        filter_display = _CATEGORY_DISPLAY.get(category) if category != "all" else None
        search_list = _browser_cache_by_category.get(filter_display, cache) if filter_display else cache

        results = []
        for item in search_list:
            # Substring match using pre-lowercased search_name
            if query_lower in item.get("search_name", item.get("name", "").lower()):
                results.append(item)

        if not results:
            return f"No results found for '{query}' in category '{category}'"

        # Sort: loadable items first, then by name
        results.sort(key=lambda x: (not x.get("is_loadable", False), x.get("name", "").lower()))

        # Limit to 50 results
        results = results[:50]

        formatted_output = f"Found {len(results)} results for '{query}':\n\n"
        for item in results:
            loadable = " [loadable]" if item.get("is_loadable", False) else ""
            folder = " [folder]" if item.get("is_folder", False) else ""
            formatted_output += f"• {item.get('name', 'Unknown')}{loadable}{folder}\n"
            formatted_output += f"  Category: {item.get('category', '?')} | Path: {item.get('path', '?')}\n"
            if item.get("uri"):
                formatted_output += f"  URI: {item.get('uri')}\n"

        return formatted_output
    except Exception as e:
        logger.error(f"Error searching browser: {str(e)}")
        return "Error searching browser. Please check the server logs for details."

@mcp.tool()
def refresh_browser_cache(ctx: Context, full: bool = False) -> str:
    """
    Refresh the browser cache.

    By default only rescans user content (Plug-ins, User Library) — this is
    fast and safe.  Set full=True to also rescan all stock Ableton library
    categories (only needed after installing new Ableton Packs).
    """
    try:
        categories = _BROWSER_CATEGORIES if full else _BROWSER_CATEGORIES_USER
        success = _populate_browser_cache(force=True, categories=categories)
        if success:
            with _browser_cache_lock:
                count = len(_browser_cache_flat)
                cats = len(_browser_cache_by_category)
                devices = len(_device_uri_map)
            scope = "full" if full else "user content"
            return f"Browser cache refreshed ({scope}): {count} items across {cats} categories, {devices} device names mapped (saved to disk)"
        return "Failed to refresh browser cache. Make sure Ableton is running."
    except Exception as e:
        logger.error("Error refreshing browser cache: %s", e)
        return "Error refreshing browser cache. Please check the server logs for details."


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

        # Filter out parameter index 0 ("Device On") to prevent accidentally
        # disabling the device — a common source of issues.
        safe_params = [p for p in parameters if int(p.get("index", 0)) != 0]
        skipped = len(parameters) - len(safe_params)

        for i, p in enumerate(safe_params):
            if not isinstance(p, dict):
                raise ValueError(f"Parameter at index {i} must be a dictionary.")
            if "index" not in p or "value" not in p:
                raise ValueError(f"Parameter at index {i} must have 'index' and 'value' keys.")

        if len(safe_params) == 0:
            return "No settable parameters after filtering (parameter 0 'Device On' is excluded)."

        # Send individual set_hidden_param commands with a small delay between
        # each to avoid overwhelming Ableton.  This is more reliable than the
        # base64-encoded batch OSC approach which can fail with long payloads.
        m4l = get_m4l_connection()
        ok_count = 0
        fail_count = 0
        errors = []

        for p in safe_params:
            try:
                result = m4l.send_command("set_hidden_param", {
                    "track_index": track_index,
                    "device_index": device_index,
                    "parameter_index": int(p["index"]),
                    "value": float(p["value"])
                })
                if result.get("status") == "success":
                    ok_count += 1
                else:
                    fail_count += 1
                    errors.append(f"[{p['index']}]: {result.get('message', '?')}")
            except Exception as e:
                fail_count += 1
                errors.append(f"[{p['index']}]: {str(e)}")

            # Small delay between params to let Ableton breathe
            if len(safe_params) > 6:
                time.sleep(0.05)

        total = ok_count + fail_count
        msg = f"Batch set complete: {ok_count}/{total} parameters set successfully ({fail_count} failed)."
        if skipped:
            msg += f" ({skipped} skipped: 'Device On' excluded for safety.)"
        if errors:
            msg += f" Errors: {'; '.join(errors[:5])}"
        return msg
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
    """Capture the complete state of a device (all parameters).

    Stores the snapshot in memory with a unique ID for later recall.
    Use restore_device_snapshot() to restore a saved state.
    Use list_snapshots() to see all stored snapshots.

    Parameters:
    - track_index: The index of the track containing the device
    - device_index: The index of the device on the track
    - snapshot_name: Optional human-readable name for the snapshot
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(device_index, "device_index")

        ableton = get_ableton_connection()
        result = ableton.send_command("get_device_parameters", {
            "track_index": track_index,
            "device_index": device_index,
        })

        snapshot_id = str(uuid.uuid4())[:8]
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        params = result.get("parameters", [])

        snapshot = {
            "id": snapshot_id,
            "name": snapshot_name or f"{result.get('device_name', 'Unknown')}_{snapshot_id}",
            "timestamp": timestamp,
            "track_index": track_index,
            "device_index": device_index,
            "device_name": result.get("device_name", "Unknown"),
            "device_class": result.get("device_type", "Unknown"),
            "parameter_count": len(params),
            "parameters": params
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

    """
    try:
        if snapshot_id not in _snapshot_store:
            return f"Snapshot '{snapshot_id}' not found. Use list_snapshots() to see available snapshots."

        snapshot = _snapshot_store[snapshot_id]
        target_track = track_index if track_index >= 0 else snapshot["track_index"]
        target_device = device_index if device_index >= 0 else snapshot["device_index"]

        params_to_set = [p for p in snapshot["parameters"] if p.get("name")]

        if not params_to_set:
            return "Snapshot contains no parameters to restore."

        ableton = get_ableton_connection()
        data = _tcp_batch_restore_params(ableton, target_track, target_device, params_to_set)
        ok = data["params_set"]
        failed = data["params_failed"]
        return (
            f"Restored snapshot '{snapshot['name']}' (ID: {snapshot_id})\n"
            f"Target: track {target_track}, device {target_device}\n"
            f"Parameters restored: {ok}/{len(params_to_set)} ({failed} failed)"
        )
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
    """
    try:
        if not isinstance(track_indices, list) or len(track_indices) == 0:
            raise ValueError("track_indices must be a non-empty list of integers.")
        for ti in track_indices:
            _validate_index(ti, "track_index")

        ableton = get_ableton_connection()
        group_id = str(uuid.uuid4())[:8]
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        snapshot_ids = []
        device_count = 0

        for ti in track_indices:
            track_info = ableton.send_command("get_track_info", {"track_index": ti})
            devices = track_info.get("devices", [])

            for di, dev in enumerate(devices):
                try:
                    result = ableton.send_command("get_device_parameters", {
                        "track_index": ti,
                        "device_index": di,
                    })
                except Exception:
                    continue

                params = result.get("parameters", [])
                snap_id = str(uuid.uuid4())[:8]

                _snapshot_store[snap_id] = {
                    "id": snap_id,
                    "group_id": group_id,
                    "name": f"{result.get('device_name', 'Unknown')}_t{ti}_d{di}",
                    "timestamp": timestamp,
                    "track_index": ti,
                    "device_index": di,
                    "device_name": result.get("device_name", "Unknown"),
                    "device_class": result.get("device_type", "Unknown"),
                    "parameter_count": len(params),
                    "parameters": params
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
    except Exception as e:
        logger.error(f"Error snapshotting all devices: {str(e)}")
        return f"Error capturing group snapshot: {str(e)}"


@mcp.tool()
def restore_group_snapshot(ctx: Context, group_id: str) -> str:
    """Restore all device states from a group snapshot.

    Restores every device captured in a snapshot_all_devices() call.

    Parameters:
    - group_id: The group snapshot ID (starts with 'group_')

    """
    try:
        if group_id not in _snapshot_store:
            return f"Group snapshot '{group_id}' not found."

        group = _snapshot_store[group_id]
        if group.get("type") != "group":
            return f"'{group_id}' is not a group snapshot. Use restore_device_snapshot() instead."

        ableton = get_ableton_connection()
        total_devices = 0
        total_params = 0
        total_failed = 0

        for snap_id in group.get("snapshot_ids", []):
            if snap_id not in _snapshot_store:
                continue

            snap = _snapshot_store[snap_id]
            params_to_set = [p for p in snap.get("parameters", []) if p.get("name")]

            if not params_to_set:
                continue

            data = _tcp_batch_restore_params(ableton, snap["track_index"], snap["device_index"], params_to_set)
            total_params += data["params_set"]
            total_failed += data["params_failed"]
            total_devices += 1

        return (
            f"Restored group snapshot '{group['name']}'\n"
            f"Devices restored: {total_devices}\n"
            f"Parameters restored: {total_params} ({total_failed} failed)"
        )
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

        b_by_name = {p["name"]: p for p in snap_b.get("parameters", []) if p.get("name")}

        params_to_set = []
        skipped = 0
        for p_a in snap_a.get("parameters", []):
            name = p_a.get("name")
            if not name or name not in b_by_name:
                skipped += 1
                continue

            p_b = b_by_name[name]
            val_a = p_a["value"]
            val_b = p_b["value"]

            if p_a.get("is_quantized", False):
                interpolated = val_a if position < 0.5 else val_b
            else:
                interpolated = val_a + (val_b - val_a) * position

            params_to_set.append({"name": name, "value": interpolated})

        if not params_to_set:
            return "No matching parameters found between the two snapshots."

        ableton = get_ableton_connection()
        data = _tcp_batch_restore_params(ableton, target_track, target_device, params_to_set)
        ok = data["params_set"]
        return (
                f"Morph at position {position:.2f} "
                f"('{snap_a.get('name', snapshot_a_id)}' -> '{snap_b.get('name', snapshot_b_id)}')\n"
                f"Interpolated {ok} parameters, skipped {skipped} (unmatched)\n"
                f"Target: track {target_track}, device {target_device}"
            )
    except ValueError as e:
        return f"Invalid input: {e}"
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
        - parameter_index: int (LOM index from get_device_parameters)
        - parameter_name: str (optional, name of the parameter for TCP-based control)
        - min_value: float (parameter value when macro = 0.0)
        - max_value: float (parameter value when macro = 1.0)

    After creation, use set_macro_value() to control all linked parameters at once.
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
    """
    try:
        if macro_id not in _macro_store:
            return f"Macro '{macro_id}' not found. Use list_macros() to see available macros."
        _validate_range(value, "value", 0.0, 1.0)

        macro = _macro_store[macro_id]
        macro["current_value"] = value

        ableton = get_ableton_connection()

        # Group mappings by (track, device) for batch operations
        grouped: Dict[tuple, list] = {}
        for m in macro["mappings"]:
            key = (m["track_index"], m["device_index"])
            interpolated = m["min_value"] + (m["max_value"] - m["min_value"]) * value

            # Use parameter_name if available, fall back to index-based lookup
            param_name = m.get("parameter_name")
            if not param_name:
                # Look up the parameter name from the device
                try:
                    dev_result = ableton.send_command("get_device_parameters", {
                        "track_index": m["track_index"],
                        "device_index": m["device_index"],
                    })
                    for p in dev_result.get("parameters", []):
                        if p.get("index") == m["parameter_index"]:
                            param_name = p["name"]
                            m["parameter_name"] = param_name  # Cache for future calls
                            break
                except Exception:
                    pass

            if not param_name:
                continue

            if key not in grouped:
                grouped[key] = []
            grouped[key].append({"name": param_name, "value": interpolated})

        total_set = 0
        total_failed = 0

        for (ti, di), params in grouped.items():
            data = _tcp_batch_restore_params(ableton, ti, di, params)
            total_set += data["params_set"]
            total_failed += data["params_failed"]

        return (
            f"Macro '{macro['name']}' set to {value:.2f}\n"
            f"Updated {total_set} parameters across {len(grouped)} device(s) "
            f"({total_failed} failed)"
        )
    except ValueError as e:
        return f"Invalid input: {e}"
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
# v2.0.0 — Phase 2: Device Chain Navigation (Racks / Drum Racks)
# ==========================================================================

@mcp.tool()
def discover_rack_chains(
    ctx: Context,
    track_index: int,
    device_index: int,
    chain_path: str = None
) -> str:
    """Discover chains and nested devices inside a Rack or Drum Rack.

    For Instrument/Audio Effect Racks: lists all chains and the devices in each chain.
    For Drum Racks: lists populated drum pads with their note numbers and devices.

    Use this to navigate into nested devices that are not visible at the top level.
    Then use get_chain_device_parameters() to read params on nested devices.

    Parameters:
    - track_index: The track containing the rack device
    - device_index: The index of the rack device on the track
    - chain_path: Optional LOM sub-path to target a nested rack device.
      Examples: "chains 0 devices 0" to target the first device in chain 0,
      "drum_pads 0 chains 0 devices 0" to target a device inside a drum pad.
      The path is appended to "live_set tracks T devices D".

    Requires the AbletonMCP M4L bridge device.
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(device_index, "device_index")

        m4l = get_m4l_connection()
        result = m4l.send_command("discover_chains", {
            "track_index": track_index,
            "device_index": device_index,
            "chain_path": chain_path or "",
        })

        data = result.get("result", result)

        if data.get("error"):
            return f"Error: {data['error']}"

        if not data.get("can_have_chains"):
            return (
                f"Device '{data.get('device_name', '?')}' ({data.get('device_class', '?')}) "
                f"does not support chains."
            )

        output = (
            f"Rack: {data.get('device_name', '?')} ({data.get('device_class', '?')})\n"
            f"Chain count: {data.get('chain_count', 0)}\n"
        )

        # Chains
        for chain in data.get("chains", []):
            output += f"\n  Chain [{chain['index']}] '{chain.get('name', '')}' — {chain.get('device_count', 0)} device(s):\n"
            for dev in chain.get("devices", []):
                nested = " [RACK]" if dev.get("can_have_chains") else ""
                output += f"    [{dev['index']}] {dev.get('name', '?')} ({dev.get('class_name', '?')}){nested}\n"

        # Drum pads
        if data.get("has_drum_pads") and data.get("drum_pads"):
            output += f"\nDrum Pads (populated): {data.get('populated_pad_count', 0)}\n"
            for pad in data.get("drum_pads", []):
                note = pad.get("note", "?")
                name = pad.get("name", "?")
                muted = " [MUTED]" if pad.get("mute") else ""
                output += f"  Pad [{pad['index']}] note={note} '{name}'{muted} — {pad.get('chain_count', 0)} chain(s)\n"
                for dev in pad.get("devices", []):
                    output += f"    [{dev['index']}] {dev.get('name', '?')} ({dev.get('class_name', '?')})\n"

        return output
    except ConnectionError as e:
        return f"M4L bridge not available: {e}"
    except Exception as e:
        logger.error(f"Error discovering chains: {str(e)}")
        return f"Error discovering chains: {str(e)}"


@mcp.tool()
def get_chain_device_parameters(
    ctx: Context,
    track_index: int,
    device_index: int,
    chain_index: int,
    chain_device_index: int
) -> str:
    """Get all parameters of a device nested inside a rack chain.

    Use discover_rack_chains() first to find the chain/device indices.

    Parameters:
    - track_index: Track containing the rack
    - device_index: Index of the rack device
    - chain_index: Index of the chain within the rack
    - chain_device_index: Index of the device within the chain

    Requires the AbletonMCP M4L bridge device.
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(device_index, "device_index")

        m4l = get_m4l_connection()
        result = m4l.send_command("get_chain_device_params", {
            "track_index": track_index,
            "device_index": device_index,
            "chain_index": chain_index,
            "chain_device_index": chain_device_index,
        })

        data = result.get("result", result)

        if data.get("error"):
            return f"Error: {data['error']}"

        output = (
            f"Device: {data.get('device_name', '?')} ({data.get('device_class', '?')})\n"
            f"Location: track {track_index} → device {device_index} → chain {chain_index} → device {chain_device_index}\n"
            f"Parameters ({data.get('parameter_count', 0)}):\n\n"
        )

        for p in data.get("parameters", []):
            quant = " [quantized]" if p.get("is_quantized") else ""
            items = ""
            if p.get("value_items"):
                items = f" items=[{p['value_items']}]"
            output += (
                f"  [{p['index']}] {p.get('name', '?')}: "
                f"{p.get('value', '?')} (range: {p.get('min', '?')}–{p.get('max', '?')}){quant}{items}\n"
            )

        return output
    except ConnectionError as e:
        return f"M4L bridge not available: {e}"
    except Exception as e:
        logger.error(f"Error getting chain device params: {str(e)}")
        return f"Error getting chain device parameters: {str(e)}"


@mcp.tool()
def set_chain_device_parameter(
    ctx: Context,
    track_index: int,
    device_index: int,
    chain_index: int,
    chain_device_index: int,
    parameter_index: int,
    value: float
) -> str:
    """Set a parameter on a device nested inside a rack chain.

    Use get_chain_device_parameters() first to see available parameters.

    Parameters:
    - track_index: Track containing the rack
    - device_index: Index of the rack device
    - chain_index: Index of the chain within the rack
    - chain_device_index: Index of the device within the chain
    - parameter_index: LOM index of the parameter
    - value: The value to set

    Requires the AbletonMCP M4L bridge device.
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(device_index, "device_index")

        m4l = get_m4l_connection()
        result = m4l.send_command("set_chain_device_param", {
            "track_index": track_index,
            "device_index": device_index,
            "chain_index": chain_index,
            "chain_device_index": chain_device_index,
            "parameter_index": parameter_index,
            "value": value,
        })

        data = result.get("result", result)

        if data.get("error"):
            return f"Error: {data['error']}"

        clamped = " (clamped)" if data.get("was_clamped") else ""
        return (
            f"Set '{data.get('parameter_name', '?')}' to {data.get('actual_value', '?')}{clamped}\n"
            f"Location: track {track_index} → device {device_index} → chain {chain_index} → device {chain_device_index} → param [{parameter_index}]"
        )
    except ConnectionError as e:
        return f"M4L bridge not available: {e}"
    except Exception as e:
        logger.error(f"Error setting chain device param: {str(e)}")
        return f"Error setting chain device parameter: {str(e)}"


# ==========================================================================
# v2.0.0 — Phase 3: Simpler / Sample Deep Access
# ==========================================================================

@mcp.tool()
def get_simpler_info(
    ctx: Context,
    track_index: int,
    device_index: int
) -> str:
    """Get detailed information about a Simpler device and its loaded sample.

    Returns Simpler playback mode, sample file info, markers, warp settings,
    slicing data, and warp-mode-specific properties.

    Parameters:
    - track_index: Track containing the Simpler
    - device_index: Index of the Simpler device

    Requires the AbletonMCP M4L bridge device.
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(device_index, "device_index")

        m4l = get_m4l_connection()
        result = m4l.send_command("get_simpler_info", {
            "track_index": track_index,
            "device_index": device_index,
        })

        data = result.get("result", result)

        if data.get("error"):
            return f"Error: {data['error']}"

        playback_modes = {0: "Classic", 1: "One-Shot", 2: "Slicing"}
        mode = playback_modes.get(data.get("playback_mode"), "Unknown")

        output = (
            f"Simpler: {data.get('device_name', '?')}\n"
            f"Playback mode: {mode}\n"
            f"Voices: {data.get('voices', '?')}\n"
        )

        sample = data.get("sample")
        if not sample:
            output += "\nNo sample loaded.\n"
            return output

        output += (
            f"\nSample:\n"
            f"  File: {sample.get('file_path', '?')}\n"
            f"  Length: {sample.get('length', '?')} samples"
        )
        if sample.get("sample_rate"):
            duration = sample.get("length", 0) / sample["sample_rate"]
            output += f" ({duration:.2f}s at {sample['sample_rate']}Hz)"
        output += "\n"

        output += (
            f"  Start marker: {sample.get('start_marker', '?')}\n"
            f"  End marker: {sample.get('end_marker', '?')}\n"
            f"  Gain: {sample.get('gain', '?')}\n"
            f"  Warping: {sample.get('warping', '?')}\n"
            f"  Warp mode: {sample.get('warp_mode_name', '?')}\n"
        )

        if sample.get("slicing_sensitivity") is not None:
            output += f"  Slicing sensitivity: {sample['slicing_sensitivity']}\n"

        if sample.get("slices"):
            output += f"  Slices: {sample['slices']}\n"

        if sample.get("warp_markers"):
            output += f"  Warp markers: {sample['warp_markers']}\n"

        # Mode-specific properties
        mode_props = []
        for key in ["beats_granulation_resolution", "beats_transient_envelope",
                     "beats_transient_loop_mode", "texture_flux", "texture_grain_size",
                     "tones_grain_size", "complex_pro_envelope", "complex_pro_formants"]:
            if sample.get(key) is not None:
                mode_props.append(f"  {key}: {sample[key]}")
        if mode_props:
            output += "\nWarp mode properties:\n" + "\n".join(mode_props) + "\n"

        return output
    except ConnectionError as e:
        return f"M4L bridge not available: {e}"
    except Exception as e:
        logger.error(f"Error getting Simpler info: {str(e)}")
        return f"Error getting Simpler info: {str(e)}"


@mcp.tool()
def set_simpler_sample_properties(
    ctx: Context,
    track_index: int,
    device_index: int,
    start_marker: int = None,
    end_marker: int = None,
    warping: bool = None,
    warp_mode: int = None,
    slicing_sensitivity: float = None,
    gain: float = None
) -> str:
    """Set properties on a Simpler's loaded sample.

    Settable properties include markers, warping, gain, and warp-mode-specific params.
    Warp modes: 0=beats, 1=tones, 2=texture, 3=re_pitch, 4=complex, 5=complex_pro, 6=rex

    Parameters:
    - track_index: Track containing the Simpler
    - device_index: Index of the Simpler device
    - start_marker: Sample start position (in samples)
    - end_marker: Sample end position (in samples)
    - warping: Enable/disable warping
    - warp_mode: Warp mode (0-6)
    - slicing_sensitivity: Sensitivity for auto-slicing (0.0-1.0)
    - gain: Sample gain

    Requires the AbletonMCP M4L bridge device.
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(device_index, "device_index")

        props = {}
        if start_marker is not None:
            props["start_marker"] = start_marker
        if end_marker is not None:
            props["end_marker"] = end_marker
        if warping is not None:
            props["warping"] = 1 if warping else 0
        if warp_mode is not None:
            props["warp_mode"] = warp_mode
        if slicing_sensitivity is not None:
            props["slicing_sensitivity"] = slicing_sensitivity
        if gain is not None:
            props["gain"] = gain

        if not props:
            return "No properties specified to set."

        m4l = get_m4l_connection()
        result = m4l.send_command("set_simpler_sample_props", {
            "track_index": track_index,
            "device_index": device_index,
            "properties": props,
        })

        data = result.get("result", result)

        if data.get("error"):
            return f"Error: {data['error']}"

        output = f"Set {data.get('properties_set', 0)} sample properties."
        if data.get("errors"):
            output += "\nErrors:\n"
            for err in data["errors"]:
                output += f"  {err['property']}: {err['error']}\n"

        return output
    except ConnectionError as e:
        return f"M4L bridge not available: {e}"
    except Exception as e:
        logger.error(f"Error setting Simpler sample props: {str(e)}")
        return f"Error setting Simpler sample properties: {str(e)}"


@mcp.tool()
def simpler_manage_slices(
    ctx: Context,
    track_index: int,
    device_index: int,
    action: str,
    slice_time: float = None
) -> str:
    """Manage slices in a Simpler device (Slicing playback mode).

    Actions:
    - insert: Add a new slice at slice_time (in samples)
    - remove: Remove the slice at slice_time
    - clear: Remove all slices
    - reset: Reset slices to auto-detected positions

    Parameters:
    - track_index: Track containing the Simpler
    - device_index: Index of the Simpler device
    - action: One of "insert", "remove", "clear", "reset"
    - slice_time: Position in samples (required for insert/remove)

    Requires the AbletonMCP M4L bridge device.
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(device_index, "device_index")

        if action not in ("insert", "remove", "clear", "reset"):
            return f"Invalid action '{action}'. Use: insert, remove, clear, reset."

        if action in ("insert", "remove") and slice_time is None:
            return f"Action '{action}' requires slice_time parameter."

        m4l = get_m4l_connection()
        result = m4l.send_command("simpler_slice", {
            "track_index": track_index,
            "device_index": device_index,
            "action": action,
            "slice_time": slice_time,
        })

        data = result.get("result", result)

        if data.get("error"):
            return f"Error: {data['error']}"

        if action == "insert":
            return f"Inserted slice at sample position {slice_time}"
        elif action == "remove":
            return f"Removed slice at sample position {slice_time}"
        elif action == "clear":
            return "Cleared all slices."
        elif action == "reset":
            return "Reset slices to auto-detected positions."

        return f"Slice action '{action}' completed."
    except ConnectionError as e:
        return f"M4L bridge not available: {e}"
    except Exception as e:
        logger.error(f"Error managing Simpler slices: {str(e)}")
        return f"Error managing Simpler slices: {str(e)}"


# ==========================================================================
# v2.0.0 — Phase 4: Wavetable Modulation Matrix
# ==========================================================================

@mcp.tool()
def get_wavetable_info(
    ctx: Context,
    track_index: int,
    device_index: int
) -> str:
    """Get detailed information about a Wavetable synthesizer device.

    Returns oscillator wavetable selections, modulation matrix state,
    filter routing, unison settings, and voice configuration.

    Parameters:
    - track_index: Track containing the Wavetable device
    - device_index: Index of the Wavetable device

    Requires the AbletonMCP M4L bridge device.
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(device_index, "device_index")

        m4l = get_m4l_connection()
        result = m4l.send_command("get_wavetable_info", {
            "track_index": track_index,
            "device_index": device_index,
        })

        data = result.get("result", result)

        if data.get("error"):
            return f"Error: {data['error']}"

        mono_poly_map = {0: "Mono", 1: "Poly"}
        filter_routing_map = {0: "Serial", 1: "Parallel", 2: "Split"}
        unison_mode_map = {0: "None", 1: "Classic", 2: "Shimmer", 3: "Noise", 4: "Phase Sync", 5: "Position Spread"}

        output = (
            f"Wavetable: {data.get('device_name', '?')}\n\n"
            f"Oscillator 1:\n"
            f"  Wavetable category: {data.get('oscillator_1_wavetable_category', '?')}\n"
            f"  Wavetable index: {data.get('oscillator_1_wavetable_index', '?')}\n"
            f"  Effect mode: {data.get('oscillator_1_effect_mode', '?')}\n"
        )

        if data.get("oscillator_1_wavetables"):
            output += f"  Available wavetables: {data['oscillator_1_wavetables']}\n"

        output += (
            f"\nOscillator 2:\n"
            f"  Wavetable category: {data.get('oscillator_2_wavetable_category', '?')}\n"
            f"  Wavetable index: {data.get('oscillator_2_wavetable_index', '?')}\n"
            f"  Effect mode: {data.get('oscillator_2_effect_mode', '?')}\n"
        )

        if data.get("oscillator_2_wavetables"):
            output += f"  Available wavetables: {data['oscillator_2_wavetables']}\n"

        if data.get("wavetable_categories"):
            output += f"\nWavetable categories: {data['wavetable_categories']}\n"

        output += (
            f"\nVoice settings:\n"
            f"  Mode: {mono_poly_map.get(data.get('mono_poly'), '?')}\n"
            f"  Poly voices: {data.get('poly_voices', '?')}\n"
            f"  Unison mode: {unison_mode_map.get(data.get('unison_mode'), '?')}\n"
            f"  Unison voices: {data.get('unison_voice_count', '?')}\n"
            f"  Filter routing: {filter_routing_map.get(data.get('filter_routing'), '?')}\n"
        )

        # Modulation matrix
        if data.get("active_modulations"):
            output += "\nActive modulations:\n"
            for mod in data["active_modulations"]:
                sources = mod.get("sources", {})
                for src_name, amount in sources.items():
                    output += f"  {src_name} → {mod.get('target_name', '?')}: {amount:.4f}\n"

        if data.get("modulation_target_names"):
            output += f"\nModulation targets: {data['modulation_target_names']}\n"

        return output
    except ConnectionError as e:
        return f"M4L bridge not available: {e}"
    except Exception as e:
        logger.error(f"Error getting Wavetable info: {str(e)}")
        return f"Error getting Wavetable info: {str(e)}"


@mcp.tool()
def set_wavetable_modulation(
    ctx: Context,
    track_index: int,
    device_index: int,
    target_index: int,
    source_index: int,
    amount: float
) -> str:
    """Set a modulation amount in a Wavetable device's modulation matrix.

    Sources: 0=Env2, 1=Env3, 2=LFO1, 3=LFO2
    Target indices can be found via get_wavetable_info() modulation_target_names.

    Parameters:
    - track_index: Track containing the Wavetable
    - device_index: Index of the Wavetable device
    - target_index: Index of the modulation target parameter
    - source_index: Index of the modulation source (0=Env2, 1=Env3, 2=LFO1, 3=LFO2)
    - amount: Modulation amount (-1.0 to 1.0)

    Requires the AbletonMCP M4L bridge device.
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(device_index, "device_index")
        _validate_range(amount, "amount", -1.0, 1.0)

        m4l = get_m4l_connection()
        result = m4l.send_command("set_wavetable_modulation", {
            "track_index": track_index,
            "device_index": device_index,
            "target_index": target_index,
            "source_index": source_index,
            "amount": amount,
        })

        data = result.get("result", result)

        if data.get("error"):
            return f"Error: {data['error']}"

        return (
            f"Set modulation: {data.get('source_name', '?')} → target [{target_index}] "
            f"= {data.get('actual_amount', amount)}"
        )
    except ConnectionError as e:
        return f"M4L bridge not available: {e}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error setting Wavetable modulation: {str(e)}")
        return f"Error setting Wavetable modulation: {str(e)}"


@mcp.tool()
def set_wavetable_properties(
    ctx: Context,
    track_index: int,
    device_index: int,
    oscillator_1_wavetable_category: int = None,
    oscillator_1_wavetable_index: int = None,
    oscillator_2_wavetable_category: int = None,
    oscillator_2_wavetable_index: int = None,
    oscillator_1_effect_mode: int = None,
    oscillator_2_effect_mode: int = None,
    filter_routing: int = None,
    mono_poly: int = None,
    poly_voices: int = None,
    unison_mode: int = None,
    unison_voice_count: int = None
) -> str:
    """Set properties on a Wavetable device (oscillator settings).

    Use get_wavetable_info() to see available wavetable categories and current values.

    Settable properties (oscillator settings via M4L bridge):
    - oscillator_1_wavetable_category: Category index for Osc 1
    - oscillator_1_wavetable_index: Wavetable index within category for Osc 1
    - oscillator_2_wavetable_category: Category index for Osc 2
    - oscillator_2_wavetable_index: Wavetable index within category for Osc 2
    - oscillator_1_effect_mode: Effect mode for Osc 1
    - oscillator_2_effect_mode: Effect mode for Osc 2

    READ-ONLY properties (cannot be set via any API — use Ableton's GUI):
    - filter_routing, mono_poly, poly_voices, unison_mode, unison_voice_count
    These are readable via get_wavetable_info() but not exposed as DeviceParameters
    and LiveAPI.set() silently fails. This is a confirmed Ableton API limitation.

    Parameters:
    - track_index: Track containing the Wavetable
    - device_index: Index of the Wavetable device

    Requires the AbletonMCP M4L bridge device.
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(device_index, "device_index")

        # Settable properties (oscillator settings — work via M4L LiveAPI.set())
        settable_keys = {
            "oscillator_1_wavetable_category", "oscillator_1_wavetable_index",
            "oscillator_2_wavetable_category", "oscillator_2_wavetable_index",
            "oscillator_1_effect_mode", "oscillator_2_effect_mode",
        }
        # Read-only properties — not exposed as DeviceParameters, LiveAPI.set() fails
        readonly_keys = {
            "filter_routing", "mono_poly", "poly_voices",
            "unison_mode", "unison_voice_count",
        }

        all_vars = {
            "oscillator_1_wavetable_category": oscillator_1_wavetable_category,
            "oscillator_1_wavetable_index": oscillator_1_wavetable_index,
            "oscillator_2_wavetable_category": oscillator_2_wavetable_category,
            "oscillator_2_wavetable_index": oscillator_2_wavetable_index,
            "oscillator_1_effect_mode": oscillator_1_effect_mode,
            "oscillator_2_effect_mode": oscillator_2_effect_mode,
            "filter_routing": filter_routing,
            "mono_poly": mono_poly,
            "poly_voices": poly_voices,
            "unison_mode": unison_mode,
            "unison_voice_count": unison_voice_count,
        }

        props = {}
        readonly_requested = []
        for key, val in all_vars.items():
            if val is None:
                continue
            if key in settable_keys:
                props[key] = val
            elif key in readonly_keys:
                readonly_requested.append(key)

        if not props and not readonly_requested:
            return "No properties specified to set."

        output_parts = []

        # Set oscillator properties via M4L
        if props:
            m4l = get_m4l_connection()
            result = m4l.send_command("set_wavetable_props", {
                "track_index": track_index,
                "device_index": device_index,
                "properties": props,
            })

            data = result.get("result", result)

            if data.get("error"):
                return f"Error: {data['error']}"

            set_count = data.get('properties_set', 0)
            details = data.get('details', [])
            errors = data.get('errors', [])

            output_parts.append(f"Set {set_count} Wavetable properties.")
            if details:
                output_parts.append("Details:")
                for d in details:
                    output_parts.append(f"  {d['property']} = {d.get('value', '?')}")
            if errors:
                output_parts.append("Errors:")
                for err in errors:
                    output_parts.append(f"  {err['property']}: {err['error']}")

        # Report read-only properties
        if readonly_requested:
            names = ", ".join(readonly_requested)
            output_parts.append(
                f"\nCannot set: {names} — these Wavetable properties are read-only "
                f"(not exposed as DeviceParameters, LiveAPI.set() silently fails). "
                f"Use get_wavetable_info() to read their current values. "
                f"Change them manually in Ableton's GUI."
            )

        return "\n".join(output_parts)
    except ConnectionError as e:
        return f"M4L bridge not available: {e}"
    except Exception as e:
        logger.error(f"Error setting Wavetable properties: {str(e)}")
        return f"Error setting Wavetable properties: {str(e)}"


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

    IMPORTANT: Make sure device_index points to the correct device. Use
    get_device_parameters to verify the device name before calling this tool.
    For sound design prompts (e.g. "warm pad"), target the instrument/synth,
    not effects like Auto Filter or Reverb.

    Discovers all parameters on the target device and returns them so Claude can
    intelligently set values based on the description (e.g., "bright bass",
    "warm pad", "aggressive lead"). The current state is auto-saved as a snapshot
    for easy revert.

    After calling this tool, use set_device_parameters() to apply the preset values by name.
    Use restore_device_snapshot() with the revert snapshot ID to undo.

    Parameters:
    - track_index: The index of the track containing the device
    - device_index: The index of the device on the track
    - description: Text description of the desired sound (e.g., "bright plucky bass")
    - variation_count: How many variations to suggest (default: 1)

    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(device_index, "device_index")
        if variation_count < 1 or variation_count > 5:
            raise ValueError("variation_count must be between 1 and 5.")

        ableton = get_ableton_connection()
        result = ableton.send_command("get_device_parameters", {
            "track_index": track_index,
            "device_index": device_index,
        })

        device_name = result.get("device_name", "Unknown")
        device_class = result.get("device_type", "Unknown")
        params = result.get("parameters", [])

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
                f", display={p.get('display_value', '?')}){quant}{items}\n"
            )

        output += (
            f"\nNow calculate appropriate values for each parameter based on the description "
            f"'{description}' and device type '{device_class}'. Then call "
            f"set_device_parameters(track_index={track_index}, device_index={device_index}, "
            f"parameters=[...]) using parameter names (not indices). For quantized parameters, "
            f"use value_display with the option name. For continuous parameters, use value with a number."
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


# ==============================================================================
# Grid Notation Tools
# ==============================================================================

@mcp.tool()
def clip_to_grid(ctx: Context, track_index: int, clip_index: int) -> str:
    """Read a MIDI clip and display as ASCII grid notation (auto-detects drum vs melodic).

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    """
    try:
        from MCP_Server.grid_notation import notes_to_grid
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("get_clip_notes", {
            "track_index": track_index,
            "clip_index": clip_index,
            "start_time": 0.0,
            "time_span": 0.0,
            "start_pitch": 0,
            "pitch_span": 128,
        })
        notes = result.get("notes", [])
        clip_length = result.get("clip_length", 4.0)
        clip_name = result.get("clip_name", "Unknown")
        grid = notes_to_grid(notes)
        return f"Clip: {clip_name} ({clip_length} beats)\n\n{grid}"
    except ImportError:
        return "Error: grid_notation module not available"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error converting clip to grid: {str(e)}")
        return f"Error converting clip to grid: {str(e)}"


@mcp.tool()
def grid_to_clip(
    ctx: Context,
    track_index: int,
    clip_index: int,
    grid: str,
    length: float = 4.0,
    clear_existing: bool = True,
) -> str:
    """Write ASCII grid notation to a MIDI clip. Creates the clip if it doesn't exist.

    Grid format for drums:
        KK|o---o---|o---o-o-|
        SN|----o---|----o---|
        HC|x-x-x-x-|x-x-x-x-|

    Grid format for melodic:
        G4|----o---|--------|
        E4|--o-----|oooo----|
        C4|o-------|----oooo|

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot
    - grid: ASCII grid string (multi-line)
    - length: Clip length in beats (default: 4.0)
    - clear_existing: Clear existing notes before writing (default: true)
    """
    try:
        from MCP_Server.grid_notation import parse_grid
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        if length <= 0:
            return "Error: length must be greater than 0"

        notes = parse_grid(grid)
        if not notes:
            return "Error: No notes parsed from grid. Check the grid format."

        ableton = get_ableton_connection()

        # Create clip if it doesn't exist (ignore error if it already exists)
        try:
            ableton.send_command("create_clip", {
                "track_index": track_index,
                "clip_index": clip_index,
                "length": length,
            })
        except Exception as e:
            return f"Error creating clip: {str(e)}"

        # Clear existing notes if requested
        if clear_existing:
            try:
                ableton.send_command("clear_clip_notes", {
                    "track_index": track_index,
                    "clip_index": clip_index,
                })
            except Exception as e:
                return f"Error clearing clip notes: {str(e)}"

        # Add the parsed notes
        ableton.send_command("add_notes_to_clip", {
            "track_index": track_index,
            "clip_index": clip_index,
            "notes": notes,
        })
        return f"Wrote {len(notes)} notes from grid to track {track_index}, slot {clip_index} ({length} beats)"
    except ImportError:
        return "Error: grid_notation module not available"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Error writing grid to clip: {str(e)}")
        return f"Error writing grid to clip: {str(e)}"


# ==============================================================================
# New Tools: Session / Transport
# ==============================================================================

@mcp.tool()
def get_loop_info(ctx: Context) -> str:
    """Get loop bracket information including start, end, length, and current playback time."""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_loop_info")
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting loop info: {str(e)}")
        return f"Error getting loop info: {str(e)}"


@mcp.tool()
def get_recording_status(ctx: Context) -> str:
    """Get the current recording status including armed tracks, record mode, and overdub state."""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_recording_status")
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting recording status: {str(e)}")
        return f"Error getting recording status: {str(e)}"


@mcp.tool()
def set_loop_start(ctx: Context, position: float) -> str:
    """Set the loop start position in beats.

    Parameters:
    - position: The loop start position in beats
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("set_loop_start", {"position": position})
        return f"Loop start set to {result.get('loop_start', position)} beats"
    except Exception as e:
        return f"Error setting loop start: {str(e)}"


@mcp.tool()
def set_loop_end(ctx: Context, position: float) -> str:
    """Set the loop end position in beats.

    Parameters:
    - position: The loop end position in beats
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("set_loop_end", {"position": position})
        return f"Loop end set to {result.get('loop_end', position)} beats"
    except Exception as e:
        return f"Error setting loop end: {str(e)}"


@mcp.tool()
def set_loop_length(ctx: Context, length: float) -> str:
    """Set the loop length in beats (adjusts loop end relative to loop start).

    Parameters:
    - length: The loop length in beats
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("set_loop_length", {"length": length})
        return f"Loop length set to {result.get('loop_length', length)} beats"
    except Exception as e:
        return f"Error setting loop length: {str(e)}"


@mcp.tool()
def set_playback_position(ctx: Context, position: float) -> str:
    """Move the playhead to a specific beat position.

    Parameters:
    - position: The position in beats to jump to (0.0 = start of song)
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("set_playback_position", {"position": position})
        return f"Playback position set to {result.get('position', position)} beats"
    except Exception as e:
        return f"Error setting playback position: {str(e)}"


@mcp.tool()
def set_arrangement_overdub(ctx: Context, enabled: bool) -> str:
    """Enable or disable arrangement overdub mode.

    Parameters:
    - enabled: True to enable overdub, False to disable
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("set_arrangement_overdub", {"enabled": enabled})
        return f"Arrangement overdub {'enabled' if result.get('overdub', enabled) else 'disabled'}"
    except Exception as e:
        return f"Error setting arrangement overdub: {str(e)}"


@mcp.tool()
def start_arrangement_recording(ctx: Context) -> str:
    """Start arrangement recording in Ableton."""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("start_arrangement_recording")
        return "Arrangement recording started"
    except Exception as e:
        return f"Error starting arrangement recording: {str(e)}"


@mcp.tool()
def stop_arrangement_recording(ctx: Context) -> str:
    """Stop arrangement recording in Ableton."""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("stop_arrangement_recording")
        return "Arrangement recording stopped"
    except Exception as e:
        return f"Error stopping arrangement recording: {str(e)}"


@mcp.tool()
def set_metronome(ctx: Context, enabled: bool) -> str:
    """Enable or disable the metronome.

    Parameters:
    - enabled: True to enable the metronome, False to disable
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("set_metronome", {"enabled": enabled})
        return f"Metronome {'enabled' if result.get('metronome', enabled) else 'disabled'}"
    except Exception as e:
        return f"Error setting metronome: {str(e)}"


@mcp.tool()
def tap_tempo(ctx: Context) -> str:
    """Tap tempo - call repeatedly to set tempo by tapping."""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("tap_tempo")
        return f"Tap tempo registered. Current tempo: {result.get('tempo', '?')} BPM"
    except Exception as e:
        return f"Error tapping tempo: {str(e)}"


# ==============================================================================
# New Tools: Tracks
# ==============================================================================

@mcp.tool()
def get_all_tracks_info(ctx: Context) -> str:
    """Get information about all tracks in the session at once (bulk query)."""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_all_tracks_info")
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting all tracks info: {str(e)}")
        return f"Error getting all tracks info: {str(e)}"


@mcp.tool()
def create_return_track(ctx: Context) -> str:
    """Create a new return track in the session."""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("create_return_track")
        return f"Created return track: {result.get('name', 'unknown')}"
    except Exception as e:
        return f"Error creating return track: {str(e)}"


@mcp.tool()
def set_track_color(ctx: Context, track_index: int, color_index: int) -> str:
    """Set the color of a track.

    Parameters:
    - track_index: The index of the track
    - color_index: The color index (0-69, Ableton's color palette)
    """
    try:
        _validate_index(track_index, "track_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("set_track_color", {
            "track_index": track_index,
            "color_index": color_index,
        })
        return f"Track {track_index} color set to {color_index}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        return f"Error setting track color: {str(e)}"


@mcp.tool()
def group_tracks(ctx: Context, track_indices: list) -> str:
    """Group multiple tracks together.

    Parameters:
    - track_indices: List of track indices to group together
    """
    try:
        if not isinstance(track_indices, list) or len(track_indices) < 2:
            return "Error: track_indices must be a list of at least 2 track indices"
        ableton = get_ableton_connection()
        result = ableton.send_command("group_tracks", {"track_indices": track_indices})
        return f"Grouped {len(track_indices)} tracks"
    except Exception as e:
        return f"Error grouping tracks: {str(e)}"


# ==============================================================================
# New Tools: Audio
# ==============================================================================

@mcp.tool()
def get_audio_clip_info(ctx: Context, track_index: int, clip_index: int) -> str:
    """Get detailed information about an audio clip (warp mode, gain, file path, etc.).

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("get_audio_clip_info", {
            "track_index": track_index,
            "clip_index": clip_index,
        })
        return json.dumps(result, indent=2)
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        return f"Error getting audio clip info: {str(e)}"


@mcp.tool()
def analyze_audio_clip(ctx: Context, track_index: int, clip_index: int) -> str:
    """Analyze an audio clip comprehensively (tempo, warp, sample properties, frequency hints).

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("analyze_audio_clip", {
            "track_index": track_index,
            "clip_index": clip_index,
        })
        return json.dumps(result, indent=2)
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        return f"Error analyzing audio clip: {str(e)}"


@mcp.tool()
def set_warp_mode(ctx: Context, track_index: int, clip_index: int, warp_mode: str) -> str:
    """Set the warp mode for an audio clip.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    - warp_mode: The warp mode (beats, tones, texture, re_pitch, complex, complex_pro)
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("set_warp_mode", {
            "track_index": track_index,
            "clip_index": clip_index,
            "warp_mode": warp_mode,
        })
        return f"Warp mode set to {result.get('warp_mode', warp_mode)}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        return f"Error setting warp mode: {str(e)}"


@mcp.tool()
def set_clip_warp(ctx: Context, track_index: int, clip_index: int, warping_enabled: bool) -> str:
    """Enable or disable warping for an audio clip.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    - warping_enabled: True to enable warping, False to disable
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("set_clip_warp", {
            "track_index": track_index,
            "clip_index": clip_index,
            "warping_enabled": warping_enabled,
        })
        return f"Warping {'enabled' if result.get('warping', warping_enabled) else 'disabled'}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        return f"Error setting clip warp: {str(e)}"


@mcp.tool()
def reverse_clip(ctx: Context, track_index: int, clip_index: int) -> str:
    """Reverse an audio clip.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("reverse_clip", {
            "track_index": track_index,
            "clip_index": clip_index,
        })
        return f"Clip reversed: {result.get('reversed', True)}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        return f"Error reversing clip: {str(e)}"


@mcp.tool()
def freeze_track(ctx: Context, track_index: int) -> str:
    """Freeze a track (render effects in place to reduce CPU load).

    Parameters:
    - track_index: The index of the track to freeze
    """
    try:
        _validate_index(track_index, "track_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("freeze_track", {"track_index": track_index})
        return f"Track {track_index} ({result.get('track_name', '?')}) frozen"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        return f"Error freezing track: {str(e)}"


@mcp.tool()
def unfreeze_track(ctx: Context, track_index: int) -> str:
    """Unfreeze a track.

    Parameters:
    - track_index: The index of the track to unfreeze
    """
    try:
        _validate_index(track_index, "track_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("unfreeze_track", {"track_index": track_index})
        return f"Track {track_index} ({result.get('track_name', '?')}) unfrozen"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        return f"Error unfreezing track: {str(e)}"


# ==============================================================================
# New Tools: MIDI
# ==============================================================================

@mcp.tool()
def capture_midi(ctx: Context) -> str:
    """Capture recently played MIDI notes (requires Live 11 or later)."""
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("capture_midi")
        return "MIDI captured successfully"
    except Exception as e:
        return f"Error capturing MIDI: {str(e)}"


@mcp.tool()
def apply_groove(ctx: Context, track_index: int, clip_index: int, groove_amount: float) -> str:
    """Apply groove to a MIDI clip.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    - groove_amount: Groove amount (0.0 to 1.0)
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(clip_index, "clip_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("apply_groove", {
            "track_index": track_index,
            "clip_index": clip_index,
            "groove_amount": groove_amount,
        })
        return f"Groove amount set to {result.get('groove_amount', groove_amount)}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        return f"Error applying groove: {str(e)}"


# ==============================================================================
# New Tools: Arrangement
# ==============================================================================

@mcp.tool()
def get_arrangement_clips(ctx: Context, track_index: int) -> str:
    """Get all clips in arrangement view for a track.

    Parameters:
    - track_index: The index of the track to get arrangement clips from
    """
    try:
        _validate_index(track_index, "track_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("get_arrangement_clips", {"track_index": track_index})
        return json.dumps(result, indent=2)
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        return f"Error getting arrangement clips: {str(e)}"


@mcp.tool()
def delete_time(ctx: Context, start_time: float, end_time: float) -> str:
    """Delete a section of time from the arrangement (removes time and shifts everything after).

    Parameters:
    - start_time: Start position in beats
    - end_time: End position in beats
    """
    try:
        if start_time >= end_time:
            return "Error: start_time must be less than end_time"
        ableton = get_ableton_connection()
        result = ableton.send_command("delete_time", {
            "start_time": start_time,
            "end_time": end_time,
        })
        return f"Deleted time from {start_time} to {end_time} ({result.get('deleted_length', end_time - start_time)} beats)"
    except Exception as e:
        return f"Error deleting time: {str(e)}"


@mcp.tool()
def duplicate_time(ctx: Context, start_time: float, end_time: float) -> str:
    """Duplicate a section of time in the arrangement (copies and inserts after the selection).

    Parameters:
    - start_time: Start position in beats
    - end_time: End position in beats
    """
    try:
        if start_time >= end_time:
            return "Error: start_time must be less than end_time"
        ableton = get_ableton_connection()
        result = ableton.send_command("duplicate_time", {
            "start_time": start_time,
            "end_time": end_time,
        })
        return f"Duplicated time from {start_time} to {end_time} (pasted at {result.get('pasted_at', end_time)})"
    except Exception as e:
        return f"Error duplicating time: {str(e)}"


@mcp.tool()
def insert_silence(ctx: Context, position: float, length: float) -> str:
    """Insert silence at a position in the arrangement (shifts everything after).

    Parameters:
    - position: The position in beats to insert silence at
    - length: The length of silence in beats
    """
    try:
        if length <= 0:
            return "Error: length must be greater than 0"
        ableton = get_ableton_connection()
        result = ableton.send_command("insert_silence", {
            "position": position,
            "length": length,
        })
        return f"Inserted {length} beats of silence at position {position}"
    except Exception as e:
        return f"Error inserting silence: {str(e)}"


# ==============================================================================
# New Tools: Track-level Automation
# ==============================================================================

@mcp.tool()
def create_track_automation(
    ctx: Context,
    track_index: int,
    parameter_name: str,
    automation_points: list,
) -> str:
    """Create automation for a track parameter (arrangement-level).

    Parameters:
    - track_index: The index of the track
    - parameter_name: Name of the parameter to automate (e.g., "Volume", "Pan")
    - automation_points: List of {time: float, value: float} dictionaries
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_automation_points(automation_points)
        ableton = get_ableton_connection()
        result = ableton.send_command("create_track_automation", {
            "track_index": track_index,
            "parameter_name": parameter_name,
            "automation_points": automation_points,
        })
        return f"Created track automation for '{parameter_name}' with {result.get('points_added', len(automation_points))} points"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        return f"Error creating track automation: {str(e)}"


@mcp.tool()
def clear_track_automation(
    ctx: Context,
    track_index: int,
    parameter_name: str,
    start_time: float,
    end_time: float,
) -> str:
    """Clear automation for a parameter in a time range (arrangement-level).

    Parameters:
    - track_index: The index of the track
    - parameter_name: Name of the parameter to clear automation for
    - start_time: Start time in beats
    - end_time: End time in beats
    """
    try:
        _validate_index(track_index, "track_index")
        if start_time >= end_time:
            return "Error: start_time must be less than end_time"
        ableton = get_ableton_connection()
        result = ableton.send_command("clear_track_automation", {
            "track_index": track_index,
            "parameter_name": parameter_name,
            "start_time": start_time,
            "end_time": end_time,
        })
        return f"Cleared automation for '{parameter_name}' from {start_time} to {end_time}"
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        return f"Error clearing track automation: {str(e)}"


# ==============================================================================
# New Tools: Devices (track_type support)
# ==============================================================================

@mcp.tool()
def get_macro_values(ctx: Context, track_index: int, device_index: int) -> str:
    """Get the current macro knob values for an Instrument Rack.

    Parameters:
    - track_index: The index of the track containing the device
    - device_index: The index of the device on the track
    """
    try:
        _validate_index(track_index, "track_index")
        _validate_index(device_index, "device_index")
        ableton = get_ableton_connection()
        result = ableton.send_command("get_macro_values", {
            "track_index": track_index,
            "device_index": device_index,
        })
        return json.dumps(result, indent=2)
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        return f"Error getting macro values: {str(e)}"


# Main execution
def main():
    """Run the MCP server"""
    mcp.run()

if __name__ == "__main__":
    main()