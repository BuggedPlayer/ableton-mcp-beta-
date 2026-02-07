# Changelog

All notable changes to AbletonMCP Beta will be documented in this file.

---

## v1.9.0

### New: ASCII Grid Notation (2 tools)
- `clip_to_grid` — read a MIDI clip as ASCII grid notation (auto-detects drum vs melodic)
- `grid_to_clip` — write ASCII grid notation to a MIDI clip (creates clip if needed)

### New: Transport & Recording Controls (10 tools)
- `get_loop_info` — get loop bracket start, end, length, and current playback time
- `get_recording_status` — get armed tracks, record mode, and overdub state
- `set_loop_start` — set loop start position in beats
- `set_loop_end` — set loop end position in beats
- `set_loop_length` — set loop length in beats (adjusts end relative to start)
- `set_playback_position` — move the playhead to a specific beat position
- `set_arrangement_overdub` — enable or disable arrangement overdub mode
- `start_arrangement_recording` — start arrangement recording
- `stop_arrangement_recording` — stop arrangement recording
- `set_metronome` — enable or disable the metronome
- `tap_tempo` — tap tempo (call repeatedly to set tempo by tapping)

### New: Bulk Track Queries (2 tools)
- `get_all_tracks_info` — get information about all tracks at once (bulk query)
- `get_return_tracks_info` — get detailed info about all return tracks (bulk query)

### New: Track Management (5 tools)
- `create_return_track` — create a new return track
- `set_track_color` — set the color of a track (0-69, Ableton's palette)
- `arm_track` — arm a track for recording
- `disarm_track` — disarm a track (disable recording)
- `group_tracks` — group multiple tracks together

### New: Audio Clip Tools (6 tools)
- `get_audio_clip_info` — get audio clip details (warp mode, gain, file path)
- `analyze_audio_clip` — comprehensive audio clip analysis (tempo, warp, sample properties, frequency hints)
- `set_warp_mode` — set warp mode (beats, tones, texture, re_pitch, complex, complex_pro)
- `set_clip_warp` — enable or disable warping for an audio clip
- `reverse_clip` — reverse an audio clip
- `freeze_track` / `unfreeze_track` — freeze/unfreeze tracks to reduce CPU load

### New: Arrangement Editing (4 tools)
- `get_arrangement_clips` — get all clips in arrangement view for a track
- `delete_time` — delete a section of time from the arrangement (shifts everything after)
- `duplicate_time` — duplicate a section of time in the arrangement
- `insert_silence` — insert silence at a position (shifts everything after)

### New: Arrangement Automation (2 tools)
- `create_track_automation` — create automation for a track parameter (arrangement-level)
- `clear_track_automation` — clear automation for a parameter in a time range (arrangement-level)

### New: MIDI & Performance Tools (3 tools)
- `capture_midi` — capture recently played MIDI notes (Live 11+)
- `apply_groove` — apply groove to a MIDI clip
- `get_macro_values` — get current macro knob values for an Instrument Rack

### New: Cached Browser Tree (1 tool)
- `refresh_browser_cache` — force a full re-scan of Ableton's browser tree
- `search_browser` now uses an in-memory cache instead of querying Ableton directly — **instant results, no more timeouts**
- `get_browser_tree` returns cached data with URIs, so Claude can load instruments in fewer steps
- **Background warmup**: on startup, the server scans all 5 browser categories (Instruments, Sounds, Drums, Audio Effects, MIDI Effects) using a BFS walker up to **depth 4** — finds instruments AND their individual presets (e.g. `sounds/Operator/Bass/FM Bass`)
- Cache holds up to **5000 items**, auto-refreshes every **5 minutes**
- Fixes: `search_browser` no longer times out; Claude gets correct URIs instead of guessing wrong ones

### Performance & Code Streamlining
- **BFS queue fix**: Browser cache population now uses `deque.popleft()` (O(1)) instead of `list.pop(0)` (O(n))
- **Eliminated duplicate cache**: Removed unused per-category dict; added `_browser_cache_by_category` index for O(1) filtered search
- **Module-level `_CATEGORY_DISPLAY` constant**: No longer rebuilt on every `search_browser`/`get_browser_tree` call
- **Redundant double-lock removed**: `_get_browser_cache()` no longer acquires the lock twice on cache miss
- **Smarter cache warmup**: Polls for Ableton connection every 0.5s instead of blind 3s sleep — starts scanning as soon as ready
- **UDP drain bounded**: Socket drain loops capped at 100 iterations (was unbounded `while True`)
- **Hot-path logging → DEBUG**: Per-command INFO logs (send, receive, status) downgraded to DEBUG — eliminates 3 I/O calls per tool invocation
- **Lazy `%s` formatting**: ~25 logger calls switched from f-strings to `%s` style — skips string construction when log level is filtered
- **Cheaper dashboard log handler**: Stores lightweight tuples, defers timestamp formatting to when dashboard is actually viewed
- **Dashboard status build**: `top_tools` computed inside the lock — no more full dict copy on every 3s refresh
- **Fixed stale values**: Dashboard `tool_count` and comment updated from 81 → 131
- **Clean import**: `import collections` → `from collections import deque`

### Improvements
- Package renamed to `ableton-mcp-stable` for stable release channel
- Fixed server version detection (`importlib.metadata` now uses correct package name)
- Total tools: 94 -> **131** (+37 new tools)

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
