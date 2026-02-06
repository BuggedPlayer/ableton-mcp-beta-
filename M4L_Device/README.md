# AbletonMCP Max for Live Bridge

This Max for Live (M4L) device provides **optional** deep Live Object Model (LOM) access that extends the standard AbletonMCP Remote Script. When loaded, it enables access to hidden/non-automatable parameters on all Ableton devices (Operator, Wavetable, Simpler, Analog, Drift, etc.).

## What It Adds

| Capability | Without M4L | With M4L |
|---|---|---|
| Public device parameters | Yes (via Remote Script) | Yes |
| Hidden/non-automatable parameters | No | **Yes** |
| All existing MCP tools | Yes | Yes (unchanged) |

## How It Works

The M4L bridge communicates with the MCP server over **native OSC messages** via UDP, running alongside the existing Remote Script on a separate channel:

```
MCP Server
  ├── TCP :9877 → Remote Script (existing)
  └── UDP :9878 / :9879 → M4L Bridge (this device, OSC protocol)
```

The server sends OSC commands (e.g., `/ping`, `/discover_params`) with typed arguments (int, float, string). The M4L device's JavaScript processes them via the Live Object Model and returns base64-encoded JSON responses.

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
   [udpsend localhost 9879]
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
m4l_status()  →  "M4L bridge connected (v1.1.0)"
```

## Available MCP Tools (When Bridge Is Loaded)

| Tool | Description |
|---|---|
| `m4l_status()` | Check bridge connection status |
| `discover_device_params(track, device)` | List ALL parameters (hidden + public) for any device |
| `get_device_hidden_parameters(track, device)` | Get full parameter info including hidden ones |
| `set_device_hidden_parameter(track, device, param_index, value)` | Set any parameter by LOM index |
| `list_instrument_rack_presets()` | List saved Instrument Rack presets (VST/AU workaround) |

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

## Technical Notes

- Communication uses **native OSC messages** over UDP — the MCP server builds OSC packets with typed arguments (int, float, string) and the M4L device parses them via Max's built-in OSC support
- Responses are **base64-encoded JSON** sent back through `udpsend` to avoid issues with Max's message system (which treats curly braces `{}` as special characters)
- The bridge is **device-agnostic** — it works with any Ableton instrument or effect, not just specific ones
- Parameter indices from the LOM may differ between Ableton Live versions — always use `discover_device_params` first
- The bridge does not interfere with the Remote Script — both run simultaneously on separate ports
