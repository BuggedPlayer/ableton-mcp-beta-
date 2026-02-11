"""Arrangement: copy clip to arrangement, get arrangement clips."""

from __future__ import absolute_import, print_function, unicode_literals


def duplicate_clip_to_arrangement(song, track_index, clip_index, time, ctrl=None):
    """Copy a session clip to the arrangement timeline."""
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

        if not hasattr(track, 'duplicate_clip_to_arrangement'):
            raise Exception("duplicate_clip_to_arrangement requires Live 11 or later")

        track.duplicate_clip_to_arrangement(clip, float(time))

        return {
            "placed_at": float(time),
            "clip_name": clip.name,
            "clip_length": clip.length,
            "track_index": track_index,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error duplicating clip to arrangement: " + str(e))
        raise


def get_arrangement_clips(song, track_index, ctrl=None):
    """Get all clips in arrangement view for a track."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        track = song.tracks[track_index]

        if not hasattr(track, "arrangement_clips"):
            raise Exception(
                "Track does not have arrangement clips "
                "(may be a group track or return track)"
            )

        clips = []
        for clip in track.arrangement_clips:
            clip_info = {
                "name": clip.name,
                "start_time": clip.start_time,
                "end_time": clip.end_time,
                "length": clip.length,
                "loop_start": getattr(clip, "loop_start", None),
                "loop_end": getattr(clip, "loop_end", None),
                "is_audio_clip": clip.is_audio_clip if hasattr(clip, 'is_audio_clip') else False,
                "is_midi_clip": clip.is_midi_clip if hasattr(clip, 'is_midi_clip') else False,
                "muted": getattr(clip, "muted", False),
                "color_index": getattr(clip, "color_index", None),
            }
            clips.append(clip_info)

        return {
            "track_index": track_index,
            "track_name": track.name,
            "clip_count": len(clips),
            "clips": clips,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting arrangement clips: " + str(e))
        raise
