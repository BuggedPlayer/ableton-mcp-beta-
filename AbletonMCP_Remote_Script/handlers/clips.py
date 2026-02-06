"""Clip creation, notes, naming, fire/stop, delete, colors, loop, markers."""

from __future__ import absolute_import, print_function, unicode_literals


def create_clip(song, track_index, clip_index, length, ctrl=None):
    """Create a new MIDI clip in the specified track and clip slot."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        track = song.tracks[track_index]
        if clip_index < 0 or clip_index >= len(track.clip_slots):
            raise IndexError("Clip index out of range")
        clip_slot = track.clip_slots[clip_index]
        if clip_slot.has_clip:
            raise Exception("Clip slot already has a clip")
        clip_slot.create_clip(length)
        return {
            "name": clip_slot.clip.name,
            "length": clip_slot.clip.length,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error creating clip: " + str(e))
        raise


def add_notes_to_clip(song, track_index, clip_index, notes, ctrl=None):
    """Add MIDI notes to a clip."""
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

        # Convert note data to Live's format with validation
        live_notes = []
        for note in notes:
            pitch = max(0, min(127, int(note.get("pitch", 60))))
            start_time = max(0.0, float(note.get("start_time", 0.0)))
            duration = max(0.01, float(note.get("duration", 0.25)))
            velocity = max(1, min(127, int(note.get("velocity", 100))))
            mute = bool(note.get("mute", False))
            live_notes.append((pitch, start_time, duration, velocity, mute))

        clip.set_notes(tuple(live_notes))
        return {"note_count": len(notes)}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error adding notes to clip: " + str(e))
        raise


def set_clip_name(song, track_index, clip_index, name, ctrl=None):
    """Set the name of a clip."""
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
        clip.name = name
        return {"name": clip.name}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error setting clip name: " + str(e))
        raise


def fire_clip(song, track_index, clip_index, ctrl=None):
    """Fire a clip."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        track = song.tracks[track_index]
        if clip_index < 0 or clip_index >= len(track.clip_slots):
            raise IndexError("Clip index out of range")
        clip_slot = track.clip_slots[clip_index]
        if not clip_slot.has_clip:
            raise Exception("No clip in slot")
        clip_slot.fire()
        return {"fired": True}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error firing clip: " + str(e))
        raise


