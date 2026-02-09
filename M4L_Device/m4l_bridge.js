/**
 * AbletonMCP Beta — M4L Bridge — m4l_bridge.js
 *
 * This script runs inside a Max for Live [js] object and provides
 * deep Live Object Model (LOM) access for the AbletonMCP Beta server.
 *
 * Communication uses native OSC messages via udpreceive/udpsend:
 *   - The MCP server sends OSC messages like /ping, /discover_params, etc.
 *   - Max's udpreceive parses OSC and sends the address + args to this [js]
 *   - Responses are base64-encoded JSON sent back via outlet → udpsend
 *
 * The Max patch needs:
 *   [udpreceive 9878] → [js m4l_bridge.js] → [udpsend 127.0.0.1 9879]
 */

// Max [js] object configuration
inlets  = 1;
outlets = 1;

// ---------------------------------------------------------------------------
// Initialization
// ---------------------------------------------------------------------------
function loadbang() {
    post("AbletonMCP Beta M4L Bridge v2.0.0 starting...\n");
    post("Listening for OSC commands on port 9878.\n");
    post("Dashboard: http://127.0.0.1:9880\n");
}

// ---------------------------------------------------------------------------
// OSC message routing
//
// Max's udpreceive outputs OSC addresses as message names to the [js] object.
// The OSC address "/ping" arrives with messagename = "/ping" (with slash).
// Since "/ping" is not a valid JS function name, everything lands in
// anything(). We route based on messagename.
// ---------------------------------------------------------------------------
function anything() {
    var args = arrayfromargs(arguments);
    var addr = messagename;

    // Strip leading slash if present (Max keeps it from OSC addresses)
    var cmd = addr;
    if (cmd.charAt(0) === "/") {
        cmd = cmd.substring(1);
    }

    switch (cmd) {

        case "ping":
            handlePing(args);
            break;

        case "discover_params":
            handleDiscoverParams(args);
            break;

        case "get_hidden_params":
            handleGetHiddenParams(args);
            break;

        case "set_hidden_param":
            handleSetHiddenParam(args);
            break;

        case "batch_set_hidden_params":
            handleBatchSetHiddenParams(args);
            break;

        case "check_dashboard":
            handleCheckDashboard(args);
            break;

        // --- Phase 2: Device Chain Navigation ---
        case "discover_chains":
            handleDiscoverChains(args);
            break;

        case "get_chain_device_params":
            handleGetChainDeviceParams(args);
            break;

        case "set_chain_device_param":
            handleSetChainDeviceParam(args);
            break;

        // --- Phase 3: Simpler/Sample Deep Access ---
        case "get_simpler_info":
            handleGetSimplerInfo(args);
            break;

        case "set_simpler_sample_props":
            handleSetSimplerSampleProps(args);
            break;

        case "simpler_slice":
            handleSimplerSlice(args);
            break;

        // --- Phase 4: Wavetable Modulation ---
        case "get_wavetable_info":
            handleGetWavetableInfo(args);
            break;

        case "set_wavetable_modulation":
            handleSetWavetableModulation(args);
            break;

        case "set_wavetable_props":
            handleSetWavetableProps(args);
            break;

        // --- Diagnostic ---
        case "probe_device_info":
            handleProbeDeviceInfo(args);
            break;

        // --- Device Property Access ---
        case "get_device_property":
            handleGetDeviceProperty(args);
            break;

        case "set_device_property":
            handleSetDeviceProperty(args);
            break;

        default:
            post("AbletonMCP Beta Bridge: unknown command: '" + cmd + "' (raw: '" + addr + "')\n");
            break;
    }
}

// ---------------------------------------------------------------------------
// Command handlers — each receives native OSC-typed arguments
// ---------------------------------------------------------------------------

function handlePing(args) {
    // args: [request_id (string)]
    var requestId = (args.length > 0) ? args[0].toString() : "";
    var response = {
        status: "success",
        result: { m4l_bridge: true, version: "2.0.0" },
        id: requestId
    };
    sendResponse(JSON.stringify(response));
}

function handleDiscoverParams(args) {
    // args: [track_index (int), device_index (int), request_id (string)]
    if (args.length < 3) {
        sendError("discover_params requires track_index, device_index, request_id", "");
        return;
    }
    var trackIdx  = parseInt(args[0]);
    var deviceIdx = parseInt(args[1]);
    var requestId = args[2].toString();

    // Use chunked async discovery to avoid crashing Ableton.
    // Synchronous iteration of 40+ params with full readParamInfo() (7 get()
    // calls each) exceeds Max [js] scheduler tolerance and crashes.
    _startChunkedDiscover(trackIdx, deviceIdx, requestId);
}

function handleGetHiddenParams(args) {
    // args: [track_index (int), device_index (int), request_id (string)]
    if (args.length < 3) {
        sendError("get_hidden_params requires track_index, device_index, request_id", "");
        return;
    }
    var trackIdx  = parseInt(args[0]);
    var deviceIdx = parseInt(args[1]);
    var requestId = args[2].toString();

    _startChunkedDiscover(trackIdx, deviceIdx, requestId);
}

function handleSetHiddenParam(args) {
    // args: [track_index (int), device_index (int), parameter_index (int), value (float), request_id (string)]
    if (args.length < 5) {
        sendError("set_hidden_param requires track_index, device_index, parameter_index, value, request_id", "");
        return;
    }
    var trackIdx  = parseInt(args[0]);
    var deviceIdx = parseInt(args[1]);
    var paramIdx  = parseInt(args[2]);
    var value     = parseFloat(args[3]);
    var requestId = args[4].toString();

    var result = setHiddenParam(trackIdx, deviceIdx, paramIdx, value);
    sendResult(result, requestId);
}

// ---------------------------------------------------------------------------
// Chunked parameter discovery
//
// Reading all parameters for a large device (e.g. Wavetable, 93 params) in a
// single synchronous call crashes Ableton — the Max [js] scheduler can't
// handle ~280+ LiveAPI get() calls without yielding.  Threshold is around
// 30 params × 7 get() calls = ~210 calls.
//
// Solution: process params in small chunks with deferred callbacks between
// them, same pattern as batch_set_hidden_params.
// ---------------------------------------------------------------------------
var DISCOVER_CHUNK_SIZE = 4;    // params per chunk (4 × 7 gets = 28 — well under limit)
var DISCOVER_CHUNK_DELAY = 50;  // ms between chunks

var _discoverState = null;

function _startChunkedDiscover(trackIdx, deviceIdx, requestId) {
    var devicePath = "live_set tracks " + trackIdx + " devices " + deviceIdx;
    _startChunkedDiscoverAtPath(devicePath, requestId);
}

function _startChunkedDiscoverAtPath(devicePath, requestId) {
    if (_discoverState) {
        sendResult({ error: "Discovery busy - try again shortly" }, requestId);
        return;
    }

    var cursor = new LiveAPI(null, devicePath);

    if (!cursor || !cursor.id || parseInt(cursor.id) === 0) {
        sendResult({ error: "No device found at path: " + devicePath }, requestId);
        return;
    }

    var deviceName  = cursor.get("name").toString();
    var deviceClass = cursor.get("class_name").toString();
    var paramCount  = parseInt(cursor.getcount("parameters"));

    _discoverState = {
        devicePath:  devicePath,
        deviceName:  deviceName,
        deviceClass: deviceClass,
        paramCount:  paramCount,
        cursor:      cursor,
        idx:         0,
        parameters:  [],
        requestId:   requestId
    };

    // Start processing the first chunk
    _discoverNextChunk();
}

