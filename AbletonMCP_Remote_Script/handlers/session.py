"""Session-level commands: tempo, playback, transport, loop, recording, metronome."""

from __future__ import absolute_import, print_function, unicode_literals


def get_session_info(song, ctrl=None):
    """Get information about the current session."""
    try:
        result = {
            "tempo": song.tempo,
            "signature_numerator": song.signature_numerator,
            "signature_denominator": song.signature_denominator,
            "track_count": len(song.tracks),
            "return_track_count": len(song.return_tracks),
            "master_track": {
                "name": "Master",
                "volume": song.master_track.mixer_device.volume.value,
                "panning": song.master_track.mixer_device.panning.value,
            },
        }
        return result
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting session info: " + str(e))
        raise


def set_tempo(song, tempo, ctrl=None):
    """Set the tempo of the session."""
    try:
        song.tempo = tempo
        return {"tempo": song.tempo}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error setting tempo: " + str(e))
        raise


def start_playback(song, ctrl=None):
    """Start playing the session."""
    try:
        song.start_playing()
        return {"playing": song.is_playing}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error starting playback: " + str(e))
        raise


def stop_playback(song, ctrl=None):
    """Stop playing the session."""
    try:
        song.stop_playing()
        return {"playing": song.is_playing}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error stopping playback: " + str(e))
        raise


def get_song_transport(song, ctrl=None):
    """Get transport/arrangement state."""
    try:
        result = {
            "current_time": song.current_song_time,
            "is_playing": song.is_playing,
            "tempo": song.tempo,
            "signature_numerator": song.signature_numerator,
            "signature_denominator": song.signature_denominator,
            "loop_enabled": song.loop,
            "loop_start": song.loop_start,
            "loop_length": song.loop_length,
            "song_length": song.song_length,
        }
        try:
            result["record_mode"] = song.record_mode
        except Exception:
            result["record_mode"] = False
        return result
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting song transport: " + str(e))
        raise


def set_song_time(song, time, ctrl=None):
    """Set the arrangement playhead position."""
    try:
        target = max(0.0, float(time))
        song.current_song_time = target
        return {"current_time": target}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error setting song time: " + str(e))
        raise


def set_song_loop(song, enabled, start, length, ctrl=None):
    """Control arrangement loop bracket."""
    try:
        if enabled is not None:
            song.loop = bool(enabled)
        if start is not None:
            song.loop_start = max(0.0, float(start))
        if length is not None:
            length_val = float(length)
            if length_val <= 0:
                raise ValueError("Loop length must be positive, got {0}".format(length_val))
            song.loop_length = length_val
        # Return the values we SET (not read-back, which can be stale)
        result = {}
        result["loop_enabled"] = bool(enabled) if enabled is not None else song.loop
        result["loop_start"] = max(0.0, float(start)) if start is not None else song.loop_start
        result["loop_length"] = float(length) if length is not None else song.loop_length
        return result
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error setting song loop: " + str(e))
        raise


# --- New commands from MacWhite ---