def stop_clip(song, track_index, clip_index, ctrl=None):
    """Stop a clip."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        track = song.tracks[track_index]
        if clip_index < 0 or clip_index >= len(track.clip_slots):
            raise IndexError("Clip index out of range")
        clip_slot = track.clip_slots[clip_index]
        clip_slot.stop()
        return {"stopped": True}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error stopping clip: " + str(e))
        raise


def delete_clip(song, track_index, clip_index, ctrl=None):
    """Delete a clip from a clip slot."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        track = song.tracks[track_index]
        if clip_index < 0 or clip_index >= len(track.clip_slots):
            raise IndexError("Clip index out of range")
        clip_slot = track.clip_slots[clip_index]
        if not clip_slot.has_clip:
            raise Exception("No clip in slot")
        clip_name = clip_slot.clip.name
        clip_slot.delete_clip()
        return {
            "deleted": True,
            "clip_name": clip_name,
            "track_index": track_index,
            "clip_index": clip_index,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error deleting clip: " + str(e))
        raise


def get_clip_info(song, track_index, clip_index, ctrl=None):
    """Get detailed information about a clip."""
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

        result = {
            "name": clip.name,
            "length": clip.length,
            "is_playing": clip.is_playing,
            "is_recording": clip.is_recording,
            "is_midi_clip": hasattr(clip, 'get_notes'),
        }

        # Try to get additional properties if available
        try:
            if hasattr(clip, 'start_marker'):
                result["start_marker"] = clip.start_marker
            if hasattr(clip, 'end_marker'):
                result["end_marker"] = clip.end_marker
            if hasattr(clip, 'loop_start'):
                result["loop_start"] = clip.loop_start
            if hasattr(clip, 'loop_end'):
                result["loop_end"] = clip.loop_end
            if hasattr(clip, 'looping'):
                result["looping"] = clip.looping
            if hasattr(clip, 'warping'):
                result["warping"] = clip.warping
            if hasattr(clip, 'color_index'):
                result["color_index"] = clip.color_index
        except Exception:
            pass

        return result
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting clip info: " + str(e))
        raise


def duplicate_clip(song, track_index, clip_index, target_clip_index, ctrl=None):
    """Duplicate a clip to another slot on the same track."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        track = song.tracks[track_index]
        if clip_index < 0 or clip_index >= len(track.clip_slots):
            raise IndexError("Source clip index out of range")
        if target_clip_index < 0 or target_clip_index >= len(track.clip_slots):
            raise IndexError("Target clip index out of range")
        source_slot = track.clip_slots[clip_index]
        target_slot = track.clip_slots[target_clip_index]
        if not source_slot.has_clip:
            raise Exception("No clip in source slot")
        if target_slot.has_clip:
            raise Exception("Target slot already has a clip")
        source_slot.duplicate_clip_to(target_slot)
        return {
            "duplicated": True,
            "source_index": clip_index,
            "target_index": target_clip_index,
            "clip_name": source_slot.clip.name,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error duplicating clip: " + str(e))
        raise


def set_clip_looping(song, track_index, clip_index, looping, ctrl=None):
    """Set the looping state of a clip."""
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
        clip.looping = looping
        return {
            "track_index": track_index,
            "clip_index": clip_index,
            "looping": clip.looping,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error setting clip looping: " + str(e))
        raise


def set_clip_loop_points(song, track_index, clip_index, loop_start, loop_end, ctrl=None):
    """Set the loop start and end points of a clip."""
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

        # Set in safe order to avoid loop_start >= loop_end errors
        if loop_end > clip.loop_start:
            clip.loop_end = loop_end
            clip.loop_start = loop_start
        else:
            clip.loop_start = loop_start
            clip.loop_end = loop_end

        return {
            "track_index": track_index,
            "clip_index": clip_index,
            "loop_start": clip.loop_start,
            "loop_end": clip.loop_end,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error setting clip loop points: " + str(e))
        raise


def set_clip_color(song, track_index, clip_index, color_index, ctrl=None):
    """Set the color of a clip."""
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
        clip.color_index = color_index
        return {
            "track_index": track_index,
            "clip_index": clip_index,
            "color_index": clip.color_index,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error setting clip color: " + str(e))
        raise


def crop_clip(song, track_index, clip_index, ctrl=None):
    """Trim clip to its loop region."""
    try:
        clip = _get_clip(song, track_index, clip_index)
        if not hasattr(clip, 'crop'):
            raise Exception("clip.crop() not available in this Live version")
        clip.crop()
        return {
            "cropped": True,
            "new_length": clip.length,
            "clip_name": clip.name,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error cropping clip: " + str(e))
        raise


def duplicate_clip_loop(song, track_index, clip_index, ctrl=None):
    """Double the loop content of a clip."""
    try:
        clip = _get_clip(song, track_index, clip_index)
        if not hasattr(clip, 'duplicate_loop'):
            raise Exception("clip.duplicate_loop() not available in this Live version")
        old_length = clip.length
        clip.duplicate_loop()
        return {
            "old_length": old_length,
            "new_length": clip.length,
            "clip_name": clip.name,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error duplicating clip loop: " + str(e))
        raise


def set_clip_start_end(song, track_index, clip_index, start_marker, end_marker, ctrl=None):
    """Set clip start_marker and end_marker."""
    try:
        clip = _get_clip(song, track_index, clip_index)
        if start_marker is not None:
            clip.start_marker = float(start_marker)
        if end_marker is not None:
            clip.end_marker = float(end_marker)
        return {
            "start_marker": clip.start_marker,
            "end_marker": clip.end_marker,
            "clip_name": clip.name,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error setting clip start/end: " + str(e))
        raise


# --- Helper ---


def _get_clip(song, track_index, clip_index):
    """Get clip object with validation -- raises on invalid indices or empty slot."""
    if track_index < 0 or track_index >= len(song.tracks):
        raise IndexError("Track index out of range")
    track = song.tracks[track_index]
    if clip_index < 0 or clip_index >= len(track.clip_slots):
        raise IndexError("Clip index out of range")
    clip_slot = track.clip_slots[clip_index]
    if not clip_slot.has_clip:
        raise Exception("No clip in slot")
    return clip_slot.clip