function _discoverNextChunk() {
    if (!_discoverState) return;

    var s = _discoverState;
    try {
        var end = Math.min(s.idx + DISCOVER_CHUNK_SIZE, s.paramCount);

        for (var i = s.idx; i < end; i++) {
            s.cursor.goto(s.devicePath + " parameters " + i);

            if (!s.cursor.id || parseInt(s.cursor.id) === 0) {
                continue;
            }

            var paramInfo = readParamInfo(s.cursor, i);
            s.parameters.push(paramInfo);
        }

        s.idx = end;

        if (s.idx >= s.paramCount) {
            // All chunks done — clean up cursor and send response
            s.cursor.goto(s.devicePath);

            sendResult({
                device_name:     s.deviceName,
                device_class:    s.deviceClass,
                parameter_count: s.parameters.length,
                parameters:      s.parameters
            }, s.requestId);
            _discoverState = null;
        } else {
            // Schedule the next chunk after a short delay
            var t = new Task(_discoverNextChunk);
            t.schedule(DISCOVER_CHUNK_DELAY);
        }
    } catch (e) {
        var rid = s.requestId;
        try { s.cursor.goto(s.devicePath); } catch (ignore) {}
        _discoverState = null;
        sendError("Discovery failed at param " + s.idx + ": " + e.toString(), rid);
    }
}

// ---------------------------------------------------------------------------
// Batch set: chunked processing to avoid freezing Ableton
//
// Instead of setting all parameters in one synchronous loop (which can
// crash Ableton when there are 50-90+ params), we process them in small
// chunks with a deferred callback between each chunk.  This yields control
// back to Ableton's main thread so it can update the UI and stay alive.
// ---------------------------------------------------------------------------
var BATCH_CHUNK_SIZE = 6;     // params per chunk — keep small to stay safe
var BATCH_CHUNK_DELAY = 50;   // ms between chunks

// Persistent state for the current batch operation
var _batchState = null;

function handleBatchSetHiddenParams(args) {
    // args: [track_index (int), device_index (int), params_json_b64 (string), request_id (string)]
    if (_batchState) {
        var rid = args.length > 0 ? args[args.length - 1].toString() : "";
        sendError("Batch operation busy - try again shortly", rid);
        return;
    }
    if (args.length < 4) {
        sendError("batch_set_hidden_params requires track_index, device_index, params_json_b64, request_id", "");
        return;
    }
    var trackIdx  = parseInt(args[0]);
    var deviceIdx = parseInt(args[1]);

    // Max's udpreceive may split long OSC string arguments across multiple
    // args.  Reassemble: everything between the two int args and the last
    // arg (request_id) is the base64 payload.
    var requestId = args[args.length - 1].toString();
    var b64Parts = [];
    for (var a = 2; a < args.length - 1; a++) {
        b64Parts.push(args[a].toString());
    }
    var paramsB64 = b64Parts.join("");

    post("batch_set: args.length=" + args.length + " b64len=" + paramsB64.length + "\n");

    // Decode the base64-encoded JSON parameter array
    var paramsJson;
    try {
        paramsJson = _base64decode(paramsB64);
    } catch (e) {
        sendError("Failed to decode params_json_b64: " + e.toString(), requestId);
        return;
    }
    post("batch_set: decoded json len=" + paramsJson.length + "\n");

    var paramsList;
    try {
        paramsList = JSON.parse(paramsJson);
    } catch (e) {
        sendError("Failed to parse params JSON: " + e.toString(), requestId);
        return;
    }

    if (!paramsList || !paramsList.length) {
        sendError("params list is empty", requestId);
        return;
    }

    var devicePath = "live_set tracks " + trackIdx + " devices " + deviceIdx;
    var deviceApi  = new LiveAPI(null, devicePath);

    if (!deviceApi || !deviceApi.id || parseInt(deviceApi.id) === 0) {
        sendError("No device found at track " + trackIdx + " device " + deviceIdx, requestId);
        return;
    }

    // Filter out parameter index 0 ("Device On") to avoid accidentally
    // disabling the device — a common cause of unexpected behavior.
    var safeParams = [];
    var skippedDeviceOn = false;
    for (var i = 0; i < paramsList.length; i++) {
        if (parseInt(paramsList[i].index) === 0) {
            skippedDeviceOn = true;
            continue;
        }
        safeParams.push(paramsList[i]);
    }

    if (safeParams.length === 0) {
        sendResult({
            params_set: 0,
            params_failed: 0,
            total_requested: paramsList.length,
            skipped_device_on: skippedDeviceOn,
            message: "No settable parameters after filtering."
        }, requestId);
        return;
    }

    // Initialize chunked batch state
    // paramCursor: reusable LiveAPI object navigated via goto() — avoids creating
    // N new LiveAPI objects (same fix as discoverParams/discoverChainsAtPath)
    _batchState = {
        devicePath:  devicePath,
        paramsList:  safeParams,
        requestId:   requestId,
        cursor:      0,
        paramCursor: new LiveAPI(null, devicePath),
        okCount:     0,
        failCount:   0,
        errors:      [],
        skippedDeviceOn: skippedDeviceOn,
        totalRequested:  paramsList.length
    };

    // Start processing the first chunk
    _batchProcessNextChunk();
}

function _batchProcessNextChunk() {
    if (!_batchState) return;

    var s = _batchState;
    var end = Math.min(s.cursor + BATCH_CHUNK_SIZE, s.paramsList.length);

    for (var i = s.cursor; i < end; i++) {
        var paramIdx = parseInt(s.paramsList[i].index);
        var value    = parseFloat(s.paramsList[i].value);

        // Reuse paramCursor via goto() instead of new LiveAPI() per param
        try {
            s.paramCursor.goto(s.devicePath + " parameters " + paramIdx);
        } catch (e) {
            s.errors.push({ index: paramIdx, error: "LiveAPI error: " + e.toString() });
            s.failCount++;
            continue;
        }

        if (!s.paramCursor.id || parseInt(s.paramCursor.id) === 0) {
            s.errors.push({ index: paramIdx, error: "not found" });
            s.failCount++;
            continue;
        }

        try {
            var minVal  = parseFloat(s.paramCursor.get("min"));
            var maxVal  = parseFloat(s.paramCursor.get("max"));
            var clamped = Math.max(minVal, Math.min(maxVal, value));
            s.paramCursor.set("value", clamped);
            s.okCount++;
        } catch (e) {
            s.errors.push({ index: paramIdx, error: e.toString() });
            s.failCount++;
        }
    }

    s.cursor = end;

    if (s.cursor >= s.paramsList.length) {
        // All chunks done — send the response
        var result = {
            params_set:      s.okCount,
            params_failed:   s.failCount,
            total_requested: s.totalRequested
        };
        if (s.skippedDeviceOn) {
            result.skipped_device_on = true;
        }
        // Only include error details (not full results) to keep response small
        if (s.errors.length > 0) {
            result.errors = s.errors;
        }
        sendResult(result, s.requestId);
        _batchState = null;
    } else {
        // Schedule the next chunk after a short delay
        var t = new Task(_batchProcessNextChunk);
        t.schedule(BATCH_CHUNK_DELAY);
    }
}

function handleCheckDashboard(args) {
    var requestId = (args.length > 0) ? args[0].toString() : "";
    var response = {
        status: "success",
        result: {
            dashboard_url: "http://127.0.0.1:9880",
            bridge_version: "2.0.0",
            message: "Open the dashboard URL in your browser to view server status"
        },
        id: requestId
    };
    sendResponse(JSON.stringify(response));
}

// ---------------------------------------------------------------------------
// Phase 2: Device Chain Navigation
//
// Racks (Instrument Rack, Audio Effect Rack, Drum Rack) contain chains,
// each chain contains devices. Drum Racks also have drum_pads with chains.
// LOM paths:
//   live_set tracks T devices D chains C
//   live_set tracks T devices D chains C devices CD
//   live_set tracks T devices D drum_pads N chains C devices CD
// ---------------------------------------------------------------------------

