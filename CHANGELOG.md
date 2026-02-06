# Changelog

All notable changes to AbletonMCP Beta will be documented in this file.

---

## v1.8.3

### Architecture: Modular Remote Script
- **Refactored** `AbletonMCP_Remote_Script/__init__.py` from a ~3000-line monolith into a thin routing layer (~660 lines) with 11 handler modules under `handlers/`:
  - `session.py` — session info, transport, loop, recording, metronome
  - `tracks.py` — track CRUD, arm, color, group
  - `clips.py` — clip CRUD, notes, fire/stop, loop, markers
  - `mixer.py` — volume, pan, mute, solo, sends, return/master tracks, scenes
  - `devices.py` — device parameters with `track_type` support, macros
  - `browser.py` — browser tree, search, load instruments/samples
  - `scenes.py` — scene CRUD, fire, rename
  - `arrangement.py` — arrangement clip operations
  - `audio.py` — audio clip info, warp, reverse, analyze, freeze
  - `midi.py` — MIDI notes (legacy + extended), quantize, transpose, capture, groove
  - `automation.py` — clip/track automation, arrangement time editing
- Each handler uses standalone functions `f(song, ..., ctrl=None)` — testable without the ControlSurface class

### New: Grid Notation System
- `clip_to_grid` — read a MIDI clip and display as ASCII grid notation (auto-detects drum vs melodic)
- `grid_to_clip` — write ASCII drum/melodic grid patterns to a MIDI clip
- Supports drum labels (KK, SN, HC, etc.) and melodic note names (C4, G#3, etc.)
- Velocity symbols: `o` (normal), `O` (accent), `.` (ghost), `x` (hi-hat)

### New: 36 MCP Tools
- **Session/Transport (11)**: `get_loop_info`, `get_recording_status`, `set_loop_start`, `set_loop_end`, `set_loop_length`, `set_playback_position`, `set_arrangement_overdub`, `start_arrangement_recording`, `stop_arrangement_recording`, `set_metronome`, `tap_tempo`
- **Tracks (7)**: `get_all_tracks_info`, `get_return_tracks_info`, `create_return_track`, `set_track_color`, `arm_track`, `disarm_track`, `group_tracks`
- **Audio (7)**: `get_audio_clip_info`, `analyze_audio_clip`, `set_warp_mode`, `set_clip_warp`, `reverse_clip`, `freeze_track`, `unfreeze_track`
- **Grid (2)**: `clip_to_grid`, `grid_to_clip`
- **MIDI (2)**: `capture_midi`, `apply_groove`
- **Arrangement (4)**: `get_arrangement_clips`, `delete_time`, `duplicate_time`, `insert_silence`
- **Automation (2)**: `create_track_automation`, `clear_track_automation`
- **Devices (1)**: `get_macro_values`

### Improvements
- **Connection stability**: Added 0.1s delays before/after modifying commands to prevent race conditions with Ableton's internal processing. Modifying commands use 15s timeout, read-only commands use 10s.
- **`track_type` support**: `get_device_parameters` and `set_device_parameter` now accept `track_type` ("track", "return", "master") to control devices on return and master tracks.
- Total tools: 94 -> **130** (+36 new tools)

---

## v1.8.2

### Bug Fix: `batch_set_hidden_parameters` crash
- **Fixed**: `batch_set_hidden_parameters` was crashing Ableton when setting more than 2 parameters. The root cause was Max's OSC/UDP handling corrupting long base64-encoded payloads.
- **Server fix** (`server.py`): Replaced the single base64-encoded batch OSC message with sequential individual `set_hidden_param` UDP calls via a new `_m4l_batch_set_params()` helper. Includes 50ms inter-param delay for large batches to prevent overloading Ableton.
- **M4L fix** (`m4l_bridge.js`): Added chunked processing (6 params/chunk, 50ms delay) using Max's `Task` scheduler, URL-safe base64 decode support, and debug logging.
- **Safety**: Both server and M4L bridge now filter out parameter index 0 ("Device On") to prevent accidentally disabling devices during batch operations.
- **Dynamic timeout**: M4L `send_command` timeout now scales with parameter count (~150ms per param, minimum 10s) instead of a fixed 5s.
- Updated all internal callers: `restore_device_snapshot`, `restore_group_snapshot`, `morph_between_snapshots`, `set_macro_value`.
- Total tools: **94** (unchanged)

---

## v1.8.1

### Repository Cleanup & Documentation
- Removed stale development files: `Ideas.txt`, `todo.txt`, `lastlog.txt`, `WhatItCanDoAndWhatItCant.txt`, `Installing process.txt`, `Latest bugfix.txt`
- Added `installation_process.txt` — comprehensive step-by-step installation guide covering Windows, macOS, Claude Desktop, Cursor, Smithery, and source installs
- Added `requirements.txt` — explicit dependency listing for pip-based installs
- Updated `M4Lfunctions.txt` — expanded M4L bridge capabilities documentation with practical examples
- Normalized line endings across `server.py`, `__init__.py`, and `README.md`

### No Code Changes
- `MCP_Server/server.py` — identical functionality to v1.8.0
- `AbletonMCP_Remote_Script/__init__.py` — identical functionality to v1.8.0
- Total tools: **94** (unchanged)

---

## v1.8.0

### New: Arrangement View Workflow
- `get_song_transport` — get arrangement state (playhead, tempo, time signature, loop bracket, record mode, song length)
- `set_song_time` — set arrangement playhead position (in beats)
- `set_song_loop` — control arrangement loop bracket (enable/disable, set start/length)
- `duplicate_clip_to_arrangement` — copy session clip to arrangement timeline at beat position (Live 11+)

### New: Advanced Clip Operations
- `crop_clip` — trim clip to its loop region, discarding content outside
- `duplicate_clip_loop` — double the loop content (e.g. 4 bars -> 8 bars with content repeated)
- `set_clip_start_end` — control playback start/end markers without modifying notes

### New: Advanced MIDI Note Editing (Live 11+)
- `add_notes_extended` — add notes with probability, velocity_deviation, release_velocity
- `get_notes_extended` — get notes with extended properties
- `remove_notes_range` — selectively remove notes by time and pitch range

### New: Automation Reading & Editing
- `get_clip_automation` — read existing envelope data by sampling 64 points across clip
- `clear_clip_automation` — remove automation for a specific parameter
- `list_clip_automated_parameters` — discover all automated parameters in a clip

### Improvements
- Automation is no longer write-only; now supports reading, clearing, and discovering automated parameters
- Graceful fallback to legacy APIs on older Live versions
- Total tools: 81 -> **94** (+13 new tools)

---

## v1.7.1

### Bug Fixes
- Fixed log handler: timestamp field now only contains timestamp (was duplicating full formatted line in log viewer)

### Improvements
- Added status banner to web dashboard: green (all connected), yellow (Ableton only), red (disconnected)

---

## v1.7.0

### Maintenance
- Version bump to bypass uvx wheel cache (uvx was caching the first v1.6.0 wheel, preventing M4L auto-connect fixes from being picked up)
- No new features

---

## v1.6.0

### New: Layer 0 Core Primitives
- `batch_set_hidden_parameters` — set multiple device params in one M4L round-trip
- `snapshot_device_state` / `restore_device_snapshot` — capture and recall full device states
- `list_snapshots` / `delete_snapshot` / `get_snapshot_details` / `delete_all_snapshots`

### New: Device State Versioning & Undo
- `snapshot_all_devices` — capture all devices across multiple tracks as a group
- `restore_group_snapshot` — restore entire device groups at once
- `compare_snapshots` — diff two snapshots showing changed parameters with deltas

### New: Preset Morph Engine
- `morph_between_snapshots` — interpolate between two device states (0.0 = A, 1.0 = B); quantized params snap at midpoint

### New: Smart Macro Controller
- `create_macro_controller` / `set_macro_value` / `list_macros` / `delete_macro` — link multiple device parameters to a single 0.0-1.0 control

### New: Intelligent Preset Generator
- `generate_preset` — discover all params + auto-snapshot current state for AI-driven preset creation

### New: VST/AU Parameter Mapper
- `create_parameter_map` / `get_parameter_map` / `list_parameter_maps` / `delete_parameter_map` — map cryptic parameter names to friendly names with categories

### Improvements
- M4L bridge: added `batch_set_hidden_params` OSC command with base64-encoded JSON
- Total tools: 61 -> **81** (+20 new tools)

---

## v1.5.1

### Rebrand
- Renamed from "ableton-mcp" to "AbletonMCP Beta"
- Comprehensive README rewrite with full tool reference and architecture documentation

---

## v1.5.0

### Initial Full Release
- M4L bridge integration for hidden/non-automatable parameter access
- Bug fixes and stability improvements
- Live 12 compatibility
- 61 MCP tools
