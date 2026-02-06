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
            parameters.append({
                "index": i,
                "name": param.name,
                "value": param.value,
                "min": param.min,
                "max": param.max,
                "is_quantized": param.is_quantized,
                "value_items": list(param.value_items) if param.is_quantized else [],
            })

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
    song, track_index, device_index, parameter_name, value, track_type="track", ctrl=None
):
    """Set a device parameter by name on any track type."""
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

        # Clamp value to valid range
        clamped = max(target_param.min, min(target_param.max, value))
        target_param.value = clamped

        return {
            "device_name": device.name,
            "parameter_name": target_param.name,
            "value": target_param.value,
            "clamped": clamped != value,
            "track_type": track_type,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error setting device parameter: " + str(e))
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
