# AbletonMCP Max for Live Bridge (v2.0.0)

This Max for Live (M4L) device provides **optional** deep Live Object Model (LOM) access that extends the standard AbletonMCP Remote Script. When loaded, it enables:

- Access to hidden/non-automatable parameters on all Ableton devices
- Device chain navigation inside Instrument Racks, Audio Effect Racks, and Drum Racks
- Simpler/Sample deep access (markers, warp settings, slices)
- Wavetable modulation matrix control

## What It Adds

| Capability | Without M4L | With M4L |
|---|---|---|
| Public device parameters | Yes (via Remote Script) | Yes |
| Hidden/non-automatable parameters | No | **Yes** |
| Rack chain navigation | No | **Yes** (nested device read/write) |
| Simpler sample control | Basic (via Remote Script) | **Deep** (markers, slices, warp) |
| Wavetable modulation matrix | No | **Yes** (mod sources → targets) |
| Snapshots/Morph/Macros | Yes (via TCP since v2.0.0) | Yes |
| All existing MCP tools | Yes | Yes (unchanged) |

## How It Works

The M4L bridge communicates with the MCP server over **native OSC messages** via UDP, running alongside the existing Remote Script on a separate channel:

```
MCP Server
  ├── TCP :9877 → Remote Script (existing)
  └── UDP :9878 / :9879 → M4L Bridge (this device, OSC protocol)
```

The server sends OSC commands with typed arguments (int, float, string). The M4L device's JavaScript processes them via the Live Object Model and returns base64-encoded JSON responses.

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

- Communication uses **native OSC messages** over UDP — the MCP server builds OSC packets with typed arguments (int, float, string) and the M4L device parses them via Max's built-in OSC support
- Responses are **base64-encoded JSON** sent back through `udpsend` to avoid issues with Max's message system (which treats curly braces `{}` as special characters)
- The bridge is **device-agnostic** — it works with any Ableton instrument or effect, not just specific ones
- Parameter indices from the LOM may differ between Ableton Live versions — always use `discover_device_params` first
- The bridge does not interfere with the Remote Script — both run simultaneously on separate ports
- **v2.0.0**: Chain navigation uses `LiveAPI` with paths like `live_set tracks T devices D chains C devices CD` for nested access
- **v2.0.0**: Chain discovery uses `LiveAPI.goto()` to reuse cursor objects instead of creating `new LiveAPI()` per iteration — keeps total at 3 objects vs ~193 for a 16-pad drum rack, preventing Max `[js]` memory exhaustion
- **v2.0.0**: `discover_rack_chains` accepts optional `chain_path` (e.g. `"chains 0 devices 0"`) to navigate into nested racks
- **v2.0.0**: Simpler sample access uses path `live_set tracks T devices D sample` for LOM Sample object
- **v2.0.0**: Wavetable modulation uses `deviceApi.call("get_modulation_value", target, source)` and `set_modulation_value`
- **v2.0.0**: Wavetable `LiveAPI.set()` works for oscillator properties (category, index, effect_mode) but silently fails for voice/unison/filter properties (`unison_mode`, `unison_voice_count`, `filter_routing`, `mono_poly`, `poly_voices`). These properties are NOT exposed as DeviceParameters either (verified against full 93-parameter list), so TCP `set_device_parameter` cannot write them. This is a confirmed hard Ableton API limitation — these properties are read-only via all available APIs.
- **v2.0.0**: `setHiddenParam()` uses fire-and-forget `set()` — no post-set `get("value")` readback, which can crash Ableton.
