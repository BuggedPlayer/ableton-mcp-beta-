# AbletonMCP Max for Live Bridge (v2.0.0)

Optional deep Live Object Model (LOM) access that extends the standard AbletonMCP Remote Script. Adds **10 tools** for:

- Hidden/non-automatable parameters on any Ableton device
- Device chain navigation inside Instrument Racks, Audio Effect Racks, and Drum Racks
- Simpler/Sample deep access (markers, warp settings, slices)
- Wavetable modulation matrix control

## What It Adds

| Capability | Without M4L | With M4L |
|---|---|---|
| Public device parameters | Yes | Yes |
| Hidden/non-automatable parameters | No | **Yes** |
| Rack chain navigation | No | **Yes** |
| Simpler sample control | Basic | **Deep** (markers, slices, warp) |
| Wavetable modulation matrix | No | **Yes** |

## How It Works

```
MCP Server
  ├── TCP :9877 → Remote Script (128 tools)
  └── UDP :9878 / :9879 → M4L Bridge (10 tools, OSC protocol)
```

The server sends OSC commands with typed arguments. The M4L device processes them via the Live Object Model and returns URL-safe base64-encoded JSON responses. Large responses (>1.5KB) are automatically chunked into ~3.6KB UDP packets and reassembled by the server.

## Setup Instructions

### Prerequisites

- Ableton Live **Suite** or **Standard + Max for Live** add-on
- AbletonMCP Remote Script already installed and working

### Building the .amxd Device

The `.amxd` device must be built manually in Ableton's Max editor since it cannot be code-generated. Follow these steps:

1. **Open Ableton Live**

2. **Create a new MIDI track** (or use any existing track)

3. **Create a new Max MIDI Effect**:
   - In the browser, go to **Max for Live → Max MIDI Effect**
   - Drag it onto the MIDI track

4. **Open the Max editor** (click the wrench icon on the device)

5. **Build the patch** with these 3 objects connected in order:

   ```
   [udpreceive 9878]
        |
   [js m4l_bridge.js]
        |
   [udpsend 127.0.0.1 9879]
   ```

   To add each object: press **N** to create a new object, type the text (e.g., `udpreceive 9878`), then press Enter. Connect them top-to-bottom with patch cables.

6. **Add the JavaScript file**:
   - Copy `m4l_bridge.js` from this directory to the same folder where your `.amxd` device is saved
   - In the Max editor, the `[js m4l_bridge.js]` object should find it automatically
   - If not, use the Max file browser to locate it

7. **Save the device**:
   - **Lock the patch** first (Cmd+E / Ctrl+E)
   - **File → Save As...** in the Max editor
   - Save as `AbletonMCP_Bridge.amxd` in your User Library
   - Recommended path: `User Library/Presets/MIDI Effects/Max MIDI Effect/`

8. **Close the Max editor**

### Loading the Device