function handleDiscoverChains(args) {
    // args: [track_index (int), device_index (int), extra_path (string), request_id (string)]
    // Backward-compatible: if only 3 args, extra_path is empty
    if (args.length < 3) {
        sendError("discover_chains requires track_index, device_index, request_id", "");
        return;
    }
    var trackIdx  = parseInt(args[0]);
    var deviceIdx = parseInt(args[1]);
    var extraPath = "";
    var requestId;

    if (args.length >= 4) {
        extraPath = args[2].toString();
        requestId = args[3].toString();
    } else {
        requestId = args[2].toString();
    }

    var devicePath = "live_set tracks " + trackIdx + " devices " + deviceIdx;
    if (extraPath && extraPath !== "") {
        devicePath = devicePath + " " + extraPath;
    }
    post("discover_chains: path=" + devicePath + "\n");

    var result = discoverChainsAtPath(devicePath);
    sendResult(result, requestId);
}

function discoverChainsAtPath(devicePath) {
    var deviceApi  = new LiveAPI(null, devicePath);

    if (!deviceApi || !deviceApi.id || parseInt(deviceApi.id) === 0) {
        return { error: "No device found at path: " + devicePath };
    }

    var deviceName  = deviceApi.get("name").toString();
    var deviceClass = deviceApi.get("class_name").toString();

    // Check if this device can have chains
    var canHaveChains = false;
    try { canHaveChains = (parseInt(deviceApi.get("can_have_chains")) === 1); } catch (e) {}

    var hasDrumPads = false;
    try { hasDrumPads = (parseInt(deviceApi.get("can_have_drum_pads")) === 1); } catch (e) {}

    if (!canHaveChains) {
        return {
            device_name: deviceName,
            device_class: deviceClass,
            can_have_chains: false,
            has_drum_pads: false,
            message: "This device does not support chains."
        };
    }

    var result = {
        device_name: deviceName,
        device_class: deviceClass,
        can_have_chains: true,
        has_drum_pads: hasDrumPads
    };

    // Reuse 2 LiveAPI cursors via goto() to avoid exhausting Max's object table.
    // Previously created ~193 LiveAPI objects for a 16-pad drum rack; now only 3 total.
    var cursor = new LiveAPI(null, devicePath);
    var innerCursor = new LiveAPI(null, devicePath);

    // Enumerate chains
    var chainCount = 0;
    try { chainCount = parseInt(deviceApi.getcount("chains")); } catch (e) {}

    var chains = [];
    for (var c = 0; c < chainCount; c++) {
        var chainPath = devicePath + " chains " + c;
        cursor.goto(chainPath);
        if (!cursor.id || parseInt(cursor.id) === 0) continue;

        var chainInfo = {
            index: c,
            name: ""
        };
        try { chainInfo.name = cursor.get("name").toString(); } catch (e) {}

        // Enumerate devices in this chain
        var devCount = 0;
        try { devCount = parseInt(cursor.getcount("devices")); } catch (e) {}

        var chainDevices = [];
        for (var d = 0; d < devCount; d++) {
            innerCursor.goto(chainPath + " devices " + d);
            if (!innerCursor.id || parseInt(innerCursor.id) === 0) continue;

            var cdInfo = { index: d, name: "", class_name: "" };
            try { cdInfo.name = innerCursor.get("name").toString(); } catch (e) {}
            try { cdInfo.class_name = innerCursor.get("class_name").toString(); } catch (e) {}
            try { cdInfo.can_have_chains = (parseInt(innerCursor.get("can_have_chains")) === 1); } catch (e) {}
            chainDevices.push(cdInfo);
        }
        chainInfo.devices = chainDevices;
        chainInfo.device_count = chainDevices.length;
        chains.push(chainInfo);
    }
    result.chains = chains;
    result.chain_count = chains.length;

    // Enumerate drum pads (only if this is a Drum Rack)
    if (hasDrumPads) {
        var drumPads = [];
        var padCount = 0;
        try { padCount = parseInt(deviceApi.getcount("drum_pads")); } catch (e) {}

        for (var p = 0; p < padCount; p++) {
            var padPath = devicePath + " drum_pads " + p;
            cursor.goto(padPath);
            if (!cursor.id || parseInt(cursor.id) === 0) continue;

            // Only include pads that have chains (i.e. have content)
            var padChainCount = 0;
            try { padChainCount = parseInt(cursor.getcount("chains")); } catch (e) {}
            if (padChainCount === 0) continue;

            var padInfo = { index: p, name: "", note: -1, chain_count: padChainCount };
            try { padInfo.name = cursor.get("name").toString(); } catch (e) {}
            try { padInfo.note = parseInt(cursor.get("note")); } catch (e) {}
            try { padInfo.mute = (parseInt(cursor.get("mute")) === 1); } catch (e) {}
            try { padInfo.solo = (parseInt(cursor.get("solo")) === 1); } catch (e) {}

            // Get devices in the first chain of this pad
            if (padChainCount > 0) {
                var padChainPath = padPath + " chains 0";
                innerCursor.goto(padChainPath);
                var padDevCount = 0;
                try { padDevCount = parseInt(innerCursor.getcount("devices")); } catch (e) {}
                var padDevices = [];
                for (var pd = 0; pd < padDevCount; pd++) {
                    innerCursor.goto(padChainPath + " devices " + pd);
                    if (!innerCursor.id || parseInt(innerCursor.id) === 0) continue;
                    var pdInfo = { index: pd, name: "", class_name: "" };
                    try { pdInfo.name = innerCursor.get("name").toString(); } catch (e) {}
                    try { pdInfo.class_name = innerCursor.get("class_name").toString(); } catch (e) {}
                    padDevices.push(pdInfo);
                }
                padInfo.devices = padDevices;
            }

            drumPads.push(padInfo);
        }
        result.drum_pads = drumPads;
        result.populated_pad_count = drumPads.length;
    }

    return result;
}

function handleGetChainDeviceParams(args) {
    // args: [track_index, device_index, chain_index, chain_device_index, request_id]
    if (args.length < 5) {
        sendError("get_chain_device_params requires track_index, device_index, chain_index, chain_device_index, request_id", "");
        return;
    }
    var trackIdx      = parseInt(args[0]);
    var deviceIdx     = parseInt(args[1]);
    var chainIdx      = parseInt(args[2]);
    var chainDevIdx   = parseInt(args[3]);
    var requestId     = args[4].toString();

    var devicePath = "live_set tracks " + trackIdx + " devices " + deviceIdx
                   + " chains " + chainIdx + " devices " + chainDevIdx;
    // Use chunked discovery — same crash-safe pattern as handleDiscoverParams
    _startChunkedDiscoverAtPath(devicePath, requestId);
}

function handleSetChainDeviceParam(args) {
    // args: [track_index, device_index, chain_index, chain_device_index, param_index, value, request_id]
    if (args.length < 7) {
        sendError("set_chain_device_param requires track_index, device_index, chain_index, chain_device_index, param_index, value, request_id", "");
        return;
    }
    var trackIdx      = parseInt(args[0]);
    var deviceIdx     = parseInt(args[1]);
    var chainIdx      = parseInt(args[2]);
    var chainDevIdx   = parseInt(args[3]);
    var paramIdx      = parseInt(args[4]);
    var value         = parseFloat(args[5]);
    var requestId     = args[6].toString();

    var paramPath = "live_set tracks " + trackIdx + " devices " + deviceIdx
                  + " chains " + chainIdx + " devices " + chainDevIdx
                  + " parameters " + paramIdx;

    var paramApi = new LiveAPI(null, paramPath);
    if (!paramApi || !paramApi.id || parseInt(paramApi.id) === 0) {
        sendError("No parameter found at path: " + paramPath, requestId);
        return;
    }

    try {
        var paramName = paramApi.get("name").toString();
        var minVal    = parseFloat(paramApi.get("min"));
        var maxVal    = parseFloat(paramApi.get("max"));
        var clamped   = Math.max(minVal, Math.min(maxVal, value));
        paramApi.set("value", clamped);
        // NO readback — get() after set() can crash Ableton

        sendResult({
            parameter_name:  paramName,
            parameter_index: paramIdx,
            requested_value: value,
            actual_value:    clamped,
            was_clamped:     (clamped !== value)
        }, requestId);
    } catch (e) {
        sendError("Failed to set chain device parameter: " + e.toString(), requestId);
    }
}

