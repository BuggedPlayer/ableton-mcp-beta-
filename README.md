# AbletonMCP - Ableton Live Model Context Protocol Integration

AbletonMCP connects Ableton Live to Claude AI through the Model Context Protocol (MCP), giving Claude direct control over your Live session with **138 tools** across two communication channels.

Based on the original [ableton-mcp](https://github.com/ahujasid/ableton-mcp) by [Siddharth Ahuja](https://x.com/sidahuj).

---

## Architecture

```
Claude AI  <--MCP-->  MCP Server  <--TCP:9877-->  Ableton Remote Script
                          |
                          +------<--UDP/OSC:9878/9879-->  M4L Bridge (optional)
                          |
                          +------<--HTTP:9880-->  Web Status Dashboard
```

- **Remote Script** (TCP) — 128 tools for tracks, clips, MIDI, mixing, automation, browser, snapshots
- **M4L Bridge** (UDP/OSC) — 10 tools for hidden parameters, rack chains, Simpler samples, Wavetable modulation
- **Web Dashboard** — live status, tool metrics, server logs at `http://127.0.0.1:9880`

---

## Tool Reference (138 Tools)

### Session & Transport (7)
`get_session_info` `set_tempo` `start_playback` `stop_playback` `get_song_transport` `set_song_time` `set_song_loop`

### Track Management (10)
`get_track_info` `create_midi_track` `create_audio_track` `set_track_name` `delete_track` `duplicate_track` `get_all_tracks_info` `create_return_track` `set_track_color` `group_tracks`

### Track Mixing (6)
`set_track_volume` `set_track_pan` `set_track_mute` `set_track_solo` `set_track_arm` `set_track_send`

### Clip Management (14)
`create_clip` `get_clip_info` `set_clip_name` `delete_clip` `duplicate_clip` `fire_clip` `stop_clip` `set_clip_looping` `set_clip_loop_points` `set_clip_color` `crop_clip` `duplicate_clip_loop` `set_clip_start_end` `duplicate_clip_to_arrangement`

### MIDI Notes (8)
`add_notes_to_clip` `get_clip_notes` `clear_clip_notes` `quantize_clip_notes` `transpose_clip_notes` `add_notes_extended` `get_notes_extended` `remove_notes_range`

### Automation (4)
`create_clip_automation` `get_clip_automation` `clear_clip_automation` `list_clip_automated_parameters`

### ASCII Grid Notation (2)
`clip_to_grid` `grid_to_clip` — visual drum/melodic pattern editing

### Transport & Recording (11)
`get_loop_info` `get_recording_status` `set_loop_start` `set_loop_end` `set_loop_length` `set_playback_position` `set_arrangement_overdub` `start_arrangement_recording` `stop_arrangement_recording` `set_metronome` `tap_tempo`

### Arrangement Editing (7)
`get_arrangement_clips` `delete_time` `duplicate_time` `insert_silence` `duplicate_clip_to_arrangement` `create_track_automation` `clear_track_automation`

### Audio Clips (7)
`get_audio_clip_info` `analyze_audio_clip` `set_warp_mode` `set_clip_warp` `reverse_clip` `freeze_track` `unfreeze_track`

### MIDI & Performance (3)
`capture_midi` `apply_groove` `get_macro_values`

### Scenes (5)
`get_scenes` `create_scene` `set_scene_name` `fire_scene` `delete_scene`

### Return Tracks (6)
`get_return_tracks` `get_return_track_info` `set_return_track_volume` `set_return_track_pan` `set_return_track_mute` `set_return_track_solo`

### Master Track (2)
`get_master_track_info` `set_master_volume`

### Devices & Parameters (4)
`get_device_parameters` `set_device_parameter` `set_device_parameters` `delete_device`

### Browser & Loading (9)
`get_browser_tree` `get_browser_items_at_path` `search_browser` `refresh_browser_cache` `load_instrument_or_effect` `load_sample` `load_drum_kit` `get_user_library` `get_user_folders`

### Snapshot & Versioning (9)
`snapshot_device_state` `restore_device_snapshot` `list_snapshots` `get_snapshot_details` `delete_snapshot` `delete_all_snapshots` `snapshot_all_devices` `restore_group_snapshot` `compare_snapshots`

### Preset Morph (1)
`morph_between_snapshots` — interpolate between two device states (0.0=A, 1.0=B)

### Smart Macros (4)
`create_macro_controller` `set_macro_value` `list_macros` `delete_macro`

### Preset Generator (1)
`generate_preset` — AI-driven preset creation from text descriptions

### Parameter Mapper (4)
`create_parameter_map` `get_parameter_map` `list_parameter_maps` `delete_parameter_map`

### Rack Presets (1)
`list_instrument_rack_presets`

### M4L: Hidden Parameters (6) *requires M4L device*
`m4l_status` `discover_device_params` `get_device_hidden_parameters` `set_device_hidden_parameter` `batch_set_hidden_parameters` `list_instrument_rack_presets`

### M4L: Device Chain Navigation (3) *requires M4L device*
`discover_rack_chains` `get_chain_device_parameters` `set_chain_device_parameter`

### M4L: Simpler / Sample Access (3) *requires M4L device*
`get_simpler_info` `set_simpler_sample_properties` `simpler_manage_slices`

### M4L: Wavetable Modulation (3) *requires M4L device*
`get_wavetable_info` `set_wavetable_modulation` `set_wavetable_properties`

---

## Installation

### Prerequisites

- Ableton Live 10+ (12 recommended)
- Python 3.10+
- [uv package manager](https://astral.sh/uv)

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
        "AbletonMCP": {
            "command": "uv",
            "args": ["run", "--directory", "C:\\path\\to\\ableton-mcp-stable", "ableton-mcp-stable"]
        }
    }
}
```

### Cursor Integration

Go to Cursor Settings > MCP and add:
```
uv run --directory C:\path\to\ableton-mcp-stable ableton-mcp-stable
```

Only run one instance (either Cursor or Claude Desktop, not both).

### Installing the Remote Script

1. Copy `AbletonMCP_Remote_Script` to Ableton's MIDI Remote Scripts:
   - **macOS:** `/Users/[You]/Library/Preferences/Ableton/Live XX/User Remote Scripts`
   - **Windows:** `C:\Users\[You]\AppData\Roaming\Ableton\Live x.x.x\Preferences\User Remote Scripts`
2. In Ableton: Settings > Link, Tempo & MIDI > Control Surface > **AbletonMCP** > Input/Output: None

### M4L Bridge Setup (Optional)

See [`M4L_Device/README.md`](M4L_Device/README.md) for Max for Live bridge setup instructions.

### Building from Source

```bash
uv build
```

---

## Limitations

- **VST/AU plugins** can't be loaded directly (Ableton API limitation) — save as Instrument Rack preset first. Built-in devices load by name (`"Wavetable"`, `"Reverb"`)
- **Arrangement clips** are read-only after placement — edit in session view, place with `duplicate_clip_to_arrangement`
- **Wavetable voice properties** (`unison_mode`, `poly_voices`, `filter_routing`, etc.) are read-only — not exposed as DeviceParameters
- **Large sessions**: Break complex tasks into smaller tool calls for best results

## Troubleshooting

- **Connection issues**: Ensure Remote Script is loaded and MCP server is running
- **Timeout errors**: Break requests into smaller steps
- **Changes not applied**: Rebuild with `uv build`, restart MCP server
- **Remote Script stale**: Delete `__pycache__/*.pyc` in the Remote Scripts folder
- **Duplicate instances**: Singleton guard on port 9881 prevents conflicts. Set `ABLETON_MCP_LOCK_PORT` env var if stuck
- **Dashboard not loading**: Check `claude_desktop_config.json` path. Set `ABLETON_MCP_DASHBOARD_PORT` if port 9880 is in use
- **M4L not responding**: Reload the `[js]` object in the Max patch, or remove/re-add the M4L device
- **Save your work**: Always save your Live Set before AI-driven experimentation

## Disclaimer

This is a third-party integration and not made by Ableton.
