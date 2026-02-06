"""Track creation, deletion, properties, arm, color, group."""

from __future__ import absolute_import, print_function, unicode_literals


def get_track_info(song, track_index, ctrl=None):
    """Get information about a track."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")

        track = song.tracks[track_index]

        # Get clip slots
        clip_slots = []
        try:
            for slot_index, slot in enumerate(track.clip_slots):
                clip_info = None
                try:
                    if slot.has_clip:
                        clip = slot.clip
                        clip_info = {
                            "name": clip.name,
                            "length": clip.length if hasattr(clip, 'length') else 0,
                            "is_playing": clip.is_playing if hasattr(clip, 'is_playing') else False,
                            "is_recording": clip.is_recording if hasattr(clip, 'is_recording') else False,
                        }
                except Exception:
                    clip_info = None
                clip_slots.append({
                    "index": slot_index,
                    "has_clip": slot.has_clip,
                    "clip": clip_info,
                })
        except Exception:
            pass

        # Get devices
        from . import devices as dev_mod
        devices_list = []
        try:
            for device_index, device in enumerate(track.devices):
                devices_list.append({
                    "index": device_index,
                    "name": device.name,
                    "class_name": device.class_name,
                    "type": dev_mod.get_device_type(device, ctrl),
                })
        except Exception:
            pass

        # Safely read properties -- group tracks don't support all of these
        try:
            arm = track.arm if track.can_be_armed else False
        except Exception:
            arm = False

        try:
            is_group = track.is_foldable
        except Exception:
            is_group = False

        try:
            is_audio = track.has_audio_input
        except Exception:
            is_audio = False

        try:
            is_midi = track.has_midi_input
        except Exception:
            is_midi = False

        result = {
            "index": track_index,
            "name": track.name,
            "is_group_track": is_group,
            "is_audio_track": is_audio,
            "is_midi_track": is_midi,
            "mute": track.mute,
            "solo": track.solo,
            "arm": arm,
            "volume": track.mixer_device.volume.value,
            "panning": track.mixer_device.panning.value,
            "clip_slots": clip_slots,
            "devices": devices_list,
        }
        return result
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting track info: " + str(e))
        raise


def create_midi_track(song, index, ctrl=None):
    """Create a new MIDI track at the specified index."""
    try:
        song.create_midi_track(index)
        new_track_index = len(song.tracks) - 1 if index == -1 else index
        new_track = song.tracks[new_track_index]
        return {"index": new_track_index, "name": new_track.name}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error creating MIDI track: " + str(e))
        raise


def create_audio_track(song, index, ctrl=None):
    """Create a new audio track at the specified index."""
    try:
        song.create_audio_track(index)
        new_track_index = len(song.tracks) - 1 if index == -1 else index
        new_track = song.tracks[new_track_index]
        return {"index": new_track_index, "name": new_track.name}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error creating audio track: " + str(e))
        raise


def set_track_name(song, track_index, name, ctrl=None):
    """Set the name of a track."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        track = song.tracks[track_index]
        track.name = name
        return {"name": track.name}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error setting track name: " + str(e))
        raise


def delete_track(song, track_index, ctrl=None):
    """Delete a track from the session."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        track = song.tracks[track_index]
        track_name = track.name
        song.delete_track(track_index)
        return {
            "deleted": True,
            "track_name": track_name,
            "track_index": track_index,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error deleting track: " + str(e))
        raise


def duplicate_track(song, track_index, ctrl=None):
    """Duplicate a track with all its devices and clips."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        track = song.tracks[track_index]
        source_name = track.name
        song.duplicate_track(track_index)
        new_track_index = track_index + 1
        new_track = song.tracks[new_track_index]
        return {
            "duplicated": True,
            "source_index": track_index,
            "source_name": source_name,
            "new_index": new_track_index,
            "new_name": new_track.name,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error duplicating track: " + str(e))
        raise


# --- New commands from MacWhite ---


def create_return_track(song, ctrl=None):
    """Create a new return track."""
    try:
        song.create_return_track()
        new_index = len(song.return_tracks) - 1
        new_track = song.return_tracks[new_index]
        return {"index": new_index, "name": new_track.name}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error creating return track: " + str(e))
        raise


def set_track_color(song, track_index, color_index, ctrl=None):
    """Set track color."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        track = song.tracks[track_index]
        track.color_index = color_index
        return {"track_index": track_index, "color_index": track.color_index}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error setting track color: " + str(e))
        raise


def arm_track(song, track_index, ctrl=None):
    """Arm a track for recording."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        track = song.tracks[track_index]
        if not track.can_be_armed:
            raise Exception("Track cannot be armed (may be a group track or lack input)")
        track.arm = True
        return {
            "track_index": track_index,
            "track_name": track.name,
            "armed": track.arm,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error arming track: " + str(e))
        raise


def disarm_track(song, track_index, ctrl=None):
    """Disarm a track from recording."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        track = song.tracks[track_index]
        track.arm = False
        return {
            "track_index": track_index,
            "track_name": track.name,
            "armed": track.arm,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error disarming track: " + str(e))
        raise


def group_tracks(song, track_indices, name, ctrl=None):
    """Group tracks (selects first track for grouping)."""
    try:
        if not track_indices or len(track_indices) == 0:
            raise ValueError("No tracks specified")
        for i in track_indices:
            if i < 0 or i >= len(song.tracks):
                raise IndexError("Track index {0} out of range".format(i))
        song.view.selected_track = song.tracks[track_indices[0]]
        return {
            "grouped": True,
            "track_count": len(track_indices),
            "name": name,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error grouping tracks: " + str(e))
        raise


def get_all_tracks_info(song, ctrl=None):
    """Get summary info for all tracks at once."""
    try:
        tracks_list = []
        for i, track in enumerate(song.tracks):
            devices_list = []
            for d in track.devices:
                devices_list.append({"name": d.name, "class_name": d.class_name})
            track_info = {
                "index": i,
                "name": track.name,
                "is_audio": track.has_audio_input if hasattr(track, 'has_audio_input') else False,
                "is_midi": track.has_midi_input if hasattr(track, 'has_midi_input') else False,
                "mute": track.mute,
                "solo": track.solo,
                "volume": track.mixer_device.volume.value,
                "panning": track.mixer_device.panning.value,
                "color_index": track.color_index if hasattr(track, 'color_index') else 0,
                "devices": devices_list,
            }
            try:
                track_info["arm"] = track.arm if track.can_be_armed else False
            except Exception:
                track_info["arm"] = False
            try:
                track_info["is_group_track"] = track.is_foldable
            except Exception:
                track_info["is_group_track"] = False
            tracks_list.append(track_info)
        return {"tracks": tracks_list, "count": len(tracks_list)}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting all tracks info: " + str(e))
        raise


def get_return_tracks_info(song, ctrl=None):
    """Get info for all return tracks."""
    try:
        returns = []
        for i, track in enumerate(song.return_tracks):
            devices_list = []
            for d in track.devices:
                devices_list.append({"name": d.name, "class_name": d.class_name})
            returns.append({
                "index": i,
                "name": track.name,
                "volume": track.mixer_device.volume.value,
                "panning": track.mixer_device.panning.value,
                "color_index": track.color_index if hasattr(track, 'color_index') else 0,
                "devices": devices_list,
            })
        return {"return_tracks": returns, "count": len(returns)}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting return tracks info: " + str(e))
        raise