// ---------------------------------------------------------------------------
// Phase 3: Simpler / Sample Deep Access
//
// SimplerDevice has a 'sample' child object (LOM Sample) with properties:
//   start_marker, end_marker, file_path, gain, length, sample_rate,
//   slices, slicing_sensitivity, warp_markers, warp_mode, warping, etc.
// Functions: insert_slice, move_slice, remove_slice, clear_slices, reset_slices
// SimplerDevice props: playback_mode, multi_sample_mode, voices
// SimplerDevice funcs: crop, reverse, warp_as, warp_double, warp_half
// ---------------------------------------------------------------------------

function handleGetSimplerInfo(args) {
    // args: [track_index, device_index, request_id]
    if (args.length < 3) {
        sendError("get_simpler_info requires track_index, device_index, request_id", "");
        return;
    }
    var trackIdx  = parseInt(args[0]);
    var deviceIdx = parseInt(args[1]);
    var requestId = args[2].toString();

    var devicePath = "live_set tracks " + trackIdx + " devices " + deviceIdx;
    var deviceApi  = new LiveAPI(null, devicePath);

    if (!deviceApi || !deviceApi.id || parseInt(deviceApi.id) === 0) {
        sendError("No device found at track " + trackIdx + " device " + deviceIdx, requestId);
        return;
    }

    var className = "";
    try { className = deviceApi.get("class_name").toString(); } catch (e) {}

    if (className !== "OriginalSimpler") {
        sendError("Device is not a Simpler (class: " + className + ")", requestId);
        return;
    }

    var result = {
        device_name: "",
        device_class: className
    };
    try { result.device_name = deviceApi.get("name").toString(); } catch (e) {}

    // SimplerDevice properties
    try { result.playback_mode = parseInt(deviceApi.get("playback_mode")); } catch (e) {}
    try { result.multi_sample_mode = parseInt(deviceApi.get("multi_sample_mode")); } catch (e) {}
    try { result.pad_slicing = parseInt(deviceApi.get("pad_slicing")); } catch (e) {}
    try { result.retrigger = (parseInt(deviceApi.get("retrigger")) === 1); } catch (e) {}
    try { result.voices = parseInt(deviceApi.get("voices")); } catch (e) {}

    // Sample child
    var samplePath = devicePath + " sample";
    var sampleApi;
    try {
        sampleApi = new LiveAPI(null, samplePath);
    } catch (e) {
        result.sample = null;
        result.message = "No sample loaded";
        sendResult(result, requestId);
        return;
    }

    if (!sampleApi || !sampleApi.id || parseInt(sampleApi.id) === 0) {
        result.sample = null;
        result.message = "No sample loaded";
        sendResult(result, requestId);
        return;
    }

    var sample = {};
    try { sample.file_path         = sampleApi.get("file_path").toString(); } catch (e) {}
    try { sample.length            = parseInt(sampleApi.get("length")); } catch (e) {}
    try { sample.sample_rate       = parseInt(sampleApi.get("sample_rate")); } catch (e) {}
    try { sample.start_marker      = parseInt(sampleApi.get("start_marker")); } catch (e) {}
    try { sample.end_marker        = parseInt(sampleApi.get("end_marker")); } catch (e) {}
    try { sample.gain              = parseFloat(sampleApi.get("gain")); } catch (e) {}
    try { sample.warping           = (parseInt(sampleApi.get("warping")) === 1); } catch (e) {}
    try { sample.warp_mode         = parseInt(sampleApi.get("warp_mode")); } catch (e) {}
    try { sample.slicing_sensitivity = parseFloat(sampleApi.get("slicing_sensitivity")); } catch (e) {}

    // Warp mode name mapping
    var warpModeMap = { 0: "beats", 1: "tones", 2: "texture", 3: "re_pitch", 4: "complex", 5: "complex_pro", 6: "rex" };
    if (sample.warp_mode !== undefined) {
        sample.warp_mode_name = warpModeMap[sample.warp_mode] || "unknown";
    }

    // Read slices
    try {
        var slicesRaw = sampleApi.get("slices");
        if (slicesRaw) {
            var sliceStr = slicesRaw.toString();
            if (sliceStr && sliceStr !== "null" && sliceStr !== "") {
                sample.slices = sliceStr;
            }
        }
    } catch (e) {}

    // Read warp markers
    try {
        var markersRaw = sampleApi.get("warp_markers");
        if (markersRaw) {
            var markerStr = markersRaw.toString();
            if (markerStr && markerStr !== "null" && markerStr !== "") {
                sample.warp_markers = markerStr;
            }
        }
    } catch (e) {}

    // Beats-specific properties
    try { sample.beats_granulation_resolution  = parseInt(sampleApi.get("beats_granulation_resolution")); } catch (e) {}
    try { sample.beats_transient_envelope      = parseInt(sampleApi.get("beats_transient_envelope")); } catch (e) {}
    try { sample.beats_transient_loop_mode     = parseInt(sampleApi.get("beats_transient_loop_mode")); } catch (e) {}
    // Texture-specific
    try { sample.texture_flux       = parseFloat(sampleApi.get("texture_flux")); } catch (e) {}
    try { sample.texture_grain_size = parseFloat(sampleApi.get("texture_grain_size")); } catch (e) {}
    // Tones-specific
    try { sample.tones_grain_size   = parseFloat(sampleApi.get("tones_grain_size")); } catch (e) {}
    // Complex Pro specific
    try { sample.complex_pro_envelope  = parseFloat(sampleApi.get("complex_pro_envelope")); } catch (e) {}
    try { sample.complex_pro_formants  = parseFloat(sampleApi.get("complex_pro_formants")); } catch (e) {}

    result.sample = sample;
    sendResult(result, requestId);
}

function handleSetSimplerSampleProps(args) {
    // args: [track_index, device_index, props_json_b64, request_id]
    if (args.length < 4) {
        sendError("set_simpler_sample_props requires track_index, device_index, props_json_b64, request_id", "");
        return;
    }
    var trackIdx  = parseInt(args[0]);
    var deviceIdx = parseInt(args[1]);
    var requestId = args[args.length - 1].toString();

    // Reassemble b64 payload (Max may split long strings)
    var b64Parts = [];
    for (var a = 2; a < args.length - 1; a++) {
        b64Parts.push(args[a].toString());
    }
    var propsB64 = b64Parts.join("");

    var propsJson;
    try { propsJson = _base64decode(propsB64); } catch (e) {
        sendError("Failed to decode props_json_b64: " + e.toString(), requestId);
        return;
    }
    var props;
    try { props = JSON.parse(propsJson); } catch (e) {
        sendError("Failed to parse props JSON: " + e.toString(), requestId);
        return;
    }

    var samplePath = "live_set tracks " + trackIdx + " devices " + deviceIdx + " sample";
    var sampleApi;
    try {
        sampleApi = new LiveAPI(null, samplePath);
    } catch (e) {
        sendError("No sample found: " + e.toString(), requestId);
        return;
    }

    if (!sampleApi || !sampleApi.id || parseInt(sampleApi.id) === 0) {
        sendError("No sample loaded in Simpler at track " + trackIdx + " device " + deviceIdx, requestId);
        return;
    }

    // Settable sample properties
    var settable = [
        "start_marker", "end_marker", "warping", "warp_mode",
        "slicing_sensitivity", "gain",
        "beats_granulation_resolution", "beats_transient_envelope", "beats_transient_loop_mode",
        "texture_flux", "texture_grain_size", "tones_grain_size",
        "complex_pro_envelope", "complex_pro_formants"
    ];

    var setCount = 0;
    var errors = [];
    for (var key in props) {
        if (!props.hasOwnProperty(key)) continue;
        var found = false;
        for (var s = 0; s < settable.length; s++) {
            if (settable[s] === key) { found = true; break; }
        }
        if (!found) {
            errors.push({ property: key, error: "not a settable property" });
            continue;
        }
        try {
            sampleApi.set(key, props[key]);
            setCount++;
        } catch (e) {
            errors.push({ property: key, error: e.toString() });
        }
    }

    var result = { properties_set: setCount };
    if (errors.length > 0) result.errors = errors;
    sendResult(result, requestId);
}

