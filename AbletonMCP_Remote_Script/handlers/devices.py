"""Device parameter resolution, get/set parameters, track_type support, macros."""

from __future__ import absolute_import, print_function, unicode_literals


def resolve_track(song, track_index, track_type="track"):
    """Resolve a track by index and type (track, return, master)."""
    if track_type == "return":
        if track_index < 0 or track_index >= len(song.return_tracks):
            raise IndexError("Return track index out of range")
        return song.return_tracks[track_index]
    elif track_type == "master":
        return song.master_track
    else:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        return song.tracks[track_index]


def get_device_type(device, ctrl=None):
    """Get the type of a device."""
    try:
        if device.can_have_drum_pads:
            return "drum_machine"
        elif device.can_have_chains:
            return "rack"
        elif "instrument" in device.class_display_name.lower():
            return "instrument"
        elif "audio_effect" in device.class_name.lower():
            return "audio_effect"
        elif "midi_effect" in device.class_name.lower():
            return "midi_effect"
        else:
            return "unknown"
    except Exception:
        return "unknown"


def _resolve_display_value_bruteforce(param, display_string):
    """For non-quantized params, find the raw value that produces a display string.

    Iterates integer values in [min..max], checks param.str_for_value(v).
    Works for params like LFO Rate (0-21) where each integer = a note value.
    """
    target = display_string.strip()
    target_lower = target.lower()

    lo = int(param.min)
    hi = int(param.max)
    for v in range(lo, hi + 1):
        try:
            disp = param.str_for_value(float(v))
            if disp is None:
                continue
            disp = disp.strip()
            if disp == target or disp.lower() == target_lower:
                return float(v)
        except Exception:
            continue

    raise ValueError("'{0}' not matched for '{1}' (range {2}-{3})".format(
        display_string, param.name, param.min, param.max
    ))


def _resolve_display_value(param, display_string):
    """Resolve a display string to its raw value.

    For quantized params with value_items: direct lookup (fast).
    For non-quantized params: brute-force str_for_value scan.
    """
    # Fast path: quantized with value_items
    if param.is_quantized:
        items = list(param.value_items)
        if items:
            num = len(items)
            step = (param.max - param.min) / max(num - 1, 1)
            for i, item in enumerate(items):
                if item == display_string:
                    return param.min + i * step
            lower = display_string.lower()
            for i, item in enumerate(items):
                if item.lower() == lower:
                    return param.min + i * step
            raise ValueError("'{0}' not found in value_items for '{1}'. Options: {2}".format(
                display_string, param.name, ", ".join(items)
            ))

    # Non-quantized: brute-force via str_for_value
    return _resolve_display_value_bruteforce(param, display_string)


