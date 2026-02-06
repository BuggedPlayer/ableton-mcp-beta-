/**
 * AbletonMCP M4L Bridge — m4l_bridge.js
 *
 * This script runs inside a Max for Live [js] object and provides
 * deep Live Object Model (LOM) access for the AbletonMCP server.
 *
 * Communication uses native OSC messages via udpreceive/udpsend:
 *   - The MCP server sends OSC messages like /ping, /discover_params, etc.
 *   - Max's udpreceive parses OSC and sends the address + args to this [js]
 *   - Responses are base64-encoded JSON sent back via outlet → udpsend
 *
 * The Max patch needs:
 *   [udpreceive 9878] → [js m4l_bridge.js] → [udpsend localhost 9879]
 */

// Max [js] object configuration
inlets  = 1;
outlets = 1;

// ---------------------------------------------------------------------------
// Initialization
// ---------------------------------------------------------------------------
function loadbang() {
    post("AbletonMCP M4L Bridge v1.1.0 starting...\n");
    post("Listening for OSC commands on port 9878.\n");
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

        default:
            post("AbletonMCP Bridge: unknown command: '" + cmd + "' (raw: '" + addr + "')\n");
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
        result: { m4l_bridge: true, version: "1.1.0" },
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

    var result = discoverParams(trackIdx, deviceIdx);
    sendResult(result, requestId);
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

    var result = discoverParams(trackIdx, deviceIdx);
    sendResult(result, requestId);
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

function sendResponse(jsonStr) {
    // Base64-encode the response so it travels safely through Max messaging
    // (Max treats curly braces as special characters)
    var encoded = _base64encode(jsonStr);
    outlet(0, encoded);
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

// ---------------------------------------------------------------------------
// LOM access: discover all parameters for a device
// ---------------------------------------------------------------------------
function discoverParams(trackIdx, deviceIdx) {
    var devicePath = "live_set tracks " + trackIdx + " devices " + deviceIdx;
    var deviceApi  = new LiveAPI(null, devicePath);

    if (!deviceApi || !deviceApi.id || parseInt(deviceApi.id) === 0) {
        return { error: "No device found at track " + trackIdx + " device " + deviceIdx + "." };
    }

    var deviceName  = deviceApi.get("name").toString();
    var deviceClass = deviceApi.get("class_name").toString();

    var paramCount = parseInt(deviceApi.getcount("parameters"));
    var parameters = [];

    for (var i = 0; i < paramCount; i++) {
        var paramPath = devicePath + " parameters " + i;
        var paramApi  = new LiveAPI(null, paramPath);

        if (!paramApi || !paramApi.id || parseInt(paramApi.id) === 0) {
            continue;
        }

        var paramInfo = readParamInfo(paramApi, i);
        parameters.push(paramInfo);
    }

    return {
        device_name:     deviceName,
        device_class:    deviceClass,
        parameter_count: parameters.length,
        parameters:      parameters
    };
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

    var paramName = paramApi.get("name").toString();
    var minVal    = parseFloat(paramApi.get("min"));
    var maxVal    = parseFloat(paramApi.get("max"));

    var clamped = Math.max(minVal, Math.min(maxVal, value));
    paramApi.set("value", clamped);

    var actualValue = parseFloat(paramApi.get("value"));

    return {
        parameter_name:  paramName,
        parameter_index: paramIdx,
        requested_value: value,
        actual_value:    actualValue,
        was_clamped:     (clamped !== value)
    };
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