function handleSimplerSlice(args) {
    // args: [track_index, device_index, action ("insert"|"remove"|"clear"|"reset"), slice_time (float, for insert/remove), request_id]
    if (args.length < 4) {
        sendError("simpler_slice requires track_index, device_index, action, request_id (slice_time for insert/remove)", "");
        return;
    }
    var trackIdx  = parseInt(args[0]);
    var deviceIdx = parseInt(args[1]);
    var action    = args[2].toString();

    var sliceTime = 0;
    var requestId;
    if (action === "move") {
        if (args.length < 6) {
            sendError("simpler_slice move requires old_time, new_time, and request_id", "");
            return;
        }
        sliceTime = parseFloat(args[3]);
        requestId = args[args.length - 1].toString();
    } else if (action === "insert" || action === "remove") {
        if (args.length < 5) {
            sendError("simpler_slice " + action + " requires slice_time and request_id", "");
            return;
        }
        sliceTime = parseFloat(args[3]);
        requestId = args[args.length - 1].toString();
    } else {
        requestId = args[args.length - 1].toString();
    }

    var samplePath = "live_set tracks " + trackIdx + " devices " + deviceIdx + " sample";
    var sampleApi;
    try {
        sampleApi = new LiveAPI(null, samplePath);
    } catch (e) {
        sendError("No sample found: " + e.toString(), requestId);
        return;
    }

    if (!sampleApi || !sampleApi.id || parseInt(sampleApi.id) === 0) {
        sendError("No sample loaded in Simpler", requestId);
        return;
    }

    try {
        switch (action) {
            case "insert":
                sampleApi.call("insert_slice", sliceTime);
                sendResult({ action: "insert", slice_time: sliceTime }, requestId);
                break;
            case "remove":
                sampleApi.call("remove_slice", sliceTime);
                sendResult({ action: "remove", slice_time: sliceTime }, requestId);
                break;
            case "clear":
                sampleApi.call("clear_slices");
                sendResult({ action: "clear" }, requestId);
                break;
            case "move": {
                let newTime = parseFloat(args[4]);
                sampleApi.call("move_slice", sliceTime, newTime);
                sendResult({ action: "move", old_time: sliceTime, new_time: newTime }, requestId);
                break;
            }
            case "reset":
                sampleApi.call("reset_slices");
                sendResult({ action: "reset" }, requestId);
                break;
            default:
                sendError("Unknown slice action: " + action + " (use insert, remove, move, clear, reset)", requestId);
                break;
        }
    } catch (e) {
        sendError("Slice operation failed: " + e.toString(), requestId);
    }
}

// ---------------------------------------------------------------------------
// Phase 4: Wavetable Modulation Matrix
//
// WavetableDevice (class_name "InstrumentVector") has:
//   Properties: filter_routing, mono_poly, poly_voices,
//     oscillator_1/2_effect_mode, oscillator_1/2_wavetable_category,
//     oscillator_1/2_wavetable_index, oscillator_1/2_wavetables (list),
//     oscillator_wavetable_categories (list), unison_mode, unison_voice_count,
//     visible_modulation_target_names (list)
//   Functions:
//     get_modulation_value(target_idx, source_idx) -> float
//     set_modulation_value(target_idx, source_idx, value)
//     add_parameter_to_modulation_matrix(parameter)
//     is_parameter_modulatable(parameter) -> bool
//     get_modulation_target_parameter_name(idx) -> string
// ---------------------------------------------------------------------------

function handleGetWavetableInfo(args) {
    // args: [track_index, device_index, request_id]
    if (args.length < 3) {
        sendError("get_wavetable_info requires track_index, device_index, request_id", "");
        return;
    }
    var trackIdx  = parseInt(args[0]);
    var deviceIdx = parseInt(args[1]);
    var requestId = args[2].toString();

    var devicePath = "live_set tracks " + trackIdx + " devices " + deviceIdx;
    var deviceApi  = new LiveAPI(null, devicePath);

    if (!deviceApi || !deviceApi.id || parseInt(deviceApi.id) === 0) {
        sendError("No device found at track " + trackIdx + " device " + deviceIdx, requestId);
        return;
    }

    var className = "";
    try { className = deviceApi.get("class_name").toString(); } catch (e) {}

    if (className !== "InstrumentVector") {
        sendError("Device is not a Wavetable (class: " + className + ")", requestId);
        return;
    }

    var result = {
        device_name: "",
        device_class: className
    };
    try { result.device_name = deviceApi.get("name").toString(); } catch (e) {}

    // Oscillator settings
    try { result.oscillator_1_effect_mode = parseInt(deviceApi.get("oscillator_1_effect_mode")); } catch (e) {}
    try { result.oscillator_2_effect_mode = parseInt(deviceApi.get("oscillator_2_effect_mode")); } catch (e) {}
    try { result.oscillator_1_wavetable_category = parseInt(deviceApi.get("oscillator_1_wavetable_category")); } catch (e) {}
    try { result.oscillator_1_wavetable_index    = parseInt(deviceApi.get("oscillator_1_wavetable_index")); } catch (e) {}
    try { result.oscillator_2_wavetable_category = parseInt(deviceApi.get("oscillator_2_wavetable_category")); } catch (e) {}
    try { result.oscillator_2_wavetable_index    = parseInt(deviceApi.get("oscillator_2_wavetable_index")); } catch (e) {}

    // Wavetable lists
    try {
        var cats = deviceApi.get("oscillator_wavetable_categories");
        if (cats) result.wavetable_categories = cats.toString();
    } catch (e) {}

    try {
        var wt1 = deviceApi.get("oscillator_1_wavetables");
        if (wt1) result.oscillator_1_wavetables = wt1.toString();
    } catch (e) {}

    try {
        var wt2 = deviceApi.get("oscillator_2_wavetables");
        if (wt2) result.oscillator_2_wavetables = wt2.toString();
    } catch (e) {}

    // Voice / unison properties — readable but NOT writable via M4L
    try { result.filter_routing     = parseInt(deviceApi.get("filter_routing")); } catch (e) {}
    try { result.mono_poly          = parseInt(deviceApi.get("mono_poly")); } catch (e) {}
    try { result.poly_voices        = parseInt(deviceApi.get("poly_voices")); } catch (e) {}
    try { result.unison_mode        = parseInt(deviceApi.get("unison_mode")); } catch (e) {}
    try { result.unison_voice_count = parseInt(deviceApi.get("unison_voice_count")); } catch (e) {}

    // Modulation targets
    try {
        var targetNames = deviceApi.get("visible_modulation_target_names");
        if (targetNames) result.modulation_target_names = targetNames.toString();
    } catch (e) {}

    // Read current modulation matrix values for visible targets
    // Sources: 0=Env2, 1=Env3, 2=LFO1, 3=LFO2 (standard Wavetable layout)
    try {
        var targetNamesArr = result.modulation_target_names;
        if (targetNamesArr) {
            var names = targetNamesArr.split(",");
            var modMatrix = [];
            for (var t = 0; t < names.length && t < 50; t++) {
                var row = { target_index: t, target_name: names[t] };
                var hasValue = false;
                for (var src = 0; src < 4; src++) {
                    try {
                        var modVal = deviceApi.call("get_modulation_value", t, src);
                        if (modVal !== undefined && modVal !== null) {
                            var fVal = parseFloat(modVal);
                            if (fVal !== 0.0) {
                                if (!row.sources) row.sources = {};
                                var srcName = ["Env2", "Env3", "LFO1", "LFO2"][src];
                                row.sources[srcName] = fVal;
                                hasValue = true;
                            }
                        }
                    } catch (e) {}
                }
                if (hasValue) modMatrix.push(row);
            }
            if (modMatrix.length > 0) result.active_modulations = modMatrix;
        }
    } catch (e) {}

    sendResult(result, requestId);
}

