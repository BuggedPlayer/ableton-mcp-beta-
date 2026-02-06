# AbletonMCP - Ableton Live Model Context Protocol Integration

AbletonMCP connects Ableton Live to Claude AI through the Model Context Protocol (MCP), allowing Claude to directly interact with and control Ableton Live. This integration enables prompt-assisted music production, track creation, and Live session manipulation.

Based on the original [ableton-mcp](https://github.com/ahujasid/ableton-mcp) by [Siddharth Ahuja](https://x.com/sidahuj).

---

## Architecture

The system consists of three components that communicate through two protocols:

```
Claude AI  <--MCP-->  MCP Server  <--TCP:9877-->  Ableton Remote Script
                          |
                          +------<--UDP/OSC:9878/9879-->  M4L Bridge (optional)
                          |
                          +------<--HTTP:9880-->  Web Status Dashboard
```

### 1. Ableton Remote Script (`AbletonMCP_Remote_Script/__init__.py`)
A MIDI Remote Script (Python) that runs inside Ableton Live as a Control Surface. It creates a TCP socket server on port **9877** that listens for JSON commands and executes them against the Live API.

- Extends `ControlSurface` from Ableton's `_Framework`
- Runs a background thread for the TCP server
- Handles commands via a dispatch table mapping command types to handler methods
- Supports Live 10, 11, and 12 APIs with automatic fallbacks
- Defensive handling for group tracks, return tracks, and edge cases

### 2. MCP Server (`MCP_Server/server.py`)
A Python server that implements the Model Context Protocol and bridges between the AI client and Ableton. It maintains two connection classes:

- **`AbletonConnection`** — TCP client connecting to the Remote Script on port 9877. Sends length-prefixed JSON commands and receives length-prefixed JSON responses. Includes automatic reconnection logic.
- **`M4LConnection`** — UDP/OSC client connecting to the Max for Live bridge. Sends native OSC messages on port 9878 and listens for base64-encoded JSON responses on port 9879. Includes auto-reconnect with exponential backoff.

The server exposes **94 MCP tools** that Claude can call. It also runs a **web status dashboard** on port 9880.

**Startup sequence:**
1. Connect to Ableton Remote Script (TCP port 9877)
2. Auto-connect to M4L bridge (UDP ports 9878/9879) — no need to wait for a tool call
3. Start the web status dashboard (HTTP port 9880)

### 3. Web Status Dashboard (`http://127.0.0.1:9880`)
A live web dashboard running on a background daemon thread alongside the MCP server. Built with starlette + uvicorn (already installed as transitive deps of `mcp[cli]`, zero new dependencies). Auto-refreshes every 3 seconds.

- **Status cards**: Server version, uptime, Ableton connection, M4L bridge status, snapshot/macro/param map counts, total tool calls
- **Most Used Tools**: Bar chart of the top 10 most-called MCP tools
- **Recent Tool Calls**: Table showing tool name, duration, arguments, and error status for the last 50 tool calls
- **Server Log**: Real-time terminal-style log viewer with color-coded log levels (INFO, WARNING, ERROR) showing the last 200 server log entries — scroll to see the full boot sequence and all command activity
- Configurable port via `ABLETON_MCP_DASHBOARD_PORT` environment variable (default: 9880)
- Non-fatal: if the dashboard fails to start (e.g. port conflict), the MCP server continues normally

### 4. Max for Live Bridge (`M4L_Device/m4l_bridge.js`) *(optional)*
A JavaScript file running inside a Max for Live `[js]` object. It provides deep Live Object Model (LOM) access to hidden/non-automatable device parameters that the standard Remote Script API cannot reach.

---

## Complete Tool Reference (94 Tools)

### Session & Transport

| Tool | Parameters | Description |
|---|---|---|
| `get_session_info` | — | Get detailed information about the current Ableton session (tracks, tempo, time signature, scenes) |
| `set_tempo` | `tempo: float` | Set the session tempo in BPM |
| `start_playback` | — | Start playing the session |
| `stop_playback` | — | Stop playing the session |
| `get_song_transport` | — | Get arrangement state: playhead position, tempo, time signature, loop bracket, record mode, song length |
| `set_song_time` | `time: float` | Set the arrangement playhead position (in beats) |
| `set_song_loop` | `enabled?: bool, start?: float, length?: float` | Control the arrangement loop bracket (enable/disable, set start/length) |

### Track Management

| Tool | Parameters | Description |
|---|---|---|
| `get_track_info` | `track_index: int` | Get detailed info about a track (name, volume, pan, devices, clips, group track support) |
| `create_midi_track` | `index: int = -1` | Create a new MIDI track (-1 = end of list) |
| `create_audio_track` | `index: int = -1` | Create a new audio track (-1 = end of list) |
| `set_track_name` | `track_index: int, name: str` | Set the name of a track |
| `delete_track` | `track_index: int` | Delete a track from the session |
| `duplicate_track` | `track_index: int` | Duplicate a track with all its devices and clips |

### Track Mixing

| Tool | Parameters | Description |
|---|---|---|
| `set_track_volume` | `track_index: int, volume: float` | Set track volume (0.0-1.0, 0.85 ~ 0dB) |
| `set_track_pan` | `track_index: int, pan: float` | Set track panning (-1.0 left, 0.0 center, 1.0 right) |
| `set_track_mute` | `track_index: int, mute: bool` | Mute or unmute a track |
| `set_track_solo` | `track_index: int, solo: bool` | Solo or unsolo a track |
| `set_track_arm` | `track_index: int, arm: bool` | Arm or disarm a track for recording |
| `set_track_send` | `track_index: int, send_index: int, value: float` | Set send level to a return track (0 = Send A, 1 = Send B) |

### Clip Management

| Tool | Parameters | Description |
|---|---|---|
| `create_clip` | `track_index: int, clip_index: int, length: float = 4.0` | Create a new MIDI clip in a clip slot |
| `get_clip_info` | `track_index: int, clip_index: int` | Get detailed clip information |
| `set_clip_name` | `track_index: int, clip_index: int, name: str` | Set the name of a clip |
| `delete_clip` | `track_index: int, clip_index: int` | Delete a clip from a clip slot |
| `duplicate_clip` | `track_index: int, clip_index: int, target_clip_index: int` | Duplicate a clip to another slot on the same track |
| `fire_clip` | `track_index: int, clip_index: int` | Start playing a clip |
| `stop_clip` | `track_index: int, clip_index: int` | Stop playing a clip |
| `set_clip_looping` | `track_index: int, clip_index: int, looping: bool` | Enable or disable clip looping |
| `set_clip_loop_points` | `track_index: int, clip_index: int, loop_start: float, loop_end: float` | Set loop start and end positions in beats |
| `set_clip_color` | `track_index: int, clip_index: int, color_index: int` | Set clip color (0-69, Ableton's palette) |
| `crop_clip` | `track_index: int, clip_index: int` | Trim clip to its current loop region, discarding content outside |
| `duplicate_clip_loop` | `track_index: int, clip_index: int` | Double the loop content (e.g. 4 bars → 8 bars with content repeated) |
| `set_clip_start_end` | `track_index: int, clip_index: int, start_marker?: float, end_marker?: float` | Set clip start/end marker positions (controls playback region) |
| `duplicate_clip_to_arrangement` | `track_index: int, clip_index: int, time: float` | Copy a session clip to the arrangement timeline at a beat position (Live 11+) |

### MIDI Notes

| Tool | Parameters | Description |
|---|---|---|
| `add_notes_to_clip` | `track_index: int, clip_index: int, notes: list` | Add MIDI notes to a clip. Each note: `{pitch, start_time, duration, velocity, mute}` |
| `get_clip_notes` | `track_index: int, clip_index: int, start_time: float, time_span: float, start_pitch: int, pitch_span: int` | Get MIDI notes from a clip |
| `clear_clip_notes` | `track_index: int, clip_index: int` | Remove all MIDI notes from a clip |
| `quantize_clip_notes` | `track_index: int, clip_index: int, grid_size: float` | Quantize notes to grid (0.25=16th, 0.5=8th, 1.0=quarter) |
| `transpose_clip_notes` | `track_index: int, clip_index: int, semitones: int` | Transpose all notes by N semitones |
| `add_notes_extended` | `track_index: int, clip_index: int, notes: list` | Add notes with Live 11+ properties: `{pitch, start_time, duration, velocity, mute, probability, velocity_deviation, release_velocity}` |
| `get_notes_extended` | `track_index: int, clip_index: int, start_time?: float, time_span?: float` | Get notes with extended properties (probability, velocity_deviation, release_velocity) |
| `remove_notes_range` | `track_index: int, clip_index: int, from_time: float, time_span: float, from_pitch?: int, pitch_span?: int` | Selectively remove notes within a specific time and pitch range |

### Automation (v1.8.0)

| Tool | Parameters | Description |
|---|---|---|
| `create_clip_automation` | `track_index: int, clip_index: int, parameter_name: str, automation_points: list` | Create automation for a parameter within a clip. Points: `[{time, value}, ...]` |
| `get_clip_automation` | `track_index: int, clip_index: int, parameter_name: str` | Read existing automation — samples envelope at 64 points across the clip |
| `clear_clip_automation` | `track_index: int, clip_index: int, parameter_name: str` | Clear automation for a specific parameter in a clip |
| `list_clip_automated_parameters` | `track_index: int, clip_index: int` | List all parameters that have automation in a clip (mixer, sends, device params) |

### Scenes

| Tool | Parameters | Description |
|---|---|---|
| `get_scenes` | — | Get information about all scenes |
| `create_scene` | `index: int = -1` | Create a new scene (-1 = end of list) |
| `set_scene_name` | `scene_index: int, name: str` | Set the name of a scene |
| `fire_scene` | `scene_index: int` | Fire a scene (launch all clips in that row) |
| `delete_scene` | `scene_index: int` | Delete a scene |

### Return Tracks

| Tool | Parameters | Description |
|---|---|---|
| `get_return_tracks` | — | Get information about all return tracks |
| `get_return_track_info` | `return_track_index: int` | Get detailed info about a return track (0=A, 1=B, etc.) |
| `set_return_track_volume` | `return_track_index: int, volume: float` | Set return track volume (0.0-1.0) |
| `set_return_track_pan` | `return_track_index: int, pan: float` | Set return track panning |
| `set_return_track_mute` | `return_track_index: int, mute: bool` | Mute/unmute a return track |
| `set_return_track_solo` | `return_track_index: int, solo: bool` | Solo/unsolo a return track |

### Master Track

| Tool | Parameters | Description |
|---|---|---|
| `get_master_track_info` | — | Get master track info (volume, pan, devices) |
| `set_master_volume` | `volume: float` | Set master track volume (0.0-1.0, 0.85 ~ 0dB) |

### Devices & Parameters

| Tool | Parameters | Description |
|---|---|---|
| `get_device_parameters` | `track_index: int, device_index: int` | Get all parameters and values for a device |
| `set_device_parameter` | `track_index: int, device_index: int, parameter_name: str, value: float` | Set a device parameter by name (clamped to min/max) |
| `delete_device` | `track_index: int, device_index: int` | Delete a device from a track |

### Browser & Loading

| Tool | Parameters | Description |
|---|---|---|
| `get_browser_tree` | `category_type: str = "all"` | Get browser categories (instruments, sounds, drums, audio_effects, midi_effects) |
| `get_browser_items_at_path` | `path: str` | Get items at a browser path (e.g. "category/folder/subfolder") |
| `search_browser` | `query: str, category: str = "all"` | Search the browser for items by name |
| `load_instrument_or_effect` | `track_index: int, uri: str` | Load an instrument or effect onto a track using its browser URI |
| `load_sample` | `track_index: int, sample_uri: str` | Load an audio sample onto a track |
| `load_drum_kit` | `track_index: int, rack_uri: str, kit_path: str` | Load a drum rack with a specific kit |
| `get_user_library` | — | Get user library browser tree |
| `get_user_folders` | — | Get user-configured sample folders (returns browser URIs) |

### M4L Bridge Tools (require Max for Live device)

| Tool | Parameters | Description |
|---|---|---|
| `m4l_status` | — | Check if the M4L bridge device is loaded and responsive |
| `discover_device_params` | `track_index: int, device_index: int` | Discover ALL parameters including hidden/non-automatable ones |
| `get_device_hidden_parameters` | `track_index: int, device_index: int` | Get hidden parameter details (name, value, min, max, quantized) |
| `set_device_hidden_parameter` | `track_index: int, device_index: int, parameter_index: int, value: float` | Set any parameter by its LOM index |
| `list_instrument_rack_presets` | — | List Instrument Rack presets in user library (VST/AU workaround) |
| `batch_set_hidden_parameters` | `track_index: int, device_index: int, parameters: list` | Set multiple hidden parameters in one call. Each entry: `{index, value}` |

### Snapshot & Versioning (v1.6.0)

| Tool | Parameters | Description |
|---|---|---|
| `snapshot_device_state` | `track_index: int, device_index: int, snapshot_name: str` | Capture complete device state (all params) into a named snapshot |
| `restore_device_snapshot` | `snapshot_id: str, track_index?: int, device_index?: int` | Restore a saved snapshot (optionally to a different device) |
| `list_snapshots` | — | List all stored snapshots with IDs, names, and timestamps |
| `get_snapshot_details` | `snapshot_id: str` | Show full parameter values of a snapshot |
| `delete_snapshot` | `snapshot_id: str` | Delete a snapshot |
| `delete_all_snapshots` | — | Clear all snapshots, macros, and parameter maps |
| `snapshot_all_devices` | `track_indices: list, snapshot_name: str` | Snapshot every device across multiple tracks as a group |
| `restore_group_snapshot` | `group_id: str` | Restore all devices from a group snapshot |
| `compare_snapshots` | `snapshot_a_id: str, snapshot_b_id: str` | Diff two snapshots — show changed parameters with deltas |

### Preset Morph Engine (v1.6.0)

| Tool | Parameters | Description |
|---|---|---|
| `morph_between_snapshots` | `snapshot_a_id: str, snapshot_b_id: str, position: float` | Interpolate between two snapshots (0.0=A, 1.0=B). Quantized params snap at midpoint |

### Smart Macro Controller (v1.6.0)

| Tool | Parameters | Description |
|---|---|---|
| `create_macro_controller` | `name: str, mappings: list` | Create a macro linking multiple params. Each mapping: `{track_index, device_index, parameter_index, min_value, max_value}` |
| `set_macro_value` | `macro_id: str, value: float` | Set macro 0.0-1.0, interpolating all linked params via batch set |
| `list_macros` | — | List all macro controllers |
| `delete_macro` | `macro_id: str` | Delete a macro controller |

### Intelligent Preset Generator (v1.6.0)

| Tool | Parameters | Description |
|---|---|---|
| `generate_preset` | `track_index: int, device_index: int, description: str, variation_count: int` | Discover all params, auto-snapshot current state, return param list for AI-driven preset creation |

### VST/AU Parameter Mapper (v1.6.0)

| Tool | Parameters | Description |
|---|---|---|
| `create_parameter_map` | `track_index: int, device_index: int, friendly_names: list` | Map cryptic param names to friendly names with categories |
| `get_parameter_map` | `map_id: str` | Retrieve a stored parameter map |
| `list_parameter_maps` | — | List all parameter maps |
| `delete_parameter_map` | `map_id: str` | Delete a parameter map |

---

## Communication Protocols

### TCP Protocol (Remote Script <-> MCP Server)

Port **9877**, length-prefixed JSON messages.

**Request format:**
```json
{
  "type": "command_name",
  "params": { "key": "value" }
}
```

**Response format:**
```json
{
  "status": "success",
  "result": { ... }
}
```

Messages are framed with a 4-byte big-endian length prefix to prevent TCP stream corruption. Both sides use `struct.pack(">I", len(data))` for framing.

### OSC/UDP Protocol (M4L Bridge <-> MCP Server)

The MCP server sends native OSC messages to port **9878** (M4L `udpreceive`). The M4L bridge responds with base64-encoded JSON via `udpsend` to port **9879**.

**OSC Commands:**

| Address | Arguments | Description |
|---|---|---|
| `/ping` | `request_id` | Health check — returns bridge version |
| `/discover_params` | `track_index, device_index, request_id` | Enumerate all LOM parameters |
| `/get_hidden_params` | `track_index, device_index, request_id` | Get hidden parameter details |
| `/set_hidden_param` | `track_index, device_index, param_index, value, request_id` | Set a parameter by LOM index |
| `/batch_set_hidden_params` | `track_index, device_index, params_b64, request_id` | Set multiple params at once. `params_b64` is base64-encoded JSON: `[{index, value}, ...]` |
| `/check_dashboard` | `request_id` | Returns the dashboard URL and bridge version |

**Why base64?** Max treats `{` and `}` as special characters in its messaging system, so JSON responses are base64-encoded before being sent through `outlet()`.

---

## Max for Live Bridge Implementation

The M4L bridge (`m4l_bridge.js`) is a JavaScript file running in a Max `[js]` object. It provides access to the Live Object Model (LOM), which exposes parameters that the standard Remote Script API hides.

### Max Patch Structure

```
[udpreceive 9878] --> [js m4l_bridge.js] --> [udpsend localhost 9879]
```

### How It Works

1. **OSC Routing**: Max's `udpreceive` parses incoming OSC packets. The OSC address (e.g. `/ping`) becomes the `messagename` in the `anything()` function. A `switch` statement routes to the appropriate handler.

2. **LOM Access**: Uses Max's `LiveAPI` to access the Live Object Model:
   ```javascript
   var devicePath = "live_set tracks " + trackIdx + " devices " + deviceIdx;
   var deviceApi = new LiveAPI(null, devicePath);
   ```

3. **Parameter Discovery**: Iterates all parameters on a device via `getcount("parameters")`, reading each parameter's `name`, `value`, `min`, `max`, `is_quantized`, `default_value`, and `value_items`.

4. **Parameter Setting**: Sets any parameter by its LOM index, with value clamping:
   ```javascript
   var clamped = Math.max(minVal, Math.min(maxVal, value));
   paramApi.set("value", clamped);
   ```

5. **Response Encoding**: All responses are JSON-stringified, then base64-encoded (Max's JS engine lacks `btoa`, so a custom encoder is included), and sent via `outlet(0, encoded)`.

### Setup

1. Requires Ableton Live **Suite** or **Standard + Max for Live**
2. Create a new Max MIDI Effect
3. Build the patch: `[udpreceive 9878] -> [js m4l_bridge.js] -> [udpsend localhost 9879]`
4. Copy `M4L_Device/m4l_bridge.js` to the device's folder
5. Save the device and load it on any track
6. See [`M4L_Device/README.md`](M4L_Device/README.md) for detailed instructions

---

## Remote Script Implementation Details

The Remote Script (`AbletonMCP_Remote_Script/__init__.py`) is a `ControlSurface` subclass that:

- **Socket Server**: Runs a TCP server on port 9877 in a background thread. Each connected client gets its own handler thread.
- **Command Dispatch**: Incoming JSON commands are dispatched to handler methods via a type-to-function mapping (e.g. `"get_session_info"` -> `_get_session_info()`).
- **Thread Safety**: Commands that need the Live API are scheduled on the main thread using `schedule_message()`, since Ableton's API is not thread-safe.
- **Live 11/12 Compatibility**: Note operations use `remove_notes_extended()` with fallback to legacy `remove_notes()`. Quantize uses the built-in `clip.quantize()` API when available.
- **Group Track Support**: `get_track_info` safely handles group tracks by wrapping `arm`, `is_foldable`, `has_audio_input`, `has_midi_input` in try/except blocks, since group tracks don't support all track properties.

---

## v1.6.0 Feature Systems

Five advanced feature systems built on shared core primitives. All route through the M4L bridge — no Remote Script changes needed.

### Intelligent Preset Generator
Discover all device parameters, then Claude intelligently sets values based on text descriptions like "bright bass", "warm pad", or "aggressive lead". Auto-snapshots current state for easy revert.

### Smart Macro Controller
Link multiple parameters across devices to a single 0.0-1.0 "super-knob". When the macro moves, all linked parameters interpolate proportionally. Supports cross-device, cross-track linking.

### VST/AU Parameter Mapper
Scan third-party plugins, discover their hidden parameters, and create custom control surfaces with human-readable names organized by category.

### Preset Morph Engine
Capture two different device states as snapshots, then smoothly morph between them at any position. Continuous parameters interpolate linearly; quantized parameters (waveform selectors) snap at the midpoint.

### Device State Versioning & Undo
Snapshot every device on your tracks, then rollback to any previous state. Group snapshots capture multiple tracks at once. Compare any two snapshots to see exactly what changed.

### Web Status Dashboard
Live web dashboard at `http://127.0.0.1:9880` showing connection status, tool call metrics, and a real-time server log. Runs automatically on startup — just open your browser.

### Auto M4L Bridge Connection
The server now automatically connects to the M4L bridge device on startup, right after connecting to Ableton. No need to wait for a tool call to trigger the connection — the dashboard shows M4L status immediately.

**Core primitive**: `batch_set_hidden_parameters` sets 100+ params in a single M4L round-trip instead of 100 separate calls.

### v1.8.0 — Arrangement View & Advanced Editing

#### Arrangement View Workflow
Build clips in session view, then place them on the arrangement timeline with `duplicate_clip_to_arrangement`. Control the arrangement playhead (`set_song_time`), loop bracket (`set_song_loop`), and read full transport state (`get_song_transport`). While Ableton's API doesn't allow direct arrangement clip editing, this session-to-arrangement workflow is the supported path.

#### Advanced Clip Operations
`crop_clip` trims a clip to its loop region. `duplicate_clip_loop` doubles the loop content. `set_clip_start_end` controls playback start/end markers without modifying notes.

#### Advanced MIDI Note Editing (Live 11+)
`add_notes_extended` and `get_notes_extended` support Live 11+ note properties: **probability** (0.0-1.0 trigger chance), **velocity_deviation** (random velocity range), and **release_velocity**. Falls back gracefully to legacy APIs on older Live versions. `remove_notes_range` allows selective note removal by time and pitch range.

#### Automation Reading & Editing
Automation is no longer write-only. `get_clip_automation` reads existing envelope data by sampling 64 points across the clip. `list_clip_automated_parameters` discovers all automated parameters in a clip (mixer, sends, device params). `clear_clip_automation` removes automation for a specific parameter.

---

## Installation

### Prerequisites

- Ableton Live 10+ (12 recommended)
- Python 3.10+
- [uv package manager](https://astral.sh/uv)

Install uv:
```bash
# macOS
brew install uv

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### MCP Server Setup (Claude Desktop)

Add to `claude_desktop_config.json`:
```json
{
    "mcpServers": {
        "AbletonMCP-Beta": {
            "command": "uvx",
            "args": ["path/to/dist/ableton_mcp_beta-1.8.0-py3-none-any.whl"]
        }
    }
}
```

After starting, the web dashboard is available at `http://127.0.0.1:9880`.

### Cursor Integration

Go to Cursor Settings > MCP and set the command to the wheel path. Only run one instance of the MCP server (either Cursor or Claude Desktop), not both.

### Installing the Remote Script

1. Copy the `AbletonMCP_Remote_Script` folder to Ableton's MIDI Remote Scripts directory:

   **macOS:**
   - `Contents/App-Resources/MIDI Remote Scripts/` (right-click Ableton app -> Show Package Contents)
   - `/Users/[Username]/Library/Preferences/Ableton/Live XX/User Remote Scripts`

   **Windows:**
   - `C:\Users\[Username]\AppData\Roaming\Ableton\Live x.x.x\Preferences\User Remote Scripts`
   - `C:\ProgramData\Ableton\Live XX\Resources\MIDI Remote Scripts\`
   - `C:\Program Files\Ableton\Live XX\Resources\MIDI Remote Scripts\`

2. Launch Ableton Live
3. Go to Settings/Preferences -> Link, Tempo & MIDI
4. In the Control Surface dropdown, select **AbletonMCP** (or **AbletonMCP_Remote_Script**)
5. Set Input and Output to **None**

### Building from Source

```bash
uv build
```

This generates a `.whl` package in `dist/`. After rebuilding, restart the MCP server for changes to take effect. Remember to also delete `__pycache__/__init__.cpython-*.pyc` in the Ableton Remote Scripts folder when updating the Remote Script.

---

## Example Commands

- "Create an 80s synthwave track"
- "Create a Metro Boomin style hip-hop beat"
- "Create a new MIDI track with a synth bass instrument"
- "Add reverb to my drums"
- "Create a 4-bar MIDI clip with a simple melody"
- "Load a 808 drum rack into the selected track"
- "Add a jazz chord progression to the clip in track 1"
- "Set the tempo to 120 BPM"

---

## Limitations

- VST/AU plugins cannot be loaded directly (Ableton API limitation) — save as Instrument Rack first, then use `list_instrument_rack_presets` + `load_instrument_or_effect`
- Complex arrangements should be broken into smaller steps
- Clip automation works best with MIDI clips and basic device parameters
- Arrangement view is limited: clips can be placed via `duplicate_clip_to_arrangement` but not edited directly in arrangement. Arrangement automation is not supported by Ableton's API
- Extended note properties (probability, velocity_deviation, release_velocity) require Live 11+
- Always save your work before extensive experimentation

## Troubleshooting

- **Connection issues**: Make sure the Remote Script is loaded in Ableton and the MCP server is configured
- **Timeout errors**: Simplify requests or break them into smaller steps
- **Changes not taking effect**: If you edited source code, rebuild the `.whl` with `uv build` — the MCP server runs from the packaged wheel, not source files
- **Remote Script not updating**: Delete `__pycache__/__init__.cpython-*.pyc` in the Ableton MIDI Remote Scripts folder, then reload the control surface
- **Multiple server instances**: Ensure only one MCP server is running at a time
- **Dashboard not loading**: Make sure Claude Desktop is running the latest wheel (check `claude_desktop_config.json`). Restart Claude Desktop after rebuilding. If port 9880 is in use, set `ABLETON_MCP_DASHBOARD_PORT` env var

## Disclaimer

This is a third-party integration and not made by Ableton.
