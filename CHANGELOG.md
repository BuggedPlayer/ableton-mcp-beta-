# Changelog

All notable changes to AbletonMCP Beta will be documented in this file.

---

## v2.0.0

### New: Device Chain Navigation (3 tools, requires M4L)
- `discover_rack_chains` — discover chains, nested devices, and drum pads inside Instrument/Audio Effect/Drum Racks
- `get_chain_device_parameters` — read all parameters of a device nested inside a rack chain
- `set_chain_device_parameter` — set a parameter on a device nested inside a rack chain
- LOM paths: `live_set tracks T devices D chains C devices CD`

### New: Simpler / Sample Deep Access (3 tools, requires M4L)
- `get_simpler_info` — get Simpler device state: playback mode, sample file path, markers, warp settings, slices, warp-mode-specific properties
- `set_simpler_sample_properties` — set sample start/end markers, warping, warp mode, gain, slicing sensitivity
- `simpler_manage_slices` — manage slices: insert at position, remove at position, clear all, reset to auto-detected
- LOM paths: `live_set tracks T devices D sample`

### New: Wavetable Modulation Matrix (3 tools, requires M4L)
- `get_wavetable_info` — get Wavetable device state: oscillator wavetable categories/indices, modulation matrix with active modulations, voice/unison/filter settings
- `set_wavetable_modulation` — set modulation amount in Wavetable's mod matrix (sources: Env2, Env3, LFO1, LFO2)
- `set_wavetable_properties` — set oscillator wavetable category/index, effect modes. Voice/unison/filter properties are read-only (Ableton API limitation)

### M4L Bridge v2.0.0
- **9 new OSC commands**: `/discover_chains`, `/get_chain_device_params`, `/set_chain_device_param`, `/get_simpler_info`, `/set_simpler_sample_props`, `/simpler_slice`, `/get_wavetable_info`, `/set_wavetable_modulation`, `/set_wavetable_props`
- **Generic LOM helper**: `discoverParamsAtPath()` enables parameter discovery at arbitrary LOM paths (used by chain device params)
- **LiveAPI cursor reuse**: `discoverChainsAtPath()` uses `LiveAPI.goto()` to reuse 3 cursor objects instead of creating ~193 per call — prevents Max `[js]` memory exhaustion on large drum racks
- **OSC packet builders**: All 9 new commands have corresponding builders in `M4LConnection._build_osc_packet()`

### TCP Port: Snapshot / Restore / Morph / Macros
- **`snapshot_device_state`** — ported from M4L `discover_params` to TCP `get_device_parameters`. No longer requires M4L bridge.
- **`restore_device_snapshot`** — ported from `_m4l_batch_set_params()` to `_tcp_batch_restore_params()` using name-based parameters. No M4L needed.
- **`snapshot_all_devices`** — ported to TCP. Snapshots all devices across tracks without M4L.
- **`restore_group_snapshot`** — ported to TCP.
- **`morph_between_snapshots`** — ported to TCP. Now uses name-based parameter matching instead of index-based.
- **`set_macro_value`** — ported to TCP. Auto-looks up parameter names from device if not cached.
- **`generate_preset`** — fully rewritten to use TCP. No longer calls M4L `discover_params` (which caused timeouts and crashes).
- **New helper**: `_tcp_batch_restore_params()` — restores device parameters via TCP `set_device_parameters_batch` using name-based params.

### Removed Redundant Tools (-3)
- **`arm_track`** — use `set_track_arm(arm=True)` instead
- **`disarm_track`** — use `set_track_arm(arm=False)` instead
- **`get_return_tracks_info`** — use `get_return_tracks` instead

### Bug Fixes & Improvements
- **Fixed `set_wavetable_properties` crash**: removed post-set `get()` read-back verification that crashed Ableton. Now uses fire-and-forget `set()` for oscillator properties via M4L
- **Fixed `set_device_hidden_parameter` crash**: removed post-set `paramApi.get("value")` readback in `setHiddenParam()` — same crash pattern as the wavetable fix. Now reports clamped value instead of reading back
- **Confirmed Wavetable voice properties read-only**: `unison_mode`, `unison_voice_count`, `filter_routing`, `mono_poly`, `poly_voices` are NOT exposed as DeviceParameters (verified against full 93-parameter list). Neither M4L `LiveAPI.set()` nor TCP `set_device_parameter` can write them — hard Ableton API limitation. `set_wavetable_properties` now returns a clear error message for these
- **Fixed `discover_rack_chains` nested rack support**: added optional `chain_path` parameter to target devices inside chains (e.g. `"chains 0 devices 0"` for nested racks)
- **Fixed `discover_rack_chains` crash on large drum racks**: refactored `discoverChainsAtPath` to reuse LiveAPI objects via `goto()` instead of creating ~193 new objects per call. Now uses 3 cursor objects total, preventing Max `[js]` memory exhaustion
- **Fixed `discover_device_params` crash on large devices** (e.g. Wavetable with 93 params): two root causes found and fixed:
  1. **Synchronous LiveAPI overload**: >~210 `get()` calls in a single [js] execution crashes Ableton. Fixed by chunked async discovery (4 params/chunk with 50ms `Task.schedule()` delays)
  2. **Response size through outlet/udpsend**: >~8KB base64 via Max `outlet()` crashes Ableton (symbol size limit + OSC routing issues with `+` and `/` characters in standard base64). Fixed by chunked response protocol (Rev 4): JSON is split into 2KB pieces, each base64-encoded independently with URL-safe conversion (`+`→`-`, `/`→`_`), wrapped in a chunk envelope, and sent via deferred `Task.schedule()`. Python server detects chunk metadata (`_c`/`_t` keys), buffers all pieces, decodes each, and reassembles the full JSON
