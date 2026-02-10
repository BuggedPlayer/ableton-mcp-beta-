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


def _normalize_display(s):
    """Remove all whitespace and lowercase for robust display string comparison."""
    return "".join(s.split()).lower()


def _resolve_display_value_bruteforce(param, display_string, ctrl=None):
    """For non-quantized params, find the raw value that produces a display string.

    Iterates integer values in [min..max], checks param.str_for_value(v).
    Works for params like LFO Rate (0-21) where each integer = a note value.
    Uses aggressive normalization (strip all whitespace) for robust matching.
    """
    target_norm = _normalize_display(display_string)

    lo = int(param.min)
    hi = int(param.max)
    if ctrl:
        ctrl.log_message("Bruteforce resolve '{0}' (norm: '{1}') for '{2}' (range {3}-{4})".format(
            display_string, target_norm, param.name, lo, hi))

    for v in range(lo, hi + 1):
        try:
            disp = param.str_for_value(float(v))
            if disp is None:
                continue
            disp_norm = _normalize_display(disp)
            if disp_norm == target_norm:
                if ctrl:
                    ctrl.log_message("  MATCH at v={0}".format(v))
                return float(v)
        except Exception as e:
            if ctrl:
                ctrl.log_message("  v={0} -> ERROR: {1}".format(v, e))
            continue

    raise ValueError("'{0}' not matched for '{1}' (range {2}-{3})".format(
        display_string, param.name, param.min, param.max
    ))


def _resolve_display_value(param, display_string, ctrl=None):
    """Resolve a display string to its raw value.

    For quantized params with value_items: direct lookup (fast).
    For non-quantized params: brute-force str_for_value scan.
    """
    if ctrl:
        ctrl.log_message("Resolve display '{0}' for param '{1}' (quantized={2})".format(
            display_string, param.name, param.is_quantized))

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
    return _resolve_display_value_bruteforce(param, display_string, ctrl)


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
            value = _resolve_display_value(target_param, value_display, ctrl)

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
                if ctrl:
                    ctrl.log_message("Batch resolve: '{0}' value_display='{1}'".format(pname, value_display))
                try:
                    pvalue = _resolve_display_value(target, value_display, ctrl)
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


# --- Drum Pad Operations ---


def _get_drum_rack(song, track_index, device_index):
    """Resolve a drum rack device, raising if not found or not a drum rack."""
    if track_index < 0 or track_index >= len(song.tracks):
        raise IndexError("Track index out of range")
    track = song.tracks[track_index]
    if device_index < 0 or device_index >= len(track.devices):
        raise IndexError("Device index out of range")
    device = track.devices[device_index]
    if not device.can_have_drum_pads:
        raise Exception("Device '{0}' is not a Drum Rack".format(device.name))
    return device


