"""Audio: load samples, warp, crop, reverse, analyze, freeze, export."""

from __future__ import absolute_import, print_function, unicode_literals

import traceback


def _get_audio_clip(song, track_index, clip_index):
    """Validate indices and return the audio clip, or raise."""
    if track_index < 0 or track_index >= len(song.tracks):
        raise IndexError("Track index out of range")
    track = song.tracks[track_index]
    if clip_index < 0 or clip_index >= len(track.clip_slots):
        raise IndexError("Clip index out of range")
    clip_slot = track.clip_slots[clip_index]
    if not clip_slot.has_clip:
        raise Exception("No clip in slot")
    clip = clip_slot.clip
    if not hasattr(clip, 'is_audio_clip') or not clip.is_audio_clip:
        raise Exception("Clip is not an audio clip")
    return clip


def get_audio_clip_info(song, track_index, clip_index, ctrl=None):
    """Get information about an audio clip."""
    try:
        clip = _get_audio_clip(song, track_index, clip_index)

        warp_mode_map = {
            0: "beats", 1: "tones", 2: "texture",
            3: "re_pitch", 4: "complex", 5: "complex_pro",
        }
        warp_mode = "unknown"
        if hasattr(clip, "warp_mode"):
            warp_mode = warp_mode_map.get(clip.warp_mode, "unknown")

        return {
            "name": clip.name,
            "length": clip.length,
            "is_audio_clip": clip.is_audio_clip,
            "warping": getattr(clip, "warping", None),
            "warp_mode": warp_mode,
            "start_marker": getattr(clip, "start_marker", None),
            "end_marker": getattr(clip, "end_marker", None),
            "loop_start": getattr(clip, "loop_start", None),
            "loop_end": getattr(clip, "loop_end", None),
            "gain": getattr(clip, "gain", None),
            "file_path": getattr(clip, "file_path", None),
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting audio clip info: " + str(e))
        raise


def set_warp_mode(song, track_index, clip_index, warp_mode, ctrl=None):
    """Set the warp mode for an audio clip."""
    try:
        clip = _get_audio_clip(song, track_index, clip_index)

        warp_mode_map = {
            "beats": 0, "tones": 1, "texture": 2,
            "re_pitch": 3, "complex": 4, "complex_pro": 5,
        }
        if warp_mode.lower() not in warp_mode_map:
            raise ValueError(
                "Invalid warp mode. Must be one of: beats, tones, texture, "
                "re_pitch, complex, complex_pro"
            )
        clip.warp_mode = warp_mode_map[warp_mode.lower()]
        return {"warp_mode": warp_mode.lower(), "warping": clip.warping}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error setting warp mode: " + str(e))
        raise


def set_clip_warp(song, track_index, clip_index, warping_enabled, ctrl=None):
    """Enable or disable warping for an audio clip."""
    try:
        clip = _get_audio_clip(song, track_index, clip_index)
        clip.warping = warping_enabled
        return {"warping": clip.warping}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error setting clip warp: " + str(e))
        raise


def reverse_clip(song, track_index, clip_index, ctrl=None):
    """Reverse an audio clip."""
    try:
        clip = _get_audio_clip(song, track_index, clip_index)

        if hasattr(clip, "sample"):
            sample = clip.sample
            if hasattr(sample, "reverse"):
                sample.reverse = not sample.reverse
                return {"reversed": sample.reverse}

        raise NotImplementedError(
            "Audio clip reversal is not available in this version of the API. "
            "You may need to use Ableton's built-in reverse function manually."
        )
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error reversing clip: " + str(e))
        raise


def analyze_audio_clip(song, track_index, clip_index, ctrl=None):
    """Analyze an audio clip comprehensively."""
    try:
        clip = _get_audio_clip(song, track_index, clip_index)

        warp_mode_map = {
            0: "beats", 1: "tones", 2: "texture",
            3: "re_pitch", 4: "complex", 5: "complex_pro",
        }

        analysis = {
            "basic_info": {
                "name": clip.name,
                "length_beats": clip.length,
                "loop_start": getattr(clip, "loop_start", None),
                "loop_end": getattr(clip, "loop_end", None),
                "file_path": getattr(clip, "file_path", None),
            },
            "tempo_rhythm": {
                "warping_enabled": getattr(clip, "warping", None),
                "warp_mode": (
                    warp_mode_map.get(clip.warp_mode, "unknown")
                    if hasattr(clip, "warp_mode") else None
                ),
            },
            "audio_properties": {},
            "frequency_analysis": {},
        }

        # Sample properties
        if hasattr(clip, "sample"):
            sample = clip.sample
            try:
                if hasattr(sample, "length"):
                    analysis["audio_properties"]["sample_length"] = sample.length
                    if hasattr(sample, "sample_rate") and sample.sample_rate > 0:
                        analysis["audio_properties"]["duration_seconds"] = sample.length / sample.sample_rate
                        analysis["audio_properties"]["sample_rate"] = sample.sample_rate
                if hasattr(sample, "bit_depth"):
                    analysis["audio_properties"]["bit_depth"] = sample.bit_depth
                if hasattr(sample, "channels"):
                    analysis["audio_properties"]["channels"] = sample.channels
                    analysis["audio_properties"]["is_stereo"] = sample.channels == 2
            except Exception:
                pass

        # Frequency hints from warp mode
        if hasattr(clip, "warp_mode"):
            character_map = {
                0: "percussive", 1: "tonal", 2: "textural", 4: "full_spectrum", 5: "full_spectrum",
            }
            analysis["frequency_analysis"]["character"] = character_map.get(clip.warp_mode, "unknown")

        # Summary
        parts = []
        if getattr(clip, "warping", False):
            parts.append("warped audio")
        else:
            parts.append("unwarped audio")
        if analysis["frequency_analysis"].get("character"):
            parts.append(analysis["frequency_analysis"]["character"] + " character")
        analysis["summary"] = ", ".join(parts).capitalize()

        return analysis
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error analyzing audio clip: " + str(e))
            ctrl.log_message(traceback.format_exc())
        raise


def freeze_track(song, track_index, ctrl=None):
    """Freeze a track."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        track = song.tracks[track_index]
        if not getattr(track, "can_be_frozen", False) or not track.can_be_frozen:
            raise Exception("Track cannot be frozen (may be a return or master track)")
        if not hasattr(track, "freeze"):
            raise Exception("Freeze not available on this track")
        track.freeze = True
        return {
            "track_index": track_index,
            "frozen": True,
            "track_name": track.name,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error freezing track: " + str(e))
        raise


def unfreeze_track(song, track_index, ctrl=None):
    """Unfreeze a track."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        track = song.tracks[track_index]
        if not hasattr(track, "freeze"):
            raise Exception("Freeze not available on this track")
        track.freeze = False
        return {
            "track_index": track_index,
            "frozen": False,
            "track_name": track.name,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error unfreezing track: " + str(e))
        raise