function handleSetWavetableModulation(args) {
    // args: [track_index, device_index, target_index, source_index, amount, request_id]
    if (args.length < 6) {
        sendError("set_wavetable_modulation requires track_index, device_index, target_index, source_index, amount, request_id", "");
        return;
    }
    var trackIdx    = parseInt(args[0]);
    var deviceIdx   = parseInt(args[1]);
    var targetIdx   = parseInt(args[2]);
    var sourceIdx   = parseInt(args[3]);
    var amount      = parseFloat(args[4]);
    var requestId   = args[5].toString();

    var devicePath = "live_set tracks " + trackIdx + " devices " + deviceIdx;
    var deviceApi  = new LiveAPI(null, devicePath);

    if (!deviceApi || !deviceApi.id || parseInt(deviceApi.id) === 0) {
        sendError("No device found", requestId);
        return;
    }

    try {
        deviceApi.call("set_modulation_value", targetIdx, sourceIdx, amount);

        // Read back the value to confirm
        var actualVal = parseFloat(deviceApi.call("get_modulation_value", targetIdx, sourceIdx));
        var srcNames = ["Env2", "Env3", "LFO1", "LFO2"];

        sendResult({
            target_index: targetIdx,
            source_index: sourceIdx,
            source_name:  srcNames[sourceIdx] || ("Source " + sourceIdx),
            requested_amount: amount,
            actual_amount: actualVal
        }, requestId);
    } catch (e) {
        sendError("Failed to set modulation: " + e.toString(), requestId);
    }
}

function handleSetWavetableProps(args) {
    // args: [track_index, device_index, props_json_b64, request_id]
    if (args.length < 4) {
        sendError("set_wavetable_props requires track_index, device_index, props_json_b64, request_id", "");
        return;
    }
    var trackIdx  = parseInt(args[0]);
    var deviceIdx = parseInt(args[1]);
    var requestId = args[args.length - 1].toString();

    var b64Parts = [];
    for (var a = 2; a < args.length - 1; a++) {
        b64Parts.push(args[a].toString());
    }
    var propsB64 = b64Parts.join("");

    var propsJson;
    try { propsJson = _base64decode(propsB64); } catch (e) {
        sendError("Failed to decode props_json_b64: " + e.toString(), requestId);
        return;
    }
    var props;
    try { props = JSON.parse(propsJson); } catch (e) {
        sendError("Failed to parse props JSON: " + e.toString(), requestId);
        return;
    }

    var devicePath = "live_set tracks " + trackIdx + " devices " + deviceIdx;
    var deviceApi  = new LiveAPI(null, devicePath);

    if (!deviceApi || !deviceApi.id || parseInt(deviceApi.id) === 0) {
        sendError("No device found", requestId);
        return;
    }

    // Tier 1: Oscillator properties — reliably settable via LiveAPI.set()
    var tier1 = [
        "oscillator_1_effect_mode", "oscillator_2_effect_mode",
        "oscillator_1_wavetable_category", "oscillator_1_wavetable_index",
        "oscillator_2_wavetable_category", "oscillator_2_wavetable_index"
    ];
    // Tier 2: Voice/unison/filter properties — these are handled by the MCP
    // server via TCP (set_device_parameter). LiveAPI.set() silently fails for these.
    var tier2 = [
        "filter_routing", "mono_poly", "poly_voices",
        "unison_mode", "unison_voice_count"
    ];

    var setCount = 0;
    var errors = [];
    var details = [];
    for (var key in props) {
        if (!props.hasOwnProperty(key)) continue;

        // Check which tier
        var isTier1 = false, isTier2 = false;
        for (var s = 0; s < tier1.length; s++) {
            if (tier1[s] === key) { isTier1 = true; break; }
        }
        if (!isTier1) {
            for (var s2 = 0; s2 < tier2.length; s2++) {
                if (tier2[s2] === key) { isTier2 = true; break; }
            }
        }
        if (!isTier1 && !isTier2) {
            errors.push({ property: key, error: "not a settable property" });
            continue;
        }

        // Tier 2 properties: skip — the server routes these via TCP instead
        if (isTier2) {
            details.push({ property: key, value: Number(props[key]), note: "skipped — use TCP set_device_parameter instead" });
            continue;
        }

        var val = Number(props[key]);

        // Fire-and-forget set() — NO get() calls to avoid Ableton crashes
        try {
            deviceApi.set(key, val);
        } catch (e) {
            errors.push({ property: key, error: e.toString() });
            continue;
        }

        setCount++;
        details.push({ property: key, value: val });
    }

    var result = { properties_set: setCount };
    if (details.length > 0) result.details = details;
    if (errors.length > 0) result.errors = errors;
    sendResult(result, requestId);
}

// ---------------------------------------------------------------------------
// Diagnostic: probe device info + try setting "read-only" properties
// ---------------------------------------------------------------------------

function handleProbeDeviceInfo(args) {
    // args: [track_index, device_index, request_id]
    if (args.length < 3) {
        sendError("probe_device_info requires track_index, device_index, request_id", "");
        return;
    }
    var trackIdx  = parseInt(args[0]);
    var deviceIdx = parseInt(args[1]);
    var requestId = args[2].toString();

    var devicePath = "live_set tracks " + trackIdx + " devices " + deviceIdx;
    var deviceApi  = new LiveAPI(null, devicePath);

    if (!deviceApi || !deviceApi.id || parseInt(deviceApi.id) === 0) {
        sendError("No device found at track " + trackIdx + " device " + deviceIdx, requestId);
        return;
    }

    var result = {};

    // 1. Dump device info (shows properties, children, functions)
    try {
        var info = deviceApi.get("info").toString();
        result.info = info;
    } catch (e) {
        result.info_error = e.toString();
    }

    // 2. Read current values of tier-2 properties
    var tier2Props = ["unison_mode", "unison_voice_count", "poly_voices", "filter_routing", "mono_poly"];
    var beforeValues = {};
    for (var i = 0; i < tier2Props.length; i++) {
        var prop = tier2Props[i];
        try {
            beforeValues[prop] = deviceApi.get(prop).toString();
        } catch (e) {
            beforeValues[prop] = "ERROR: " + e.toString();
        }
    }
    result.before_values = beforeValues;

    // 3. Try setting each property via set(), then readback
    var setAttempts = {};
    for (var j = 0; j < tier2Props.length; j++) {
        var prop2 = tier2Props[j];
        var testVal = 1; // Safe test value for all (Classic/Parallel/Poly/1 voice)
        var attempt = {};
        try {
            deviceApi.set(prop2, testVal);
            attempt.set_no_crash = true;
        } catch (e) {
            attempt.set_error = e.toString();
        }
        // Readback to see if it actually changed
        try {
            var after = deviceApi.get(prop2).toString();
            attempt.after_set = after;
            attempt.changed = (after !== beforeValues[prop2]);
        } catch (e) {
            attempt.readback_error = e.toString();
        }
        setAttempts[prop2] = attempt;
    }
    result.set_attempts = setAttempts;

    // 4. Try call() with various method name patterns
    var callAttempts = {};
    for (var k = 0; k < tier2Props.length; k++) {
        var prop3 = tier2Props[k];
        var testVal2 = 2; // Different value to distinguish from set() test
        var callResult = {};

        // Try: deviceApi.call(prop_name, value)
        try {
            deviceApi.call(prop3, testVal2);
            callResult.call_direct = "no error";
        } catch (e) {
            callResult.call_direct = "ERROR: " + e.toString();
        }

        // Try: deviceApi.call("set_" + prop_name, value)
        try {
            deviceApi.call("set_" + prop3, testVal2);
            callResult.call_set_prefix = "no error";
        } catch (e) {
            callResult.call_set_prefix = "ERROR: " + e.toString();
        }

        // Readback after call attempts
        try {
            var afterCall = deviceApi.get(prop3).toString();
            callResult.after_call = afterCall;
            callResult.changed_from_before = (afterCall !== beforeValues[prop3]);
        } catch (e) {
            callResult.readback_error = e.toString();
        }

        callAttempts[prop3] = callResult;
    }
    result.call_attempts = callAttempts;

    // 5. Try accessing properties through children paths
    var childAttempts = {};
    var childPaths = [
        "live_set tracks " + trackIdx + " devices " + deviceIdx + " parameters",
        "live_set tracks " + trackIdx + " devices " + deviceIdx + " view"
    ];
    for (var p = 0; p < childPaths.length; p++) {
        try {
            var childApi = new LiveAPI(null, childPaths[p]);
            if (childApi && childApi.id && parseInt(childApi.id) !== 0) {
                childAttempts[childPaths[p]] = {
                    id: childApi.id.toString(),
                    info: childApi.get("info").toString().substring(0, 500)
                };
            } else {
                childAttempts[childPaths[p]] = "no object";
            }
        } catch (e) {
            childAttempts[childPaths[p]] = "ERROR: " + e.toString();
        }
    }
    result.child_paths = childAttempts;

    // 6. Restore original values (best effort)
    for (var r = 0; r < tier2Props.length; r++) {
        var prop4 = tier2Props[r];
        var origVal = beforeValues[prop4];
        if (origVal && origVal.indexOf("ERROR") === -1) {
            try { deviceApi.set(prop4, parseInt(origVal)); } catch (e) {}
        }
    }

    sendResult(result, requestId);
}