def get_loop_info(song, ctrl=None):
    """Get loop information."""
    try:
        return {
            "loop_start": song.loop_start,
            "loop_end": song.loop_start + song.loop_length,
            "loop_length": song.loop_length,
            "loop": song.loop,
            "current_song_time": song.current_song_time,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting loop info: " + str(e))
        raise


def set_loop_start(song, position, ctrl=None):
    """Set the loop start position."""
    try:
        song.loop_start = position
        return {"loop_start": song.loop_start, "loop_end": song.loop_start + song.loop_length}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error setting loop start: " + str(e))
        raise


def set_loop_end(song, position, ctrl=None):
    """Set the loop end position."""
    try:
        pos = float(position)
        if pos <= song.loop_start:
            raise ValueError("Loop end ({0}) must be greater than loop start ({1})".format(
                pos, song.loop_start))
        # loop_end isn't a direct property; compute via loop_length
        song.loop_length = pos - song.loop_start
        return {"loop_start": song.loop_start, "loop_end": song.loop_start + song.loop_length}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error setting loop end: " + str(e))
        raise


def set_loop_length(song, length, ctrl=None):
    """Set the loop length."""
    try:
        length_val = float(length)
        if length_val <= 0:
            raise ValueError("Loop length must be positive, got {0}".format(length_val))
        song.loop_length = length_val
        return {
            "loop_start": song.loop_start,
            "loop_end": song.loop_start + song.loop_length,
            "loop_length": song.loop_length,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error setting loop length: " + str(e))
        raise


def set_playback_position(song, position, ctrl=None):
    """Set the playback position."""
    try:
        song.current_song_time = max(0.0, float(position))
        return {"current_song_time": song.current_song_time}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error setting playback position: " + str(e))
        raise


def set_arrangement_overdub(song, enabled, ctrl=None):
    """Enable or disable arrangement overdub mode."""
    try:
        song.arrangement_overdub = enabled
        return {"arrangement_overdub": song.arrangement_overdub}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error setting arrangement overdub: " + str(e))
        raise


def start_arrangement_recording(song, ctrl=None):
    """Start recording into the arrangement view."""
    try:
        song.record_mode = True
        if not song.is_playing:
            song.start_playing()
        return {
            "recording": song.record_mode,
            "playing": song.is_playing,
            "arrangement_overdub": song.arrangement_overdub,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error starting arrangement recording: " + str(e))
        raise


def stop_arrangement_recording(song, stop_playback=True, ctrl=None):
    """Stop arrangement recording.

    Args:
        song: Live Song object.
        stop_playback: If True (default), also stops transport playback.
            Set to False to stop recording while keeping playback running
            (useful for punch-out workflows where you want to keep listening).
        ctrl: Optional controller for logging.
    """
    try:
        song.record_mode = False
        if stop_playback and song.is_playing:
            song.stop_playing()
        return {"recording": song.record_mode, "playing": song.is_playing}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error stopping arrangement recording: " + str(e))
        raise


def get_recording_status(song, ctrl=None):
    """Get the current recording status."""
    try:
        armed_tracks = []
        for i, track in enumerate(song.tracks):
            try:
                if track.can_be_armed and track.arm:
                    armed_tracks.append({
                        "index": i,
                        "name": track.name,
                        "is_midi": track.has_midi_input,
                        "is_audio": track.has_audio_input,
                    })
            except Exception:
                pass
        return {
            "record_mode": song.record_mode,
            "arrangement_overdub": song.arrangement_overdub,
            "session_record": song.session_record,
            "is_playing": song.is_playing,
            "armed_tracks": armed_tracks,
            "armed_track_count": len(armed_tracks),
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting recording status: " + str(e))
        raise


def set_metronome(song, enabled, ctrl=None):
    """Enable or disable the metronome."""
    try:
        song.metronome = enabled
        return {"metronome": song.metronome}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error setting metronome: " + str(e))
        raise


def tap_tempo(song, ctrl=None):
    """Tap tempo to set BPM."""
    try:
        song.tap_tempo()
        return {"tempo": song.tempo}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error tapping tempo: " + str(e))
        raise


# --- Undo / Redo ---


def undo(song, ctrl=None):
    """Undo the last action."""
    try:
        if not song.can_undo:
            return {"undone": False, "reason": "Nothing to undo"}
        song.undo()
        return {"undone": True}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error performing undo: " + str(e))
        raise


def redo(song, ctrl=None):
    """Redo the last undone action."""
    try:
        if not song.can_redo:
            return {"redone": False, "reason": "Nothing to redo"}
        song.redo()
        return {"redone": True}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error performing redo: " + str(e))
        raise


# --- Additional transport ---


def continue_playing(song, ctrl=None):
    """Continue playback from the current position (does not jump to start)."""
    try:
        song.continue_playing()
        return {"playing": song.is_playing, "position": song.current_song_time}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error continuing playback: " + str(e))
        raise


def re_enable_automation(song, ctrl=None):
    """Re-enable all automation that has been manually overridden."""
    try:
        song.re_enable_automation()
        return {"re_enabled": True}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error re-enabling automation: " + str(e))
        raise


# --- Cue points ---


def get_cue_points(song, ctrl=None):
    """Get all cue points (markers) in the arrangement."""
    try:
        cues = []
        for cue in song.cue_points:
            cues.append({
                "name": cue.name,
                "time": cue.time,
            })
        cues.sort(key=lambda c: c["time"])
        return {"cue_points": cues, "count": len(cues)}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting cue points: " + str(e))
        raise


def set_or_delete_cue(song, ctrl=None):
    """Toggle a cue point at the current playback position.

    If a cue point exists at the current position, it is deleted.
    Otherwise, a new cue point is created.
    """
    try:
        song.set_or_delete_cue()
        return {"position": song.current_song_time}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error toggling cue point: " + str(e))
        raise


def jump_to_cue(song, direction, ctrl=None):
    """Jump to the next or previous cue point.

    Args:
        direction: 'next' or 'prev'
    """
    try:
        if direction == "next":
            if not song.can_jump_to_next_cue:
                return {"jumped": False, "reason": "No next cue point"}
            song.jump_to_next_cue()
        elif direction == "prev":
            if not song.can_jump_to_prev_cue:
                return {"jumped": False, "reason": "No previous cue point"}
            song.jump_to_prev_cue()
        else:
            raise ValueError("direction must be 'next' or 'prev', got '{0}'".format(direction))
        return {"jumped": True, "position": song.current_song_time}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error jumping to cue: " + str(e))
        raise


def get_groove_pool(song, ctrl=None):
    """Read the groove pool: global groove amount and list of grooves with their params."""
    try:
        result = {
            "groove_amount": getattr(song, "groove_amount", 1.0),
            "grooves": [],
        }
        pool = getattr(song, "groove_pool", None)
        if pool is not None and hasattr(pool, "grooves"):
            for i, groove in enumerate(pool.grooves):
                groove_info = {
                    "index": i,
                    "name": getattr(groove, "name", "Groove {0}".format(i)),
                    "timing_amount": getattr(groove, "timing_amount", 0.0),
                    "quantization_amount": getattr(groove, "quantization_amount", 0.0),
                    "random_amount": getattr(groove, "random_amount", 0.0),
                    "velocity_amount": getattr(groove, "velocity_amount", 0.0),
                }
                result["grooves"].append(groove_info)
        result["groove_count"] = len(result["grooves"])
        return result
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting groove pool: " + str(e))
        raise


def set_groove_settings(song, groove_amount=None, groove_index=None,
                         timing_amount=None, quantization_amount=None,
                         random_amount=None, velocity_amount=None, ctrl=None):
    """Set global groove amount or individual groove parameters."""
    try:
        result = {}
        if groove_amount is not None:
            song.groove_amount = float(groove_amount)
            result["groove_amount"] = song.groove_amount
        if groove_index is not None:
            pool = getattr(song, "groove_pool", None)
            if pool is None or not hasattr(pool, "grooves"):
                raise Exception("Groove pool not available")
            grooves = list(pool.grooves)
            groove_index = int(groove_index)
            if groove_index < 0 or groove_index >= len(grooves):
                raise IndexError("Groove index {0} out of range (have {1} grooves)".format(
                    groove_index, len(grooves)))
            groove = grooves[groove_index]
            if timing_amount is not None:
                groove.timing_amount = float(timing_amount)
            if quantization_amount is not None:
                groove.quantization_amount = float(quantization_amount)
            if random_amount is not None:
                groove.random_amount = float(random_amount)
            if velocity_amount is not None:
                groove.velocity_amount = float(velocity_amount)
            result["groove_index"] = groove_index
            result["groove_name"] = getattr(groove, "name", "")
            result["timing_amount"] = groove.timing_amount
            result["quantization_amount"] = groove.quantization_amount
            result["random_amount"] = groove.random_amount
            result["velocity_amount"] = groove.velocity_amount
        if not result:
            raise ValueError("No parameters specified")
        return result
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error setting groove settings: " + str(e))
        raise
