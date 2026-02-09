"""Automation: clip automation, track-level automation, arrangement time editing."""

from __future__ import absolute_import, print_function, unicode_literals

import traceback


def _find_parameter(song, track_index, parameter_name):
    """Find a track mixer or device parameter by name."""
    track = song.tracks[track_index]
    lower = parameter_name.lower()

    # Check mixer parameters
    if lower == "volume":
        return track.mixer_device.volume
    elif lower in ("pan", "panning"):
        return track.mixer_device.panning

    # Check send parameters
    if lower.startswith("send"):
        send_char = lower.replace("send", "").strip()
        if send_char:
            send_index = ord(send_char[0].upper()) - ord("A")
            if 0 <= send_index < len(track.mixer_device.sends):
                return track.mixer_device.sends[send_index]

    # Check device parameters
    for device in track.devices:
        for p in device.parameters:
            if p.name.lower() == lower:
                return p

    raise ValueError("Parameter '{0}' not found".format(parameter_name))


def create_clip_automation(song, track_index, clip_index, parameter_name, automation_points, ctrl=None):
    """Create automation for a parameter within a clip."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")

        # Get the clip
        track = song.tracks[track_index]
        if clip_index < 0 or clip_index >= len(track.clip_slots):
            raise IndexError("Clip index out of range")
        clip_slot = track.clip_slots[clip_index]
        if not clip_slot.has_clip:
            raise Exception("No clip in slot")
        clip = clip_slot.clip

        # Find the parameter
        param = _find_parameter(song, track_index, parameter_name)

        if not hasattr(clip, 'automation_envelope'):
            if hasattr(clip, 'create_automation_envelope'):
                envelope = clip.create_automation_envelope(param)
            else:
                raise Exception("Clip does not support automation envelopes")
        else:
            envelope = clip.automation_envelope(param)

        if envelope is None:
            if hasattr(clip, 'create_automation_envelope'):
                envelope = clip.create_automation_envelope(param)
            if envelope is None:
                raise Exception("Could not get automation envelope for parameter '{0}'".format(parameter_name))

        # Clear existing automation so we start with a clean envelope
        if hasattr(envelope, 'clear'):
            try:
                envelope.clear()
            except Exception:
                pass

        # Insert breakpoints — Ableton linearly interpolates between them.
        # Use duration=0 to create simple breakpoints (not held steps).
        for point in automation_points:
            time_val = float(point.get("time", 0.0))
            value = float(point.get("value", 0.0))
            clamped = max(param.min, min(param.max, value))
            envelope.insert_step(time_val, 0.0, clamped)

        return {
            "parameter": parameter_name,
            "track_index": track_index,
            "clip_index": clip_index,
            "points_added": len(automation_points),
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error creating clip automation: " + str(e))
            ctrl.log_message(traceback.format_exc())
        raise


def get_clip_automation(song, track_index, clip_index, parameter_name, ctrl=None):
    """Read automation envelope from a clip."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")

        track = song.tracks[track_index]
        if clip_index < 0 or clip_index >= len(track.clip_slots):
            raise IndexError("Clip index out of range")
        clip_slot = track.clip_slots[clip_index]
        if not clip_slot.has_clip:
            raise Exception("No clip in slot")
        clip = clip_slot.clip

        param = _find_parameter(song, track_index, parameter_name)

        if not hasattr(clip, 'automation_envelope'):
            return {"has_automation": False, "parameter": parameter_name, "reason": "Clip does not support automation envelopes"}

        envelope = clip.automation_envelope(param)
        if envelope is None:
            return {"has_automation": False, "parameter": parameter_name}

        # Sample the envelope at evenly-spaced points
        num_samples = 64
        clip_len = clip.length
        if clip_len <= 0:
            return {"has_automation": False, "parameter": parameter_name, "reason": "Clip has zero length"}

        points = []
        step = clip_len / num_samples
        for i in range(num_samples + 1):
            t = i * step
            try:
                val = envelope.value_at_time(t)
                points.append({"time": round(t, 4), "value": round(val, 4)})
            except Exception:
                pass

        return {
            "has_automation": True,
            "parameter": parameter_name,
            "param_min": param.min,
            "param_max": param.max,
            "clip_length": clip_len,
            "point_count": len(points),
            "points": points,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting clip automation: " + str(e))
        raise


def clear_clip_automation(song, track_index, clip_index, parameter_name, ctrl=None):
    """Clear automation for a specific parameter in a clip."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")

        track = song.tracks[track_index]
        if clip_index < 0 or clip_index >= len(track.clip_slots):
            raise IndexError("Clip index out of range")
        clip_slot = track.clip_slots[clip_index]
        if not clip_slot.has_clip:
            raise Exception("No clip in slot")
        clip = clip_slot.clip

        param = _find_parameter(song, track_index, parameter_name)

        if not hasattr(clip, 'automation_envelope'):
            raise Exception("Clip does not support automation envelopes")

        envelope = clip.automation_envelope(param)
        if envelope is None:
            return {"cleared": False, "parameter": parameter_name, "reason": "No automation envelope found"}

        if hasattr(envelope, 'clear'):
            envelope.clear()
            return {"cleared": True, "parameter": parameter_name}
        else:
            raise Exception("Envelope does not support clear()")
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error clearing clip automation: " + str(e))
        raise


def list_clip_automated_params(song, track_index, clip_index, ctrl=None):
    """List all parameters that have automation in a clip."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")

        track = song.tracks[track_index]
        if clip_index < 0 or clip_index >= len(track.clip_slots):
            raise IndexError("Clip index out of range")
        clip_slot = track.clip_slots[clip_index]
        if not clip_slot.has_clip:
            raise Exception("No clip in slot")
        clip = clip_slot.clip

        if not hasattr(clip, 'automation_envelope'):
            return {"automated_parameters": [], "count": 0, "reason": "Clip does not support automation envelopes"}

        automated = []

        # Check mixer parameters
        for name, param in [("Volume", track.mixer_device.volume), ("Pan", track.mixer_device.panning)]:
            try:
                env = clip.automation_envelope(param)
                if env is not None:
                    automated.append({"name": name, "source": "Mixer"})
            except Exception:
                pass

        # Check send parameters
        for i, send in enumerate(track.mixer_device.sends):
            try:
                env = clip.automation_envelope(send)
                if env is not None:
                    automated.append({"name": "Send " + chr(65 + i), "source": "Mixer"})
            except Exception:
                pass

        # Check device parameters
        for dev_idx, device in enumerate(track.devices):
            for param in device.parameters:
                try:
                    env = clip.automation_envelope(param)
                    if env is not None:
                        automated.append({
                            "name": param.name,
                            "source": device.name,
                            "device_index": dev_idx,
                        })
                except Exception:
                    pass

        return {"automated_parameters": automated, "count": len(automated)}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error listing automated params: " + str(e))
        raise