// ---------------------------------------------------------------------------
// Device Property Access — get/set device-level LOM properties
// ---------------------------------------------------------------------------

function handleGetDeviceProperty(args) {
    // args: [track_index (int), device_index (int), property_name (string), request_id (string)]
    if (args.length < 4) {
        sendError("get_device_property requires track_index, device_index, property_name, request_id", "");
        return;
    }
    var trackIdx     = parseInt(args[0]);
    var deviceIdx    = parseInt(args[1]);
    var propertyName = args[2].toString();
    var requestId    = args[3].toString();

    var result = getDeviceProperty(trackIdx, deviceIdx, propertyName);
    sendResult(result, requestId);
}

function handleSetDeviceProperty(args) {
    // args: [track_index (int), device_index (int), property_name (string), value (float), request_id (string)]
    if (args.length < 5) {
        sendError("set_device_property requires track_index, device_index, property_name, value, request_id", "");
        return;
    }
    var trackIdx     = parseInt(args[0]);
    var deviceIdx    = parseInt(args[1]);
    var propertyName = args[2].toString();
    var value        = parseFloat(args[3]);
    var requestId    = args[4].toString();

    var result = setDeviceProperty(trackIdx, deviceIdx, propertyName, value);
    sendResult(result, requestId);
}

// ---------------------------------------------------------------------------
// Response helpers
// ---------------------------------------------------------------------------

function sendResult(result, requestId) {
    if (result.error) {
        sendError(result.error, requestId);
        return;
    }
    var response = {
        status: "success",
        result: result,
        id: requestId
    };
    sendResponse(JSON.stringify(response));
}

function sendError(message, requestId) {
    var response = {
        status: "error",
        message: message,
        id: requestId
    };
    sendResponse(JSON.stringify(response));
}

// ---------------------------------------------------------------------------
// Chunked response sending  (Revision 4)
//
// Max's [js] outlet() has a practical symbol size limit of ~8KB.  Responses
// larger than that crash Ableton.  Standard base64 '+' and '/' characters
// also confuse Max's OSC routing.
//
// Previous revisions failed because:
//   Rev 1-2: Non-base64 prefixes crash OSC layer
//   Rev 3:   _jsonEscape() inflates pieces (doubling every " char)
//   Rev 3b:  O(n^2) char-by-char _toUrlSafeBase64() on 16KB string locks
//            up the JS engine; full 16KB intermediate string in memory
//
// Rev 4 solution — chunk JSON first, encode pieces independently:
//   1. If small (≤ 1500 chars JSON) → encode + URL-safe + outlet directly
//   2. If large → split raw JSON into 2000-char pieces
//   3. Each piece: base64 → URL-safe → wrap in envelope → base64 → URL-safe
//   4. ALL chunks deferred via Task.schedule() (not synchronous)
//   5. Each outlet() sends ~3.6KB — well under 8KB limit
//
// Key safety properties:
//   - Never creates the full base64 string in memory
//   - .replace() for URL-safe conversion is O(n) native, not O(n^2) loop
//   - Each operation works on ≤ ~3.6KB strings — no memory pressure
//   - First chunk is deferred (not synchronous from discovery callback)
// ---------------------------------------------------------------------------
var RESPONSE_PIECE_SIZE  = 2000;  // chars of RAW JSON per chunk (conservative)
var RESPONSE_CHUNK_DELAY = 50;    // ms between outlet() calls
var _responseSendState   = null;  // global state for deferred chunk sending
var _responseSendQueue   = [];    // queued responses when send is busy

function _toUrlSafe(b64) {
    // O(n) native .replace() — NOT char-by-char concatenation
    return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
}

function sendResponse(jsonStr) {
    // If a chunked send is in progress, queue this response (regardless of size)
    if (_responseSendState) {
        _responseSendQueue.push(jsonStr);
        post("sendResponse: queued (send busy), queue depth=" + _responseSendQueue.length + "\n");
        return;
    }

    // Small response — encode + send directly (backward compatible)
    if (jsonStr.length <= 1500) {
        outlet(0, _toUrlSafe(_base64encode(jsonStr)));
        return;
    }

    // Large response — store raw JSON, defer ALL chunk sending via Task

    var totalChunks = Math.ceil(jsonStr.length / RESPONSE_PIECE_SIZE);
    post("sendResponse: " + jsonStr.length + " chars JSON -> " + totalChunks + " chunks\n");

    _responseSendState = {
        jsonStr:     jsonStr,
        totalChunks: totalChunks,
        idx:         0
    };

    // DEFER first chunk — don't send synchronously from discovery callback
    var t = new Task(_sendNextResponsePiece);
    t.schedule(RESPONSE_CHUNK_DELAY);
}

function _sendNextResponsePiece() {
    if (!_responseSendState) return;
    var s = _responseSendState;

    try {
        // Extract this piece of raw JSON
        var start = s.idx * RESPONSE_PIECE_SIZE;
        var end   = Math.min(start + RESPONSE_PIECE_SIZE, s.jsonStr.length);
        var piece = s.jsonStr.substring(start, end);

        // Encode piece independently → URL-safe base64 (O(n) via .replace())
        var pieceB64 = _toUrlSafe(_base64encode(piece));

        // Wrap in chunk envelope, encode envelope, send
        // pieceB64 is pure [A-Za-z0-9_-] — no escaping needed in the JSON string
        var envelope = '{"_c":' + s.idx + ',"_t":' + s.totalChunks + ',"_d":"' + pieceB64 + '"}';
        var envelopeB64 = _toUrlSafe(_base64encode(envelope));
        outlet(0, envelopeB64);
    } catch (e) {
        post("_sendNextResponsePiece error: " + e.toString() + "\n");
        _responseSendState = null;
        _drainResponseQueue();
        return;
    }

    s.idx++;
    if (s.idx < s.totalChunks) {
        var t = new Task(_sendNextResponsePiece);
        t.schedule(RESPONSE_CHUNK_DELAY);
    } else {
        _responseSendState = null;
        _drainResponseQueue();
    }
}