- **Chunked response protocol**: M4L bridge splits large responses into multiple ~3.6KB UDP packets. Python server reassembles automatically. Small responses sent as-is (backward compatible). Key safety: never creates the full base64 string in memory, uses `.replace()` for O(n) URL-safe conversion, defers all outlet() calls via Task.schedule()
- **Fixed `set_chain_device_parameter` crash**: removed post-set `paramApi.get("value")` readback in `handleSetChainDeviceParam()` — same crash pattern as wavetable and hidden param fixes
- **Fixed `batch_set_hidden_parameters` LiveAPI exhaustion**: refactored `_batchProcessNextChunk()` to reuse a single cursor via `goto()` instead of creating new LiveAPI per parameter (93 objects → 1)
- **Fixed Remote Script crash on client disconnect**: wrapped `client.sendall()` response send in try/except to handle broken connections cleanly instead of propagating the error
- **Fixed `grid_to_clip` silent failures**: `except Exception: pass` replaced with proper error returns
- **Fixed `generate_preset` device targeting**: improved docstring guidance to target synth, not effects
- **Reduced bruteforce resolver logging**: removed per-iteration logging from `devices.py` — only MATCH and ERROR logged now
- **Improved documentation**: Moved automation and extended note features from Limitations to Features — these are capabilities, not limitations. Fixed `create_clip_automation` docstring that incorrectly said "arrangement automation is not supported"
- Total tools: 132 -> **138** (+9 new, -3 removed)

---

## v1.9.1

### New: Batch Parameter Setting (1 tool)
- `set_device_parameters` — set multiple device parameters in a single call (JSON array of `{name, value}` pairs). Replaces 20+ individual `set_device_parameter` calls with one round-trip. Essential for sound design tasks like creating pads, leads, etc.

### New: Dynamic Device URI Map
- **Automatic device name resolution**: say `"load Reverb on track 3"` and the server resolves the name to the correct browser URI (`query:AudioFx#Reverb`) instantly — no `search_browser` needed
- `_device_uri_map` built dynamically from browser cache after each scan (5,118 device names mapped)
- Collision resolution: Instruments > Audio Effects > MIDI Effects > Max for Live > Plug-ins > Drums > User Library
- `_resolve_device_uri()` does O(1) lookup, waits for warmup thread if map is empty

### New: Disk Browser Cache
- Browser cache is now **persisted to disk** at `~/.ableton-mcp/browser_cache.json` after each successful scan
- On startup, disk cache is loaded **instantly** (~50ms) before Ableton even connects — `search_browser` and device loading work immediately
- Background refresh still runs to keep cache fresh and re-saves to disk
- 24-hour staleness limit — disk cache is ignored if older than 1 day
- Atomic writes via temp file + `os.replace()` to prevent corruption
- `refresh_browser_cache` updates both memory and disk

### New: Singleton Guard
- **Prevents duplicate MCP server instances** — uses exclusive TCP port lock on 9881 with `SO_EXCLUSIVEADDRUSE`
- Second instance exits immediately with clear error message instead of silently fighting the first

### Browser Cache Improvements
- **Expanded categories**: now scans 7 browser categories (was 5): Instruments, Drums, Audio Effects, MIDI Effects, Max for Live, Plug-ins, User Library
- **Removed non-device categories**: Sounds, Clips, Samples, Packs no longer scanned (not useful for device loading, were slowing scan)
- **Per-category item cap**: 1,500 items per category (was 5,000 shared across all — Instruments used to consume entire budget)
- **BFS depth reduced**: depth 3 (was 4) — gets device categories without individual preset files
- **Rate limiting**: 50ms delay between BFS commands to prevent socket flooding
- **60-second timeout** for browser scan commands (was 10s — Samples root was timing out)
- **Reconnect resilience**: if connection drops mid-scan, waits 2s, reconnects, and continues instead of crashing
- **Duplicate scan prevention**: `_browser_cache_populating` flag prevents warmup thread and `_resolve_device_uri` from triggering concurrent scans
- **5-second warmup delay**: gives Remote Script time to fully initialize before opening second TCP connection

### Bug Fixes
- **Fixed socket concurrency crash**: browser cache scan used shared global TCP connection, corrupting it for other tools. Now uses dedicated `AbletonConnection` with proper `try/finally` cleanup
- **Fixed `_browser_cache_populating` flag not resetting** on early connection failure (was getting stuck forever, blocking all future scans)
- **Fixed hardcoded device URIs**: replaced `_WELL_KNOWN_DEVICES` dict (which had wrong URIs like `query:Audio%20Effects#Reverb`) with dynamic map from actual Ableton browser cache
- **Fixed M4L socket binding**: replaced `SO_REUSEADDR` with `SO_EXCLUSIVEADDRUSE` to prevent port sharing between instances

### Performance
- Browser cache scan: ~2 minutes (was ~3.5 min with old categories, was ~100s with duplicate scans)
- **With disk cache: instant startup** — 0ms wait for search/device loading (was 2-3.5 minutes on first use)
- Total: 6,473 items cached, 5,118 device names mapped

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
- Total tools: 94 -> **131** (+37 new tools, see v1.9.1 for +1 more)

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