1. Open your Ableton Live project
2. Find `AbletonMCP_Bridge` in your User Library browser
3. Drag it onto **any MIDI track** (it listens globally via UDP — the track doesn't matter)
4. The device will immediately start listening on UDP port 9878

### Verifying the Connection

Use the `m4l_status` MCP tool to check if the bridge is connected:

```
m4l_status()  →  "M4L bridge connected (v2.0.0)"
```

## Available MCP Tools (When Bridge Is Loaded)

### Hidden Parameter Access

| Tool | Description |
|---|---|
| `m4l_status()` | Check bridge connection status |
| `discover_device_params(track, device)` | List ALL parameters (hidden + public) for any device |
| `get_device_hidden_parameters(track, device)` | Get full parameter info including hidden ones |
| `set_device_hidden_parameter(track, device, param_index, value)` | Set any parameter by LOM index |
| `batch_set_hidden_parameters(track, device, params)` | Set multiple hidden params in one call |
| `list_instrument_rack_presets()` | List saved Instrument Rack presets (VST/AU workaround) |

### Device Chain Navigation (v2.0.0)

| Tool | Description |
|---|---|
| `discover_rack_chains(track, device, chain_path?)` | Discover chains, nested devices, and drum pads in Racks. Use `chain_path` (e.g. `"chains 0 devices 0"`) for nested racks |
| `get_chain_device_parameters(track, device, chain, chain_device)` | Read all params of a nested device |
| `set_chain_device_parameter(track, device, chain, chain_device, param, value)` | Set a param on a nested device |

### Simpler / Sample Deep Access (v2.0.0)

| Tool | Description |
|---|---|
| `get_simpler_info(track, device)` | Get Simpler state: playback mode, sample file, markers, warp, slices |
| `set_simpler_sample_properties(track, device, ...)` | Set sample markers, warp mode, gain, etc. |
| `simpler_manage_slices(track, device, action, ...)` | Insert, remove, clear, or reset slices |

### Wavetable Modulation Matrix (v2.0.0)

| Tool | Description |
|---|---|
| `get_wavetable_info(track, device)` | Get oscillator wavetables, mod matrix, unison, filter routing |
| `set_wavetable_modulation(track, device, target, source, amount)` | Set modulation amount (Env2/Env3/LFO1/LFO2 → target) |
| `set_wavetable_properties(track, device, ...)` | Set wavetable selection, effect modes (via M4L). Unison/filter/voice properties are read-only (Ableton API limitation) |

## Troubleshooting

**"M4L bridge not connected"**
- Ensure the AbletonMCP_Bridge device is loaded on a track
- Check that port 9878 is not used by another application
- Make sure the patch is **locked** (not in edit mode) — `udpreceive` may not work while unlocked

**"Timeout waiting for M4L response"**
- The M4L device may be in edit mode — close the Max editor
- Try removing and re-adding the device to the track
- Double-click the `[js m4l_bridge.js]` object to reload the script

**Port conflicts**
- Default ports: 9878 (commands) and 9879 (responses)
- If these conflict with other software, edit the port numbers in:
  - The Max patch objects (`udpreceive` and `udpsend`)
  - `server.py` (`M4LConnection` class: `send_port` and `recv_port`)

## OSC Commands Reference (v2.0.0)

| Address | Arguments | Description |
|---|---|---|
| `/ping` | `request_id` | Health check — returns bridge version |
| `/discover_params` | `track_idx, device_idx, request_id` | Enumerate all LOM parameters |
| `/get_hidden_params` | `track_idx, device_idx, request_id` | Get hidden parameter details |
| `/set_hidden_param` | `track_idx, device_idx, param_idx, value, request_id` | Set a parameter by LOM index |
| `/batch_set_hidden_params` | `track_idx, device_idx, params_b64, request_id` | Set multiple params (chunked, base64 JSON) |
| `/check_dashboard` | `request_id` | Returns dashboard URL and bridge version |
| `/discover_chains` | `track_idx, device_idx, [extra_path], request_id` | Discover rack chains and drum pads. Optional `extra_path` for nested racks |
| `/get_chain_device_params` | `track_idx, device_idx, chain_idx, chain_device_idx, request_id` | Get nested device params |
| `/set_chain_device_param` | `track_idx, device_idx, chain_idx, chain_device_idx, param_idx, value, request_id` | Set nested device param |
| `/get_simpler_info` | `track_idx, device_idx, request_id` | Get Simpler + sample info |
| `/set_simpler_sample_props` | `track_idx, device_idx, props_b64, request_id` | Set sample properties (base64 JSON) |
| `/simpler_slice` | `track_idx, device_idx, action, [slice_time], request_id` | Manage slices |
| `/get_wavetable_info` | `track_idx, device_idx, request_id` | Get Wavetable state + mod matrix |
| `/set_wavetable_modulation` | `track_idx, device_idx, target_idx, source_idx, amount, request_id` | Set mod matrix amount |
| `/set_wavetable_props` | `track_idx, device_idx, props_b64, request_id` | Set Wavetable properties (base64 JSON) |

## Technical Notes

### Communication
- **Protocol**: Native OSC messages over UDP. Server builds typed OSC packets; M4L parses via Max's built-in OSC support.
- **Responses**: URL-safe base64-encoded JSON (`A-Z a-z 0-9 - _` only). Standard base64 `+` and `/` conflict with Max's OSC routing.
- **Device-agnostic**: Works with any Ableton instrument or effect. Always use `discover_device_params` first — LOM indices may vary between Live versions.
- **Non-interfering**: Runs alongside the Remote Script on separate UDP ports.

### Chunked Response Protocol (Rev 4)
Large device discovery (e.g. Wavetable with 93 parameters) produces responses that exceed Max's ~8KB outlet symbol limit and crash Ableton. The bridge handles this automatically:

1. Responses ≤1.5KB JSON are sent directly (backward compatible)
2. Larger responses are split into 2KB raw JSON pieces
3. Each piece is base64-encoded independently with URL-safe conversion
4. Wrapped in a chunk envelope (`{"_c":idx,"_t":total,"_d":"..."}`) and encoded again
5. All chunks sent via deferred `Task.schedule()` with 50ms delays (~3.6KB each)
6. Python server detects chunk metadata, buffers, and reassembles

Key safety: never creates the full base64 string in memory; `.replace()` for URL-safe conversion is O(n) native; no synchronous outlet from discovery callbacks.

### Crash Prevention
- **Chunked async discovery**: Large devices discovered 4 params/chunk with 50ms `Task.schedule()` delays. Prevents synchronous LiveAPI overload (>210 `get()` calls crashes Ableton).
- **LiveAPI cursor reuse**: `discover_rack_chains` uses `goto()` to reuse 3 cursor objects instead of creating ~193 per call. Prevents Max `[js]` memory exhaustion on large drum racks.
- **Fire-and-forget writes**: `set_device_hidden_parameter`, `set_chain_device_parameter`, and `set_wavetable_properties` do not read back after `set()`. Post-set `get("value")` readback was the #1 crash pattern.

### Known Limitations
- **Wavetable voice properties** (`unison_mode`, `unison_voice_count`, `filter_routing`, `mono_poly`, `poly_voices`) are read-only — not exposed as DeviceParameters, and `LiveAPI.set()` silently fails. Hard Ableton API limitation.