function _drainResponseQueue() {
    while (_responseSendQueue.length > 0) {
        var next = _responseSendQueue.shift();
        sendResponse(next);
        // If sendResponse started a chunked send, stop draining —
        // the next drain will happen when _sendNextResponsePiece completes
        if (_responseSendState) break;
    }
}

// ---------------------------------------------------------------------------
// Base64 encode — Max's JS engine doesn't have btoa
// ---------------------------------------------------------------------------
var _b64chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

function _base64encode(str) {
    var result = "";
    var i = 0;
    while (i < str.length) {
        var c1 = str.charCodeAt(i++) || 0;
        var c2 = str.charCodeAt(i++) || 0;
        var c3 = str.charCodeAt(i++) || 0;
        var triplet = (c1 << 16) | (c2 << 8) | c3;
        result += _b64chars.charAt((triplet >> 18) & 63);
        result += _b64chars.charAt((triplet >> 12) & 63);
        result += (i - 1 > str.length) ? "=" : _b64chars.charAt((triplet >> 6) & 63);
        result += (i > str.length) ? "=" : _b64chars.charAt(triplet & 63);
    }
    return result;
}

function _base64decode(str) {
    var lookup = {};
    for (var c = 0; c < _b64chars.length; c++) {
        lookup[_b64chars.charAt(c)] = c;
    }
    // Also accept URL-safe base64 variants (- instead of +, _ instead of /)
    lookup["-"] = 62;
    lookup["_"] = 63;
    str = str.replace(/=/g, "");
    var result = "";
    var i = 0;
    while (i < str.length) {
        var b0 = lookup[str.charAt(i++)] || 0;
        var b1 = lookup[str.charAt(i++)] || 0;
        var b2 = lookup[str.charAt(i++)] || 0;
        var b3 = lookup[str.charAt(i++)] || 0;
        var triplet = (b0 << 18) | (b1 << 12) | (b2 << 6) | b3;
        result += String.fromCharCode((triplet >> 16) & 255);
        if (i - 2 < str.length) result += String.fromCharCode((triplet >> 8) & 255);
        if (i - 1 < str.length) result += String.fromCharCode(triplet & 255);
    }
    return result;
}

// ---------------------------------------------------------------------------
// LOM access: set a specific parameter by its LOM index
// ---------------------------------------------------------------------------
function setHiddenParam(trackIdx, deviceIdx, paramIdx, value) {
    var paramPath = "live_set tracks " + trackIdx
                  + " devices " + deviceIdx
                  + " parameters " + paramIdx;
    var paramApi  = new LiveAPI(null, paramPath);

    if (!paramApi || !paramApi.id || parseInt(paramApi.id) === 0) {
        return { error: "No parameter found at index " + paramIdx + "." };
    }

    try {
        var paramName = paramApi.get("name").toString();
        var minVal    = parseFloat(paramApi.get("min"));
        var maxVal    = parseFloat(paramApi.get("max"));

        var clamped = Math.max(minVal, Math.min(maxVal, value));
        paramApi.set("value", clamped);
        // NO readback — get() after set() can crash Ableton (same pattern as wavetable fix)

        return {
            parameter_name:  paramName,
            parameter_index: paramIdx,
            requested_value: value,
            actual_value:    clamped,
            was_clamped:     (clamped !== value)
        };
    } catch (e) {
        return { error: "Failed to set parameter " + paramIdx + ": " + e.toString() };
    }
}

// ---------------------------------------------------------------------------
// LOM access: get a device-level property (not an indexed parameter)
// ---------------------------------------------------------------------------
function getDeviceProperty(trackIdx, deviceIdx, propertyName) {
    var devicePath = "live_set tracks " + trackIdx + " devices " + deviceIdx;
    var deviceApi  = new LiveAPI(null, devicePath);

    if (!deviceApi || !deviceApi.id || parseInt(deviceApi.id) === 0) {
        return { error: "No device found at track " + trackIdx + " device " + deviceIdx + "." };
    }

    try {
        var deviceName  = deviceApi.get("name").toString();
        var deviceClass = deviceApi.get("class_name").toString();
        var rawValue    = deviceApi.get(propertyName);

        var currentValue;
        if (rawValue === undefined || rawValue === null) {
            return { error: "Property '" + propertyName + "' returned null/undefined on this device." };
        }
        var numVal = parseFloat(rawValue.toString());
        currentValue = isNaN(numVal) ? rawValue.toString() : numVal;

        return {
            device_name:   deviceName,
            device_class:  deviceClass,
            property_name: propertyName,
            value:         currentValue
        };
    } catch (e) {
        return { error: "Failed to get property '" + propertyName + "' on device: " + e.toString() };
    }
}

// ---------------------------------------------------------------------------
// LOM access: set a device-level property (not an indexed parameter)
// ---------------------------------------------------------------------------
function setDeviceProperty(trackIdx, deviceIdx, propertyName, value) {
    var devicePath = "live_set tracks " + trackIdx + " devices " + deviceIdx;
    var deviceApi  = new LiveAPI(null, devicePath);

    if (!deviceApi || !deviceApi.id || parseInt(deviceApi.id) === 0) {
        return { error: "No device found at track " + trackIdx + " device " + deviceIdx + "." };
    }

    var READONLY = [
        "class_name", "class_display_name", "type",
        "can_have_chains", "can_have_drum_pads",
        "canonical_parent", "view", "parameters",
        "is_active"
    ];
    for (var r = 0; r < READONLY.length; r++) {
        if (propertyName === READONLY[r]) {
            return { error: "Property '" + propertyName + "' is read-only and cannot be set." };
        }
    }

    try {
        var deviceName  = deviceApi.get("name").toString();
        var deviceClass = deviceApi.get("class_name").toString();

        var oldRaw = deviceApi.get(propertyName);
        var oldNum = parseFloat(oldRaw.toString());
        var oldValue = isNaN(oldNum) ? oldRaw.toString() : oldNum;

        // Convert to integer if value is whole number (LOM device props are often enum ints)
        var setValue = value;
        if (value === Math.floor(value)) {
            setValue = Math.floor(value);
        }

        deviceApi.set(propertyName, setValue);

        var newRaw = deviceApi.get(propertyName);
        var newNum = parseFloat(newRaw.toString());
        var newValue = isNaN(newNum) ? newRaw.toString() : newNum;

        return {
            device_name:     deviceName,
            device_class:    deviceClass,
            property_name:   propertyName,
            old_value:       oldValue,
            new_value:       newValue,
            requested_value: value,
            success:         (newValue !== oldValue || newValue == value)
        };
    } catch (e) {
        return { error: "Failed to set property '" + propertyName + "' on device: " + e.toString() };
    }
}

// ---------------------------------------------------------------------------
// readParamInfo — extract all useful info from a single parameter LiveAPI
// ---------------------------------------------------------------------------
function readParamInfo(paramApi, index) {
    var info = {
        index:        index,
        name:         "",
        value:        0,
        min:          0,
        max:          0,
        is_quantized: false,
        default_value: 0
    };

    try { info.name          = paramApi.get("name").toString(); }         catch (e) {}
    try { info.value         = parseFloat(paramApi.get("value")); }       catch (e) {}
    try { info.min           = parseFloat(paramApi.get("min")); }         catch (e) {}
    try { info.max           = parseFloat(paramApi.get("max")); }         catch (e) {}
    try { info.is_quantized  = (parseInt(paramApi.get("is_quantized")) === 1); } catch (e) {}
    try { info.default_value = parseFloat(paramApi.get("default_value")); } catch (e) {}

    if (info.is_quantized) {
        try {
            var items = paramApi.get("value_items");
            if (items) {
                info.value_items = items.toString();
            }
        } catch (e) {}
    }

    return info;
}