def get_device_parameters(song, track_index, device_index, track_type="track", ctrl=None):
    """Get all parameters for a device on any track type."""
    try:
        track = resolve_track(song, track_index, track_type)
        device_list = list(track.devices)
        if ctrl:
            ctrl.log_message(
                "Track '" + str(track.name) + "' has " + str(len(device_list)) + " devices"
            )
        if device_index < 0 or device_index >= len(device_list):
            raise IndexError(
                "Device index out of range (have " + str(len(device_list)) + " devices)"
            )
        device = device_list[device_index]

        parameters = []
        for i, param in enumerate(device.parameters):
            param_info = {
                "index": i,
                "name": param.name,
                "value": param.value,
                "min": param.min,
                "max": param.max,
                "is_quantized": param.is_quantized,
                "value_items": list(param.value_items) if param.is_quantized else [],
            }
            try:
                param_info["display_value"] = param.str_for_value(param.value)
            except Exception:
                pass
            parameters.append(param_info)

        return {
            "device_name": device.name,
            "device_type": device.class_name,
            "parameters": parameters,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting device parameters: " + str(e))
        raise


def set_device_parameter(
    song, track_index, device_index, parameter_name, value,
    track_type="track", value_display=None, ctrl=None
):
    """Set a device parameter by name on any track type.

    value_display: optional display string (e.g. '1/4') for quantized params.
    If provided, overrides the numeric value.
    """
    try:
        track = resolve_track(song, track_index, track_type)
        device_list = list(track.devices)
        if device_index < 0 or device_index >= len(device_list):
            raise IndexError("Device index out of range")
        device = device_list[device_index]

        # Find the parameter by name
        target_param = None
        for param in device.parameters:
            if param.name == parameter_name:
                target_param = param
                break

        if target_param is None:
            raise ValueError("Parameter '{0}' not found on device '{1}'".format(
                parameter_name, device.name
            ))

        # Resolve display string to raw value if provided
        if value_display is not None:
            value = _resolve_display_value(target_param, value_display)

        # Clamp value to valid range
        clamped = max(target_param.min, min(target_param.max, value))
        target_param.value = clamped

        display = None
        try:
            display = target_param.str_for_value(target_param.value)
        except Exception:
            pass

        result = {
            "device_name": device.name,
            "parameter_name": target_param.name,
            "value": target_param.value,
            "clamped": clamped != value,
            "track_type": track_type,
        }
        if display is not None:
            result["display_value"] = display
        return result
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error setting device parameter: " + str(e))
        raise


def set_device_parameters_batch(
    song, track_index, device_index, parameters, track_type="track", ctrl=None
):
    """Set multiple device parameters at once.

    parameters is a list of dicts with 'name' and either 'value' (numeric)
    or 'value_display' (display string like '1/4') for quantized params.
    """
    try:
        track = resolve_track(song, track_index, track_type)
        device_list = list(track.devices)
        if device_index < 0 or device_index >= len(device_list):
            raise IndexError("Device index out of range")
        device = device_list[device_index]

        # Build a name->param lookup once
        param_map = {}
        for param in device.parameters:
            param_map[param.name] = param

        results = []
        for entry in parameters:
            pname = entry.get("name", "")
            pvalue = entry.get("value", 0.0)
            value_display = entry.get("value_display")
            target = param_map.get(pname)
            if target is None:
                results.append({"name": pname, "error": "not found"})
                continue
            # Resolve display string if provided
            if value_display is not None:
                try:
                    pvalue = _resolve_display_value(target, value_display)
                except ValueError as ve:
                    results.append({"name": pname, "error": str(ve)})
                    continue
            clamped = max(target.min, min(target.max, pvalue))
            target.value = clamped
            entry_result = {"name": target.name, "value": target.value, "clamped": clamped != pvalue}
            try:
                entry_result["display_value"] = target.str_for_value(target.value)
            except Exception:
                pass
            results.append(entry_result)

        return {
            "device_name": device.name,
            "track_type": track_type,
            "results": results,
            "count": len(results),
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error in batch set parameters: " + str(e))
        raise


def delete_device(song, track_index, device_index, ctrl=None):
    """Delete a device from a track."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        track = song.tracks[track_index]
        if device_index < 0 or device_index >= len(track.devices):
            raise IndexError("Device index out of range")
        device = track.devices[device_index]
        device_name = device.name
        track.delete_device(device_index)
        return {
            "deleted": True,
            "device_name": device_name,
            "track_index": track_index,
            "device_index": device_index,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error deleting device: " + str(e))
        raise


# --- Macro helpers (new from MacWhite) ---


def get_macro_values(song, track_index, device_index, ctrl=None):
    """Get the values of all macro controls on a rack device."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        track = song.tracks[track_index]
        if device_index < 0 or device_index >= len(track.devices):
            raise IndexError("Device index out of range")
        device = track.devices[device_index]
        if not hasattr(device, "macros_mapped"):
            raise Exception("Device is not a rack (no macros)")

        macros = []
        for i in range(8):
            param_index = i + 1
            if param_index < len(device.parameters):
                macro_param = device.parameters[param_index]
                macros.append({
                    "index": i,
                    "name": macro_param.name,
                    "value": macro_param.value,
                    "min": macro_param.min,
                    "max": macro_param.max,
                    "is_enabled": getattr(macro_param, "is_enabled", True),
                })

        return {
            "track_index": track_index,
            "device_index": device_index,
            "device_name": device.name,
            "macros": macros,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting macro values: " + str(e))
        raise


def set_macro_value(song, track_index, device_index, macro_index, value, ctrl=None):
    """Set the value of a specific macro control on a rack device."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        track = song.tracks[track_index]
        if device_index < 0 or device_index >= len(track.devices):
            raise IndexError("Device index out of range")
        device = track.devices[device_index]
        if not hasattr(device, "macros_mapped"):
            raise Exception("Device is not a rack (no macros)")
        if macro_index < 0 or macro_index > 7:
            raise IndexError("Macro index must be 0-7")

        param_index = macro_index + 1
        if param_index >= len(device.parameters):
            raise Exception("Macro {0} not available on this device".format(macro_index + 1))

        macro_param = device.parameters[param_index]
        macro_param.value = max(macro_param.min, min(macro_param.max, value))

        return {
            "track_index": track_index,
            "device_index": device_index,
            "macro_index": macro_index,
            "macro_name": macro_param.name,
            "value": macro_param.value,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error setting macro value: " + str(e))
        raise