def get_drum_pads(song, track_index, device_index, ctrl=None):
    """Get drum pad info from a drum rack device."""
    try:
        device = _get_drum_rack(song, track_index, device_index)
        pads = []
        for pad in device.drum_pads:
            pad_info = {
                "note": pad.note,
                "name": pad.name,
                "mute": pad.mute,
                "solo": pad.solo,
            }
            try:
                pad_info["has_chains"] = len(pad.chains) > 0 if pad.chains else False
            except Exception:
                pad_info["has_chains"] = False
            pads.append(pad_info)
        return {
            "device_name": device.name,
            "track_index": track_index,
            "device_index": device_index,
            "pads": pads,
            "pad_count": len(pads),
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting drum pads: " + str(e))
        raise


def set_drum_pad(song, track_index, device_index, note, mute=None, solo=None, ctrl=None):
    """Set mute/solo on a drum pad by MIDI note number."""
    try:
        device = _get_drum_rack(song, track_index, device_index)
        note = int(note)
        target_pad = None
        for pad in device.drum_pads:
            if pad.note == note:
                target_pad = pad
                break
        if target_pad is None:
            raise ValueError("No drum pad found for MIDI note {0}".format(note))
        if mute is not None:
            target_pad.mute = bool(mute)
        if solo is not None:
            target_pad.solo = bool(solo)
        return {
            "note": target_pad.note,
            "name": target_pad.name,
            "mute": target_pad.mute,
            "solo": target_pad.solo,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error setting drum pad: " + str(e))
        raise


def copy_drum_pad(song, track_index, device_index, source_note, dest_note, ctrl=None):
    """Copy drum pad contents from source to destination note."""
    try:
        device = _get_drum_rack(song, track_index, device_index)
        source_note = int(source_note)
        dest_note = int(dest_note)
        if not hasattr(device, 'copy_pad'):
            raise Exception("copy_pad not available in this Live version")
        device.copy_pad(source_note, dest_note)
        src_name = ""
        dst_name = ""
        for pad in device.drum_pads:
            if pad.note == source_note:
                src_name = pad.name
            if pad.note == dest_note:
                dst_name = pad.name
        return {
            "source_note": source_note,
            "source_name": src_name,
            "dest_note": dest_note,
            "dest_name": dst_name,
            "copied": True,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error copying drum pad: " + str(e))
        raise


# --- Rack Macro Variations ---


def _get_rack_device(song, track_index, device_index):
    """Resolve a rack device, raising if not a rack."""
    if track_index < 0 or track_index >= len(song.tracks):
        raise IndexError("Track index out of range")
    track = song.tracks[track_index]
    if device_index < 0 or device_index >= len(track.devices):
        raise IndexError("Device index out of range")
    device = track.devices[device_index]
    if not device.can_have_chains:
        raise Exception("Device '{0}' is not a Rack".format(device.name))
    return device


def get_rack_variations(song, track_index, device_index, ctrl=None):
    """Read variation count, selected index, and macro mapping status."""
    try:
        device = _get_rack_device(song, track_index, device_index)
        return {
            "device_name": device.name,
            "track_index": track_index,
            "device_index": device_index,
            "variation_count": getattr(device, "variation_count", 0),
            "selected_variation_index": getattr(device, "selected_variation_index", -1),
            "has_macro_mappings": getattr(device, "has_macro_mappings", False),
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting rack variations: " + str(e))
        raise


def rack_variation_action(song, track_index, device_index, action, variation_index=None, ctrl=None):
    """Perform a variation action on a rack device.

    Args:
        action: 'store', 'recall', 'delete', or 'randomize'
        variation_index: Required for 'recall' and 'delete'. Sets selected_variation_index first.
    """
    try:
        device = _get_rack_device(song, track_index, device_index)
        result = {"device_name": device.name, "action": action}
        if action == "store":
            device.store_variation()
            result["variation_count"] = device.variation_count
            result["selected_variation_index"] = device.selected_variation_index
        elif action == "recall":
            if variation_index is None:
                raise ValueError("variation_index is required for 'recall'")
            device.selected_variation_index = int(variation_index)
            device.recall_selected_variation()
            result["selected_variation_index"] = device.selected_variation_index
        elif action == "delete":
            if variation_index is None:
                raise ValueError("variation_index is required for 'delete'")
            device.selected_variation_index = int(variation_index)
            device.delete_selected_variation()
            result["variation_count"] = device.variation_count
        elif action == "randomize":
            device.randomize_macros()
            result["randomized"] = True
        else:
            raise ValueError("Unknown action '{0}'. Must be store, recall, delete, or randomize".format(action))
        return result
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error in rack variation action: " + str(e))
        raise


# --- Simpler-to-Drum-Rack Conversion ---


def sliced_simpler_to_drum_rack(song, track_index, device_index, ctrl=None):
    """Convert a sliced Simpler device to a Drum Rack."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        track = song.tracks[track_index]
        if device_index < 0 or device_index >= len(track.devices):
            raise IndexError("Device index out of range")
        device = track.devices[device_index]
        try:
            from Live.Conversions import sliced_simpler_to_drum_rack as _convert
        except ImportError:
            raise Exception("sliced_simpler_to_drum_rack requires Live 12+")
        device_name = device.name
        _convert(song, device)
        return {
            "converted": True,
            "source_device": device_name,
            "track_index": track_index,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error converting Simpler to Drum Rack: " + str(e))
        raise


# --- Compressor Side-Chain Routing ---


def _get_compressor_device(song, track_index, device_index):
    """Resolve a Compressor device, raising if not found or not a Compressor."""
    if track_index < 0 or track_index >= len(song.tracks):
        raise IndexError("Track index out of range")
    track = song.tracks[track_index]
    if device_index < 0 or device_index >= len(track.devices):
        raise IndexError("Device index out of range")
    device = track.devices[device_index]
    if "compressor" not in device.class_name.lower():
        raise Exception("Device '{0}' is not a Compressor (class: {1})".format(
            device.name, device.class_name))
    return device


def _get_sidechain_io(device):
    """Get the sidechain DeviceIO from a compressor, or None if unavailable.

    The CompressorDevice exposes input_routing_type/channel as read-only.
    The writable path is through device.input_routings[0] (a DeviceIO object)
    whose routing_type and routing_channel ARE settable.
    """
    try:
        if hasattr(device, 'input_routings') and len(device.input_routings) > 0:
            return device.input_routings[0]
    except Exception:
        pass
    return None


def get_compressor_sidechain(song, track_index, device_index, ctrl=None):
    """Get side-chain routing info from a Compressor device."""
    try:
        device = _get_compressor_device(song, track_index, device_index)
        result = {
            "device_name": device.name,
            "track_index": track_index,
            "device_index": device_index,
        }
        sidechain_io = _get_sidechain_io(device)
        if sidechain_io:
            # Preferred: read from DeviceIO (also writable)
            try:
                result["input_routing_type"] = str(sidechain_io.routing_type.display_name)
            except Exception:
                result["input_routing_type"] = None
            try:
                result["input_routing_channel"] = str(sidechain_io.routing_channel.display_name)
            except Exception:
                result["input_routing_channel"] = None
            try:
                result["available_input_types"] = [
                    str(r.display_name) for r in sidechain_io.available_routing_types
                ]
            except Exception:
                result["available_input_types"] = []
            try:
                result["available_input_channels"] = [
                    str(r.display_name) for r in sidechain_io.available_routing_channels
                ]
            except Exception:
                result["available_input_channels"] = []
            result["routing_via"] = "DeviceIO"
        else:
            # Fallback: read-only properties on CompressorDevice
            try:
                result["input_routing_type"] = str(device.input_routing_type.display_name)
            except Exception:
                result["input_routing_type"] = None
            try:
                result["input_routing_channel"] = str(device.input_routing_channel.display_name)
            except Exception:
                result["input_routing_channel"] = None
            try:
                result["available_input_types"] = [
                    str(r.display_name) for r in device.available_input_routing_types
                ]
            except Exception:
                result["available_input_types"] = []
            try:
                result["available_input_channels"] = [
                    str(r.display_name) for r in device.available_input_routing_channels
                ]
            except Exception:
                result["available_input_channels"] = []
            result["routing_via"] = "CompressorDevice (read-only)"
        return result
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting compressor sidechain: " + str(e))
        raise


def set_compressor_sidechain(song, track_index, device_index,
                              input_type=None, input_channel=None, ctrl=None):
    """Set side-chain routing on a Compressor device by display name.

    Uses the DeviceIO path (device.input_routings[0]) which exposes writable
    routing_type/routing_channel, unlike the read-only properties on
    CompressorDevice itself.
    """
    try:
        device = _get_compressor_device(song, track_index, device_index)
        sidechain_io = _get_sidechain_io(device)
        if sidechain_io is None:
            raise Exception(
                "Cannot access sidechain routing on '{0}'. "
                "The device does not expose input_routings (DeviceIO).".format(device.name))
        changes = {}
        if input_type is not None:
            for rt in sidechain_io.available_routing_types:
                if str(rt.display_name) == input_type:
                    sidechain_io.routing_type = rt
                    changes["input_routing_type"] = input_type
                    break
            else:
                avail = ", ".join(str(r.display_name) for r in sidechain_io.available_routing_types)
                raise ValueError("Input type '{0}' not found. Available: {1}".format(input_type, avail))
        if input_channel is not None:
            for ch in sidechain_io.available_routing_channels:
                if str(ch.display_name) == input_channel:
                    sidechain_io.routing_channel = ch
                    changes["input_routing_channel"] = input_channel
                    break
            else:
                avail = ", ".join(str(r.display_name) for r in sidechain_io.available_routing_channels)
                raise ValueError("Input channel '{0}' not found. Available: {1}".format(input_channel, avail))
        changes["device_name"] = device.name
        changes["track_index"] = track_index
        changes["device_index"] = device_index
        return changes
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error setting compressor sidechain: " + str(e))
        raise


# --- EQ8 Controls ---


def _get_eq8_device(song, track_index, device_index):
    """Resolve an EQ Eight device, raising if not found or not an EQ Eight."""
    if track_index < 0 or track_index >= len(song.tracks):
        raise IndexError("Track index out of range")
    track = song.tracks[track_index]
    if device_index < 0 or device_index >= len(track.devices):
        raise IndexError("Device index out of range")
    device = track.devices[device_index]
    if "eq8" not in device.class_name.lower():
        raise Exception("Device '{0}' is not an EQ Eight (class: {1})".format(
            device.name, device.class_name))
    return device


def get_eq8_properties(song, track_index, device_index, ctrl=None):
    """Get EQ8-specific properties: edit_mode, global_mode, oversample, selected_band."""
    try:
        device = _get_eq8_device(song, track_index, device_index)
        result = {
            "device_name": device.name,
            "track_index": track_index,
            "device_index": device_index,
        }
        try:
            result["edit_mode"] = int(device.edit_mode)
        except Exception:
            result["edit_mode"] = None
        try:
            result["global_mode"] = int(device.global_mode)
        except Exception:
            result["global_mode"] = None
        try:
            result["oversample"] = bool(device.oversample)
        except Exception:
            result["oversample"] = None
        try:
            result["selected_band"] = int(device.view.selected_band)
        except Exception:
            result["selected_band"] = None
        result["edit_mode_labels"] = {0: "a", 1: "b"}
        result["global_mode_labels"] = {0: "stereo", 1: "left_right", 2: "mid_side"}
        return result
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting EQ8 properties: " + str(e))
        raise


def set_eq8_properties(song, track_index, device_index,
                        edit_mode=None, global_mode=None,
                        oversample=None, selected_band=None, ctrl=None):
    """Set EQ8-specific properties."""
    try:
        device = _get_eq8_device(song, track_index, device_index)
        changes = {}
        if edit_mode is not None:
            edit_mode = int(edit_mode)
            if edit_mode not in (0, 1):
                raise ValueError("edit_mode must be 0 (a) or 1 (b)")
            device.edit_mode = edit_mode
            changes["edit_mode"] = edit_mode
        if global_mode is not None:
            global_mode = int(global_mode)
            if global_mode not in (0, 1, 2):
                raise ValueError("global_mode must be 0 (stereo), 1 (left_right), or 2 (mid_side)")
            device.global_mode = global_mode
            changes["global_mode"] = global_mode
        if oversample is not None:
            device.oversample = bool(oversample)
            changes["oversample"] = bool(oversample)
        if selected_band is not None:
            selected_band = int(selected_band)
            if selected_band < 0 or selected_band > 7:
                raise ValueError("selected_band must be 0-7")
            device.view.selected_band = selected_band
            changes["selected_band"] = selected_band
        changes["device_name"] = device.name
        changes["track_index"] = track_index
        changes["device_index"] = device_index
        return changes
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error setting EQ8 properties: " + str(e))
        raise


# --- Hybrid Reverb IR ---


def _get_hybrid_reverb_device(song, track_index, device_index):
    """Resolve a Hybrid Reverb device, raising if not found or wrong type."""
    if track_index < 0 or track_index >= len(song.tracks):
        raise IndexError("Track index out of range")
    track = song.tracks[track_index]
    if device_index < 0 or device_index >= len(track.devices):
        raise IndexError("Device index out of range")
    device = track.devices[device_index]
    if "hybrid" not in device.class_name.lower():
        raise Exception("Device '{0}' is not a Hybrid Reverb (class: {1})".format(
            device.name, device.class_name))
    return device


def get_hybrid_reverb_ir(song, track_index, device_index, ctrl=None):
    """Get IR configuration from a Hybrid Reverb device."""
    try:
        device = _get_hybrid_reverb_device(song, track_index, device_index)
        result = {
            "device_name": device.name,
            "track_index": track_index,
            "device_index": device_index,
        }
        try:
            result["ir_category_index"] = int(device.ir_category_index)
        except Exception:
            result["ir_category_index"] = None
        try:
            result["ir_category_list"] = [str(c) for c in device.ir_category_list]
        except Exception:
            result["ir_category_list"] = []
        try:
            result["ir_file_index"] = int(device.ir_file_index)
        except Exception:
            result["ir_file_index"] = None
        try:
            result["ir_file_list"] = [str(f) for f in device.ir_file_list]
        except Exception:
            result["ir_file_list"] = []
        try:
            result["ir_attack_time"] = float(device.ir_attack_time)
        except Exception:
            result["ir_attack_time"] = None
        try:
            result["ir_decay_time"] = float(device.ir_decay_time)
        except Exception:
            result["ir_decay_time"] = None
        try:
            result["ir_size_factor"] = float(device.ir_size_factor)
        except Exception:
            result["ir_size_factor"] = None
        try:
            result["ir_time_shaping_on"] = bool(device.ir_time_shaping_on)
        except Exception:
            result["ir_time_shaping_on"] = None
        return result
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting Hybrid Reverb IR: " + str(e))
        raise


def set_hybrid_reverb_ir(song, track_index, device_index,
                          ir_category_index=None, ir_file_index=None,
                          ir_attack_time=None, ir_decay_time=None,
                          ir_size_factor=None, ir_time_shaping_on=None, ctrl=None):
    """Set IR configuration on a Hybrid Reverb device."""
    try:
        device = _get_hybrid_reverb_device(song, track_index, device_index)
        changes = {}
        if ir_category_index is not None:
            device.ir_category_index = int(ir_category_index)
            changes["ir_category_index"] = int(ir_category_index)
        if ir_file_index is not None:
            device.ir_file_index = int(ir_file_index)
            changes["ir_file_index"] = int(ir_file_index)
        if ir_attack_time is not None:
            device.ir_attack_time = float(ir_attack_time)
            changes["ir_attack_time"] = float(ir_attack_time)
        if ir_decay_time is not None:
            device.ir_decay_time = float(ir_decay_time)
            changes["ir_decay_time"] = float(ir_decay_time)
        if ir_size_factor is not None:
            device.ir_size_factor = float(ir_size_factor)
            changes["ir_size_factor"] = float(ir_size_factor)
        if ir_time_shaping_on is not None:
            device.ir_time_shaping_on = bool(ir_time_shaping_on)
            changes["ir_time_shaping_on"] = bool(ir_time_shaping_on)
        changes["device_name"] = device.name
        changes["track_index"] = track_index
        changes["device_index"] = device_index
        return changes
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error setting Hybrid Reverb IR: " + str(e))
        raise