# --- New: Track-level automation and arrangement time editing (from MacWhite) ---


def create_track_automation(song, track_index, parameter_name, automation_points, ctrl=None):
    """Create automation for a track parameter (arrangement-level)."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        parameter = _find_parameter(song, track_index, parameter_name)

        if not hasattr(parameter, "automation_envelope"):
            raise Exception("Parameter does not support automation")

        automation_envelope = parameter.automation_envelope
        for point in automation_points:
            time_val = float(point["time"])
            value = max(parameter.min, min(parameter.max, float(point["value"])))
            automation_envelope.insert_step(time_val, 0.0, value)

        return {
            "parameter": parameter_name,
            "track_index": track_index,
            "points_added": len(automation_points),
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error creating track automation: " + str(e))
            ctrl.log_message(traceback.format_exc())
        raise


def clear_track_automation(song, track_index, parameter_name, start_time, end_time, ctrl=None):
    """Clear automation for a parameter in a time range."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        parameter = _find_parameter(song, track_index, parameter_name)

        if not hasattr(parameter, "automation_envelope"):
            raise Exception("Parameter does not support automation")

        if end_time <= start_time:
            msg = "End time must be greater than start time"
            if ctrl:
                ctrl.log_message("Invalid clear range: " + msg)
            raise ValueError(msg)

        automation_envelope = parameter.automation_envelope
        current_value = parameter.value
        automation_envelope.insert_step(start_time, end_time - start_time, current_value)

        return {
            "parameter": parameter_name,
            "track_index": track_index,
            "cleared_from": start_time,
            "cleared_to": end_time,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error clearing track automation: " + str(e))
        raise


def delete_time(song, start_time, end_time, ctrl=None):
    """Delete a section of time from the arrangement."""
    try:
        if start_time >= end_time:
            raise ValueError("Start time must be less than end time")
        song.delete_time(start_time, end_time - start_time)
        return {
            "deleted_from": start_time,
            "deleted_to": end_time,
            "deleted_length": end_time - start_time,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error deleting time: " + str(e))
        raise


def duplicate_time(song, start_time, end_time, ctrl=None):
    """Duplicate a section of time in the arrangement."""
    try:
        if start_time >= end_time:
            raise ValueError("Start time must be less than end time")
        song.duplicate_time(start_time, end_time - start_time)
        return {
            "duplicated_from": start_time,
            "duplicated_to": end_time,
            "duplicated_length": end_time - start_time,
            "pasted_at": end_time,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error duplicating time: " + str(e))
        raise


def insert_silence(song, position, length, ctrl=None):
    """Insert silence at a position in the arrangement."""
    try:
        if length <= 0:
            raise ValueError("Length must be greater than 0")
        song.insert_time(position, length)
        return {"inserted_at": position, "inserted_length": length}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error inserting silence: " + str(e))
        raise
