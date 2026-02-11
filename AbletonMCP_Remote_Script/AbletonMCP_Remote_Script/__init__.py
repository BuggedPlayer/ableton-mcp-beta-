# AbletonMCP Beta / init.py
from __future__ import absolute_import, print_function, unicode_literals

from _Framework.ControlSurface import ControlSurface
import socket
import json
import threading
import time
import traceback

# Change queue import for Python 2
try:
    import Queue as queue  # Python 2
except ImportError:
    import queue  # Python 3

from . import handlers

# Constants for socket communication
DEFAULT_PORT = 9877
UDP_REALTIME_PORT = 9882
HOST = "localhost"

# -----------------------------------------------------------------------
# Command routing tables
# -----------------------------------------------------------------------
# Commands that modify Live's state must run on the main thread via
# schedule_message + queue.  Read-only commands run on the socket thread.
# -----------------------------------------------------------------------

MODIFYING_COMMANDS = {
    # session
    "set_tempo", "start_playback", "stop_playback",
    "set_song_time", "set_song_loop",
    "set_loop_start", "set_loop_end", "set_loop_length",
    "set_playback_position",
    "set_arrangement_overdub",
    "start_arrangement_recording", "stop_arrangement_recording",
    "set_metronome", "tap_tempo",
    "undo", "redo", "continue_playing", "re_enable_automation",
    "set_or_delete_cue", "jump_to_cue", "set_groove_settings",
    "set_song_settings", "trigger_session_record", "navigate_playback",
    "select_scene", "select_track", "set_detail_clip",
    "set_song_scale", "set_punch", "set_link_enabled",
    "set_view", "zoom_scroll_view",
    # tracks
    "create_midi_track", "create_audio_track", "create_return_track",
    "set_track_name", "delete_track", "duplicate_track",
    "set_track_color", "arm_track", "disarm_track", "group_tracks",
    "set_track_routing", "set_track_monitoring",
    "create_midi_track_with_simpler",
    "set_track_fold",
    "create_take_lane", "insert_device",
    # clips
    "create_clip", "add_notes_to_clip", "set_clip_name",
    "fire_clip", "stop_clip", "delete_clip",
    "duplicate_clip", "set_clip_looping", "set_clip_loop_points",
    "set_clip_color", "crop_clip", "duplicate_clip_loop", "set_clip_start_end",
    "set_clip_pitch", "set_clip_launch_mode",
    "set_clip_launch_quantization", "set_clip_legato", "audio_to_midi",
    "duplicate_clip_region", "move_clip_playing_pos", "set_clip_grid",
    # mixer
    "set_track_volume", "set_track_pan", "set_track_mute", "set_track_solo",
    "set_track_arm", "set_track_send",
    "set_return_track_volume", "set_return_track_pan",
    "set_return_track_mute", "set_return_track_solo",
    "set_master_volume",
    "set_crossfade_assign",
    # scenes
    "create_scene", "delete_scene", "duplicate_scene",
    "fire_scene", "set_scene_name", "set_scene_tempo",
    # devices
    "set_device_parameter", "set_device_parameters_batch", "delete_device",
    "set_macro_value", "set_drum_pad", "copy_drum_pad",
    "rack_variation_action", "sliced_simpler_to_drum_rack",
    "set_compressor_sidechain", "set_eq8_properties", "set_hybrid_reverb_ir",
    "set_transmute_properties",
    "set_simpler_properties", "simpler_sample_action", "manage_sample_slices",
    # browser
    "load_browser_item", "load_instrument_or_effect", "load_sample",
    "preview_browser_item",
    # midi
    "add_notes_extended", "remove_notes_range", "clear_clip_notes",
    "quantize_clip_notes", "transpose_clip_notes",
    "capture_midi", "apply_groove",
    # automation
    "create_clip_automation", "clear_clip_automation",
    "create_track_automation", "clear_track_automation",
    "delete_time", "duplicate_time", "insert_silence",
    # arrangement
    "duplicate_clip_to_arrangement",
    # audio
    "set_warp_mode", "set_clip_warp", "reverse_clip",
    "freeze_track", "unfreeze_track",
    # warp markers
    "add_warp_marker", "move_warp_marker", "remove_warp_marker",
    # looper
    "control_looper",
}

READ_ONLY_COMMANDS = {
    # session
    "get_session_info", "get_song_transport",
    "get_loop_info", "get_recording_status",
    "get_cue_points", "get_groove_pool",
    "get_song_settings",
    "get_song_scale", "get_selection_state",
    "get_link_status", "get_tuning_system",
    "get_view_state", "get_playing_clips",
    # tracks
    "get_track_info", "get_all_tracks_info", "get_return_tracks_info",
    "get_track_routing",
    "get_track_meters",
    "get_take_lanes",
    # clips
    "get_clip_info",
    # mixer
    "get_scenes", "get_return_tracks", "get_return_track_info",
    "get_master_track_info",
    # devices
    "get_device_parameters", "get_macro_values",
    "get_drum_pads", "get_rack_variations",
    "get_compressor_sidechain", "get_eq8_properties", "get_hybrid_reverb_ir",
    "get_transmute_properties",
    "get_simpler_properties",
    # browser
    "get_browser_item", "get_browser_tree", "get_browser_items_at_path",
    "search_browser", "get_user_library", "get_user_folders",
    # midi
    "get_clip_notes", "get_notes_extended",
    # automation
    "get_clip_automation", "list_clip_automated_params",
    # audio
    "get_audio_clip_info", "analyze_audio_clip",
    "get_warp_markers",
    # arrangement
    "get_arrangement_clips",
}


def create_instance(c_instance):
    """Create and return the AbletonMCP script instance"""
    return AbletonMCP(c_instance)


class AbletonMCP(ControlSurface):
    """AbletonMCP Beta Remote Script for Ableton Live"""

    def __init__(self, c_instance):
        """Initialize the control surface"""
        ControlSurface.__init__(self, c_instance)
        self.log_message("AbletonMCP Beta Remote Script initializing...")

        # Socket server for communication
        self.server = None
        self.client_threads = []
        self.client_sockets = []
        self.server_thread = None
        self.running = False

        # UDP real-time parameter server
        self.udp_sock = None
        self.udp_thread = None

        # Start the socket servers
        self.start_server()
        self.start_udp_server()

        self.log_message("AbletonMCP Beta initialized")

        # Show a message in Ableton
        self.show_message("AbletonMCP Beta: TCP " + str(DEFAULT_PORT) + " / UDP " + str(UDP_REALTIME_PORT))

    @property
    def _song(self):
        """Always return the current song, even after File > New"""
        return self.song()

    def disconnect(self):
        """Called when Ableton closes or the control surface is removed"""
        self.log_message("AbletonMCP Beta disconnecting...")
        self.running = False

        # Close all client sockets so their threads can exit
        for sock in self.client_sockets[:]:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except (OSError, socket.error):
                pass
            try:
                sock.close()
            except (OSError, socket.error):
                pass
        self.client_sockets = []

        # Stop the server
        if self.server:
            try:
                self.server.shutdown(socket.SHUT_RDWR)
            except (OSError, socket.error):
                pass
            try:
                self.server.close()
            except (OSError, socket.error):
                pass

        # Wait for the server thread to exit
        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(3.0)

        # Wait briefly for client threads to exit
        for client_thread in self.client_threads[:]:
            if client_thread.is_alive():
                client_thread.join(3.0)

        # Close UDP socket
        if self.udp_sock:
            try:
                self.udp_sock.close()
            except (OSError, socket.error):
                pass
            self.udp_sock = None

        if self.udp_thread and self.udp_thread.is_alive():
            self.udp_thread.join(3.0)

        ControlSurface.disconnect(self)
        self.log_message("AbletonMCP Beta disconnected")

    def start_server(self):
        """Start the socket server in a separate thread"""
        try:
            self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server.bind((HOST, DEFAULT_PORT))
            self.server.listen(5)

            self.running = True
            self.server_thread = threading.Thread(target=self._server_thread)
            self.server_thread.daemon = True
            self.server_thread.start()

            self.log_message("Server started on port " + str(DEFAULT_PORT))
        except Exception as e:
            self.log_message("Error starting server: " + str(e))
            self.show_message("AbletonMCP Beta: Error starting server - " + str(e))

    def start_udp_server(self):
        """Start the UDP real-time parameter server in a separate thread."""
        try:
            self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.udp_sock.bind((HOST, UDP_REALTIME_PORT))
            self.udp_sock.settimeout(1.0)

            self.udp_thread = threading.Thread(target=self._udp_server_loop)
            self.udp_thread.daemon = True
            self.udp_thread.start()

            self.log_message("UDP real-time server started on port " + str(UDP_REALTIME_PORT))
        except Exception as e:
            self.log_message("Error starting UDP server: " + str(e))

    def _udp_server_loop(self):
        """UDP server loop - receives fire-and-forget parameter updates."""
        while self.running:
            try:
                data, addr = self.udp_sock.recvfrom(4096)
                if not data:
                    continue

                try:
                    command = json.loads(data.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue

                self._process_udp_command(command)

            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    self.log_message("UDP server error: " + str(e))
                time.sleep(0.1)

    def _process_udp_command(self, command):
        """Process a UDP command. Fire-and-forget - no response sent."""
        cmd = command.get("type", "")
        params = command.get("params", {})
        song = self._song
        ctrl = self

        if cmd == "set_device_parameter":
            def task():
                try:
                    handlers.devices.set_device_parameter(
                        song,
                        params.get("track_index", 0),
                        params.get("device_index", 0),
                        params.get("parameter_name", ""),
                        params.get("value", 0.0),
                        params.get("track_type", "track"),
                        ctrl=ctrl,
                    )
                except Exception as e:
                    self.log_message("UDP set_device_parameter error: " + str(e))
            try:
                self.schedule_message(0, task)
            except AssertionError:
                task()

        elif cmd == "batch_set_device_parameters":
            def task():
                try:
                    handlers.devices.set_device_parameters_batch(
                        song,
                        params.get("track_index", 0),
                        params.get("device_index", 0),
                        params.get("parameters", []),
                        params.get("track_type", "track"),
                        ctrl=ctrl,
                    )
                except Exception as e:
                    self.log_message("UDP batch_set error: " + str(e))
            try:
                self.schedule_message(0, task)
            except AssertionError:
                task()

    def _server_thread(self):
        """Server thread implementation - handles client connections"""
        try:
            self.log_message("Server thread started")
            self.server.settimeout(1.0)

            while self.running:
                try:
                    client, address = self.server.accept()
                    self.log_message("Connection accepted from " + str(address))
                    self.show_message("AbletonMCP Beta: Client connected")

                    client_thread = threading.Thread(
                        target=self._handle_client,
                        args=(client,)
                    )
                    client_thread.daemon = True
                    client_thread.start()

                    self.client_threads.append(client_thread)
                    self.client_sockets.append(client)

                    # Clean up finished client threads
                    self.client_threads = [t for t in self.client_threads if t.is_alive()]

                except socket.timeout:
                    continue
                except Exception as e:
                    if self.running:
                        self.log_message("Server accept error: " + str(e))
                    time.sleep(0.5)

            self.log_message("Server thread stopped")
        except Exception as e:
            self.log_message("Server thread error: " + str(e))

    def _handle_client(self, client):
        """Handle communication with a connected client"""
        self.log_message("Client handler started")
        client.settimeout(5.0)
        buffer = ''

        try:
            while self.running:
                try:
                    try:
                        data = client.recv(8192)
                    except socket.timeout:
                        continue

                    if not data:
                        self.log_message("Client disconnected")
                        break

                    # Accumulate data (replace invalid UTF-8 instead of crashing)
                    buffer += data.decode('utf-8', errors='replace')

                    # Process all complete newline-delimited messages
                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        line = line.strip()
                        if not line:
                            continue

                        try:
                            command = json.loads(line)
                        except ValueError:
                            self.log_message("Invalid JSON received, skipping: " + line[:100])
                            continue

                        self.log_message("Received command: " + str(command.get("type", "unknown")))

                        response = self._process_command(command)

                        response_str = json.dumps(response) + '\n'
                        try:
                            client.sendall(response_str.encode('utf-8'))
                        except (OSError, socket.error):
                            self.log_message("Client disconnected during response send")
                            break

                    # 1MB safety limit
                    if len(buffer) > 1048576:
                        self.log_message("Buffer overflow (>1MB without newline), disconnecting client")
                        try:
                            err = json.dumps({"status": "error", "message": "Request too large (>1MB)"}) + '\n'
                            client.sendall(err.encode('utf-8'))
                        except Exception:
                            pass
                        break

                except Exception as e:
                    self.log_message("Error handling client data: " + str(e))
                    self.log_message(traceback.format_exc())

                    error_response = {"status": "error", "message": str(e)}
                    try:
                        client.sendall((json.dumps(error_response) + '\n').encode('utf-8'))
                    except Exception:
                        break

                    if not isinstance(e, ValueError):
                        break
        except Exception as e:
            self.log_message("Error in client handler: " + str(e))
        finally:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except (OSError, socket.error):
                pass
            try:
                client.close()
            except (OSError, socket.error):
                pass
            if client in self.client_sockets:
                self.client_sockets.remove(client)
            self.log_message("Client handler stopped")

    # ------------------------------------------------------------------
    # Command routing
    # ------------------------------------------------------------------

    def _process_command(self, command):
        """Process a command from the client and return a response."""
        command_type = command.get("type", "")
        params = command.get("params", {})
        response = {"status": "success", "result": {}}

        try:
            if command_type in MODIFYING_COMMANDS:
                response = self._dispatch_on_main_thread(command_type, params)
            elif command_type in READ_ONLY_COMMANDS:
                response = self._dispatch_on_main_thread_readonly(command_type, params)
            else:
                response["status"] = "error"
                response["message"] = "Unknown command: " + command_type
        except Exception as e:
            self.log_message("Error processing command: " + str(e))
            self.log_message(traceback.format_exc())
            response["status"] = "error"
            response["message"] = str(e)

        return response

    def _dispatch_on_main_thread(self, command_type, params):
        """Schedule a modifying command on Ableton's main thread and wait for the result."""
        response_queue = queue.Queue()

        def main_thread_task():
            try:
                result = self._dispatch_modifying(command_type, params)
                response_queue.put({"status": "success", "result": result})
            except Exception as e:
                self.log_message("Error in main thread task: " + str(e))
                self.log_message(traceback.format_exc())
                response_queue.put({"status": "error", "message": str(e)})

        try:
            self.schedule_message(0, main_thread_task)
        except AssertionError:
            main_thread_task()

        try:
            task_response = response_queue.get(timeout=10.0)
            return task_response
        except queue.Empty:
            return {"status": "error", "message": "Timeout waiting for operation to complete"}

    def _dispatch_on_main_thread_readonly(self, command_type, params):
        """Schedule a read-only command on Ableton's main thread and wait for the result."""
        response_queue = queue.Queue()

        def main_thread_task():
            try:
                result = self._dispatch_read_only(command_type, params)
                response_queue.put({"status": "success", "result": result})
            except Exception as e:
                self.log_message("Error in main thread read-only task: " + str(e))
                self.log_message(traceback.format_exc())
                response_queue.put({"status": "error", "message": str(e)})

        try:
            self.schedule_message(0, main_thread_task)
        except AssertionError:
            main_thread_task()

        try:
            task_response = response_queue.get(timeout=10.0)
            return task_response
        except queue.Empty:
            return {"status": "error", "message": "Timeout waiting for read-only operation to complete"}

    # ------------------------------------------------------------------
    # Modifying command dispatch
    # ------------------------------------------------------------------

    def _dispatch_modifying(self, cmd, p):
        """Route a modifying command to the appropriate handler function."""
        song = self._song
        ctrl = self

        # --- Session ---
        if cmd == "set_tempo":
            return handlers.session.set_tempo(song, p.get("tempo", 120.0), ctrl)
        elif cmd == "start_playback":
            return handlers.session.start_playback(song, ctrl)
        elif cmd == "stop_playback":
            return handlers.session.stop_playback(song, ctrl)
        elif cmd == "set_song_time":
            return handlers.session.set_song_time(song, p.get("time", 0.0), ctrl)
        elif cmd == "set_song_loop":
            return handlers.session.set_song_loop(song, p.get("enabled"), p.get("start"), p.get("length"), ctrl)
        elif cmd == "set_loop_start":
            return handlers.session.set_loop_start(song, p.get("position", 0.0), ctrl)
        elif cmd == "set_loop_end":
            return handlers.session.set_loop_end(song, p.get("position", 0.0), ctrl)
        elif cmd == "set_loop_length":
            return handlers.session.set_loop_length(song, p.get("length", 4.0), ctrl)
        elif cmd == "set_playback_position":
            return handlers.session.set_playback_position(song, p.get("position", 0.0), ctrl)
        elif cmd == "set_arrangement_overdub":
            return handlers.session.set_arrangement_overdub(song, p.get("enabled", False), ctrl)
        elif cmd == "start_arrangement_recording":
            return handlers.session.start_arrangement_recording(song, ctrl)
        elif cmd == "stop_arrangement_recording":
            return handlers.session.stop_arrangement_recording(song, ctrl)
        elif cmd == "set_metronome":
            return handlers.session.set_metronome(song, p.get("enabled", True), ctrl)
        elif cmd == "tap_tempo":
            return handlers.session.tap_tempo(song, ctrl)
        elif cmd == "undo":
            return handlers.session.undo(song, ctrl)
        elif cmd == "redo":
            return handlers.session.redo(song, ctrl)
        elif cmd == "continue_playing":
            return handlers.session.continue_playing(song, ctrl)
        elif cmd == "re_enable_automation":
            return handlers.session.re_enable_automation(song, ctrl)
        elif cmd == "set_or_delete_cue":
            return handlers.session.set_or_delete_cue(song, ctrl)
        elif cmd == "jump_to_cue":
            return handlers.session.jump_to_cue(song, p.get("direction", "next"), ctrl)
        elif cmd == "set_groove_settings":
            return handlers.session.set_groove_settings(
                song, p.get("groove_amount"), p.get("groove_index"),
                p.get("timing_amount"), p.get("quantization_amount"),
                p.get("random_amount"), p.get("velocity_amount"), ctrl)
        elif cmd == "set_song_settings":
            return handlers.session.set_song_settings(
                song, p.get("signature_numerator"), p.get("signature_denominator"),
                p.get("swing_amount"), p.get("clip_trigger_quantization"),
                p.get("midi_recording_quantization"), p.get("back_to_arranger"),
                p.get("follow_song"), p.get("draw_mode"),
                p.get("session_automation_record"), ctrl)
        elif cmd == "trigger_session_record":
            return handlers.session.trigger_session_record(song, p.get("record_length"), ctrl)
        elif cmd == "navigate_playback":
            return handlers.session.navigate_playback(song, p.get("action", "play_selection"), p.get("beats"), ctrl)
        elif cmd == "select_scene":
            return handlers.session.select_scene(song, p.get("scene_index", 0), ctrl)
        elif cmd == "select_track":
            return handlers.session.select_track(song, p.get("track_index", 0), p.get("track_type", "track"), ctrl)
        elif cmd == "set_detail_clip":
            return handlers.session.set_detail_clip(song, p.get("track_index", 0), p.get("clip_index", 0), ctrl)
        elif cmd == "set_song_scale":
            return handlers.session.set_song_scale(
                song, p.get("root_note"), p.get("scale_name"), p.get("scale_mode"), ctrl)
        elif cmd == "set_punch":
            return handlers.session.set_punch(
                song, p.get("punch_in"), p.get("punch_out"), p.get("count_in_duration"), ctrl)
        elif cmd == "set_link_enabled":
            return handlers.session.set_link_enabled(
                song, p.get("enabled"), p.get("start_stop_sync"), ctrl)
        elif cmd == "set_view":
            return handlers.session.set_view(
                song, p.get("action", "show"), p.get("view_name", ""), ctrl)
        elif cmd == "zoom_scroll_view":
            return handlers.session.zoom_scroll_view(
                song, p.get("action", "scroll"), p.get("direction", 0),
                p.get("view_name", ""), p.get("modifier_pressed", False), ctrl)

        # --- Tracks ---
        elif cmd == "create_midi_track":
            return handlers.tracks.create_midi_track(song, p.get("index", -1), ctrl)
        elif cmd == "create_audio_track":
            return handlers.tracks.create_audio_track(song, p.get("index", -1), ctrl)
        elif cmd == "create_return_track":
            return handlers.tracks.create_return_track(song, ctrl)
        elif cmd == "set_track_name":
            return handlers.tracks.set_track_name(song, p.get("track_index", 0), p.get("name", ""), ctrl)
        elif cmd == "delete_track":
            return handlers.tracks.delete_track(song, p.get("track_index", 0), ctrl)
        elif cmd == "duplicate_track":
            return handlers.tracks.duplicate_track(song, p.get("track_index", 0), ctrl)
        elif cmd == "set_track_color":
            return handlers.tracks.set_track_color(song, p.get("track_index", 0), p.get("color_index", 0), ctrl)
        elif cmd == "arm_track":
            return handlers.tracks.arm_track(song, p.get("track_index", 0), ctrl)
        elif cmd == "disarm_track":
            return handlers.tracks.disarm_track(song, p.get("track_index", 0), ctrl)
        elif cmd == "group_tracks":
            return handlers.tracks.group_tracks(song, p.get("track_indices", []), p.get("name", ""), ctrl)
        elif cmd == "set_track_routing":
            return handlers.tracks.set_track_routing(
                song, p.get("track_index", 0),
                p.get("input_type"), p.get("input_channel"),
                p.get("output_type"), p.get("output_channel"), ctrl)
        elif cmd == "set_track_monitoring":
            return handlers.tracks.set_track_monitoring(song, p.get("track_index", 0), p.get("state", 1), ctrl)
        elif cmd == "create_midi_track_with_simpler":
            return handlers.tracks.create_midi_track_with_simpler(song, p.get("track_index", 0), p.get("clip_index", 0), ctrl)
        elif cmd == "set_track_fold":
            return handlers.tracks.set_track_fold(song, p.get("track_index", 0), p.get("fold_state", True), ctrl)
        elif cmd == "create_take_lane":
            return handlers.tracks.create_take_lane(song, p.get("track_index", 0), ctrl)
        elif cmd == "insert_device":
            return handlers.tracks.insert_device(
                song, p.get("track_index", 0), p.get("device_name", ""),
                p.get("target_index"), ctrl)

        # --- Clips ---
        elif cmd == "create_clip":
            return handlers.clips.create_clip(song, p.get("track_index", 0), p.get("clip_index", 0), p.get("length", 4.0), ctrl)
        elif cmd == "add_notes_to_clip":
            return handlers.clips.add_notes_to_clip(song, p.get("track_index", 0), p.get("clip_index", 0), p.get("notes", []), ctrl)
        elif cmd == "set_clip_name":
            return handlers.clips.set_clip_name(song, p.get("track_index", 0), p.get("clip_index", 0), p.get("name", ""), ctrl)
        elif cmd == "fire_clip":
            return handlers.clips.fire_clip(song, p.get("track_index", 0), p.get("clip_index", 0), ctrl)
        elif cmd == "stop_clip":
            return handlers.clips.stop_clip(song, p.get("track_index", 0), p.get("clip_index", 0), ctrl)
        elif cmd == "delete_clip":
            return handlers.clips.delete_clip(song, p.get("track_index", 0), p.get("clip_index", 0), ctrl)
        elif cmd == "duplicate_clip":
            return handlers.clips.duplicate_clip(song, p.get("track_index", 0), p.get("clip_index", 0), p.get("target_clip_index", 0), ctrl)
        elif cmd == "set_clip_looping":
            return handlers.clips.set_clip_looping(song, p.get("track_index", 0), p.get("clip_index", 0), p.get("looping", True), ctrl)
        elif cmd == "set_clip_loop_points":
            return handlers.clips.set_clip_loop_points(song, p.get("track_index", 0), p.get("clip_index", 0), p.get("loop_start", 0.0), p.get("loop_end", 4.0), ctrl)
        elif cmd == "set_clip_color":
            return handlers.clips.set_clip_color(song, p.get("track_index", 0), p.get("clip_index", 0), p.get("color_index", 0), ctrl)
        elif cmd == "crop_clip":
            return handlers.clips.crop_clip(song, p.get("track_index", 0), p.get("clip_index", 0), ctrl)
        elif cmd == "duplicate_clip_loop":
            return handlers.clips.duplicate_clip_loop(song, p.get("track_index", 0), p.get("clip_index", 0), ctrl)
        elif cmd == "set_clip_start_end":
            return handlers.clips.set_clip_start_end(song, p.get("track_index", 0), p.get("clip_index", 0), p.get("start_marker"), p.get("end_marker"), ctrl)
        elif cmd == "set_clip_pitch":
            return handlers.clips.set_clip_pitch(song, p.get("track_index", 0), p.get("clip_index", 0), p.get("pitch_coarse"), p.get("pitch_fine"), ctrl)
        elif cmd == "set_clip_launch_mode":
            return handlers.clips.set_clip_launch_mode(song, p.get("track_index", 0), p.get("clip_index", 0), p.get("launch_mode", 0), ctrl)
        elif cmd == "set_clip_launch_quantization":
            return handlers.clips.set_clip_launch_quantization(song, p.get("track_index", 0), p.get("clip_index", 0), p.get("quantization", 14), ctrl)
        elif cmd == "set_clip_legato":
            return handlers.clips.set_clip_legato(song, p.get("track_index", 0), p.get("clip_index", 0), p.get("legato", False), ctrl)
        elif cmd == "audio_to_midi":
            return handlers.clips.audio_to_midi(song, p.get("track_index", 0), p.get("clip_index", 0), p.get("conversion_type", "melody"), ctrl)
        elif cmd == "duplicate_clip_region":
            return handlers.clips.duplicate_clip_region(
                song, p.get("track_index", 0), p.get("clip_index", 0),
                p.get("region_start", 0.0), p.get("region_length", 4.0),
                p.get("destination_time", 0.0), p.get("pitch", -1),
                p.get("transposition_amount", 0), ctrl)
        elif cmd == "move_clip_playing_pos":
            return handlers.clips.move_clip_playing_pos(
                song, p.get("track_index", 0), p.get("clip_index", 0),
                p.get("time", 0.0), ctrl)
        elif cmd == "set_clip_grid":
            return handlers.clips.set_clip_grid(
                song, p.get("track_index", 0), p.get("clip_index", 0),
                p.get("grid_quantization"), p.get("grid_is_triplet"), ctrl)

        # --- Mixer ---
        elif cmd == "set_track_volume":
            return handlers.mixer.set_track_volume(song, p.get("track_index", 0), p.get("volume", 0.85), ctrl)
        elif cmd == "set_track_pan":
            return handlers.mixer.set_track_pan(song, p.get("track_index", 0), p.get("pan", 0.0), ctrl)
        elif cmd == "set_track_mute":
            return handlers.mixer.set_track_mute(song, p.get("track_index", 0), p.get("mute", False), ctrl)
        elif cmd == "set_track_solo":
            return handlers.mixer.set_track_solo(song, p.get("track_index", 0), p.get("solo", False), ctrl)
        elif cmd == "set_track_arm":
            return handlers.mixer.set_track_arm(song, p.get("track_index", 0), p.get("arm", False), ctrl)
        elif cmd == "set_track_send":
            return handlers.mixer.set_track_send(song, p.get("track_index", 0), p.get("send_index", 0), p.get("value", 0.0), ctrl)
        elif cmd == "set_return_track_volume":
            return handlers.mixer.set_return_track_volume(song, p.get("return_track_index", 0), p.get("volume", 0.85), ctrl)
        elif cmd == "set_return_track_pan":
            return handlers.mixer.set_return_track_pan(song, p.get("return_track_index", 0), p.get("pan", 0.0), ctrl)
        elif cmd == "set_return_track_mute":
            return handlers.mixer.set_return_track_mute(song, p.get("return_track_index", 0), p.get("mute", False), ctrl)
        elif cmd == "set_return_track_solo":
            return handlers.mixer.set_return_track_solo(song, p.get("return_track_index", 0), p.get("solo", False), ctrl)
        elif cmd == "set_master_volume":
            return handlers.mixer.set_master_volume(song, p.get("volume", 0.85), ctrl)
        elif cmd == "set_crossfade_assign":
            return handlers.mixer.set_crossfade_assign(song, p.get("track_index", 0), p.get("assign", 0), ctrl)

        # --- Scenes ---
        elif cmd == "create_scene":
            return handlers.scenes.create_scene(song, p.get("index", -1), p.get("name", ""), ctrl)
        elif cmd == "delete_scene":
            return handlers.scenes.delete_scene(song, p.get("scene_index", 0), ctrl)
        elif cmd == "duplicate_scene":
            return handlers.scenes.duplicate_scene(song, p.get("scene_index", 0), ctrl)
        elif cmd == "fire_scene":
            return handlers.scenes.fire_scene(song, p.get("scene_index", 0), ctrl)
        elif cmd == "set_scene_name":
            return handlers.scenes.set_scene_name(song, p.get("scene_index", 0), p.get("name", ""), ctrl)
        elif cmd == "set_scene_tempo":
            return handlers.scenes.set_scene_tempo(song, p.get("scene_index", 0), p.get("tempo", 0), ctrl)

        # --- Devices ---
        elif cmd == "set_device_parameter":
            return handlers.devices.set_device_parameter(
                song, p.get("track_index", 0), p.get("device_index", 0),
                p.get("parameter_name", ""), p.get("value", 0.0),
                p.get("track_type", "track"), p.get("value_display"), ctrl)
        elif cmd == "set_device_parameters_batch":
            return handlers.devices.set_device_parameters_batch(
                song, p.get("track_index", 0), p.get("device_index", 0),
                p.get("parameters", []), p.get("track_type", "track"), ctrl)
        elif cmd == "delete_device":
            return handlers.devices.delete_device(song, p.get("track_index", 0), p.get("device_index", 0), ctrl)
        elif cmd == "set_macro_value":
            return handlers.devices.set_macro_value(
                song, p.get("track_index", 0), p.get("device_index", 0),
                p.get("macro_index", 0), p.get("value", 0.0), ctrl)
        elif cmd == "set_drum_pad":
            return handlers.devices.set_drum_pad(
                song, p.get("track_index", 0), p.get("device_index", 0),
                p.get("note", 36), p.get("mute"), p.get("solo"), ctrl)
        elif cmd == "copy_drum_pad":
            return handlers.devices.copy_drum_pad(
                song, p.get("track_index", 0), p.get("device_index", 0),
                p.get("source_note", 36), p.get("dest_note", 37), ctrl)
        elif cmd == "rack_variation_action":
            return handlers.devices.rack_variation_action(
                song, p.get("track_index", 0), p.get("device_index", 0),
                p.get("action", "recall"), p.get("variation_index"), ctrl)
        elif cmd == "sliced_simpler_to_drum_rack":
            return handlers.devices.sliced_simpler_to_drum_rack(
                song, p.get("track_index", 0), p.get("device_index", 0), ctrl)
        elif cmd == "set_compressor_sidechain":
            return handlers.devices.set_compressor_sidechain(
                song, p.get("track_index", 0), p.get("device_index", 0),
                p.get("input_type"), p.get("input_channel"), ctrl)
        elif cmd == "set_eq8_properties":
            return handlers.devices.set_eq8_properties(
                song, p.get("track_index", 0), p.get("device_index", 0),
                p.get("edit_mode"), p.get("global_mode"),
                p.get("oversample"), p.get("selected_band"), ctrl)
        elif cmd == "set_hybrid_reverb_ir":
            return handlers.devices.set_hybrid_reverb_ir(
                song, p.get("track_index", 0), p.get("device_index", 0),
                p.get("ir_category_index"), p.get("ir_file_index"),
                p.get("ir_attack_time"), p.get("ir_decay_time"),
                p.get("ir_size_factor"), p.get("ir_time_shaping_on"), ctrl)
        elif cmd == "set_transmute_properties":
            return handlers.devices.set_transmute_properties(
                song, p.get("track_index", 0), p.get("device_index", 0),
                p.get("frequency_dial_mode_index"), p.get("pitch_mode_index"),
                p.get("mod_mode_index"), p.get("mono_poly_index"),
                p.get("midi_gate_index"), p.get("polyphony"),
                p.get("pitch_bend_range"), ctrl)
        elif cmd == "set_simpler_properties":
            return handlers.devices.set_simpler_properties(
                song, p.get("track_index", 0), p.get("device_index", 0),
                p.get("playback_mode"), p.get("voices"), p.get("retrigger"),
                p.get("slicing_playback_mode"),
                p.get("start_marker"), p.get("end_marker"), p.get("gain"),
                p.get("warp_mode"), p.get("warping"),
                p.get("slicing_style"), p.get("slicing_sensitivity"),
                p.get("slicing_beat_division"),
                p.get("beats_granulation_resolution"),
                p.get("beats_transient_envelope"),
                p.get("beats_transient_loop_mode"),
                p.get("complex_pro_formants"), p.get("complex_pro_envelope"),
                p.get("texture_grain_size"), p.get("texture_flux"),
                p.get("tones_grain_size"), ctrl)
        elif cmd == "simpler_sample_action":
            return handlers.devices.simpler_sample_action(
                song, p.get("track_index", 0), p.get("device_index", 0),
                p.get("action", "reverse"), p.get("beats"), ctrl)
        elif cmd == "manage_sample_slices":
            return handlers.devices.manage_sample_slices(
                song, p.get("track_index", 0), p.get("device_index", 0),
                p.get("action", "insert"), p.get("slice_time"), p.get("new_time"), ctrl)

        # --- Browser ---
        elif cmd == "load_browser_item":
            return handlers.browser.load_browser_item(song, p.get("track_index", 0), p.get("item_uri", ""), ctrl)
        elif cmd == "load_instrument_or_effect":
            return handlers.browser.load_instrument_or_effect(song, p.get("track_index", 0), p.get("uri", ""), ctrl)
        elif cmd == "load_sample":
            return handlers.browser.load_sample(song, p.get("track_index", 0), p.get("sample_uri", ""), ctrl)
        elif cmd == "preview_browser_item":
            return handlers.browser.preview_browser_item(song, p.get("uri"), p.get("action", "preview"), ctrl)

        # --- MIDI ---
        elif cmd == "add_notes_extended":
            return handlers.midi.add_notes_extended(song, p.get("track_index", 0), p.get("clip_index", 0), p.get("notes", []), ctrl)
        elif cmd == "remove_notes_range":
            return handlers.midi.remove_notes_range(
                song, p.get("track_index", 0), p.get("clip_index", 0),
                p.get("from_time", 0.0), p.get("time_span", 0.0),
                p.get("from_pitch", 0), p.get("pitch_span", 128), ctrl)
        elif cmd == "clear_clip_notes":
            return handlers.midi.clear_clip_notes(song, p.get("track_index", 0), p.get("clip_index", 0), ctrl)
        elif cmd == "quantize_clip_notes":
            return handlers.midi.quantize_clip_notes(song, p.get("track_index", 0), p.get("clip_index", 0), p.get("grid_size", 0.25), ctrl)
        elif cmd == "transpose_clip_notes":
            return handlers.midi.transpose_clip_notes(song, p.get("track_index", 0), p.get("clip_index", 0), p.get("semitones", 0), ctrl)
        elif cmd == "capture_midi":
            return handlers.midi.capture_midi(song, ctrl)
        elif cmd == "apply_groove":
            return handlers.midi.apply_groove(song, p.get("track_index", 0), p.get("clip_index", 0), p.get("groove_amount", 0.0), ctrl)

        # --- Automation ---
        elif cmd == "create_clip_automation":
            return handlers.automation.create_clip_automation(
                song, p.get("track_index", 0), p.get("clip_index", 0),
                p.get("parameter_name", ""), p.get("automation_points", []), ctrl)
        elif cmd == "clear_clip_automation":
            return handlers.automation.clear_clip_automation(
                song, p.get("track_index", 0), p.get("clip_index", 0),
                p.get("parameter_name", ""), ctrl)
        elif cmd == "create_track_automation":
            return handlers.automation.create_track_automation(
                song, p.get("track_index", 0), p.get("parameter_name", ""),
                p.get("automation_points", []), ctrl)
        elif cmd == "clear_track_automation":
            return handlers.automation.clear_track_automation(
                song, p.get("track_index", 0), p.get("parameter_name", ""),
                p.get("start_time", 0.0), p.get("end_time", 0.0), ctrl)
        elif cmd == "delete_time":
            return handlers.automation.delete_time(song, p.get("start_time", 0.0), p.get("end_time", 0.0), ctrl)
        elif cmd == "duplicate_time":
            return handlers.automation.duplicate_time(song, p.get("start_time", 0.0), p.get("end_time", 0.0), ctrl)
        elif cmd == "insert_silence":
            return handlers.automation.insert_silence(song, p.get("position", 0.0), p.get("length", 0.0), ctrl)

        # --- Arrangement ---
        elif cmd == "duplicate_clip_to_arrangement":
            return handlers.arrangement.duplicate_clip_to_arrangement(
                song, p.get("track_index", 0), p.get("clip_index", 0), p.get("time", 0.0), ctrl)

        # --- Audio ---
        elif cmd == "set_warp_mode":
            return handlers.audio.set_warp_mode(song, p.get("track_index", 0), p.get("clip_index", 0), p.get("warp_mode", "beats"), ctrl)
        elif cmd == "set_clip_warp":
            return handlers.audio.set_clip_warp(song, p.get("track_index", 0), p.get("clip_index", 0), p.get("warping_enabled", True), ctrl)
        elif cmd == "reverse_clip":
            return handlers.audio.reverse_clip(song, p.get("track_index", 0), p.get("clip_index", 0), ctrl)
        elif cmd == "freeze_track":
            return handlers.audio.freeze_track(song, p.get("track_index", 0), ctrl)
        elif cmd == "unfreeze_track":
            return handlers.audio.unfreeze_track(song, p.get("track_index", 0), ctrl)

        # --- Warp markers ---
        elif cmd == "add_warp_marker":
            return handlers.clips.add_warp_marker(
                song, p.get("track_index", 0), p.get("clip_index", 0),
                p.get("beat_time", 0.0), p.get("sample_time"), ctrl)
        elif cmd == "move_warp_marker":
            return handlers.clips.move_warp_marker(
                song, p.get("track_index", 0), p.get("clip_index", 0),
                p.get("marker_index", 0), p.get("beat_time", 0.0),
                p.get("sample_time"), ctrl)
        elif cmd == "remove_warp_marker":
            return handlers.clips.remove_warp_marker(
                song, p.get("track_index", 0), p.get("clip_index", 0),
                p.get("marker_index", 0), ctrl)

        # --- Looper ---
        elif cmd == "control_looper":
            return handlers.devices.control_looper(
                song, p.get("track_index", 0), p.get("device_index", 0),
                p.get("action", "play"), p.get("clip_slot_index"), ctrl)

        else:
            raise Exception("Unhandled modifying command: " + cmd)

    # ------------------------------------------------------------------
    # Read-only command dispatch
    # ------------------------------------------------------------------

    def _dispatch_read_only(self, cmd, p):
        """Route a read-only command to the appropriate handler function."""
        song = self._song
        ctrl = self

        # --- Session ---
        if cmd == "get_session_info":
            return handlers.session.get_session_info(song, ctrl)
        elif cmd == "get_song_transport":
            return handlers.session.get_song_transport(song, ctrl)
        elif cmd == "get_loop_info":
            return handlers.session.get_loop_info(song, ctrl)
        elif cmd == "get_recording_status":
            return handlers.session.get_recording_status(song, ctrl)
        elif cmd == "get_cue_points":
            return handlers.session.get_cue_points(song, ctrl)
        elif cmd == "get_groove_pool":
            return handlers.session.get_groove_pool(song, ctrl)
        elif cmd == "get_song_settings":
            return handlers.session.get_song_settings(song, ctrl)
        elif cmd == "get_song_scale":
            return handlers.session.get_song_scale(song, ctrl)
        elif cmd == "get_selection_state":
            return handlers.session.get_selection_state(song, ctrl)
        elif cmd == "get_link_status":
            return handlers.session.get_link_status(song, ctrl)
        elif cmd == "get_tuning_system":
            return handlers.session.get_tuning_system(song, ctrl)
        elif cmd == "get_view_state":
            return handlers.session.get_view_state(song, ctrl)
        elif cmd == "get_playing_clips":
            return handlers.session.get_playing_clips(song, ctrl)

        # --- Tracks ---
        elif cmd == "get_track_info":
            return handlers.tracks.get_track_info(song, p.get("track_index", 0), ctrl)
        elif cmd == "get_all_tracks_info":
            return handlers.tracks.get_all_tracks_info(song, ctrl)
        elif cmd == "get_return_tracks_info":
            return handlers.tracks.get_return_tracks_info(song, ctrl)
        elif cmd == "get_track_routing":
            return handlers.tracks.get_track_routing(song, p.get("track_index", 0), ctrl)
        elif cmd == "get_track_meters":
            return handlers.tracks.get_track_meters(song, p.get("track_index"), ctrl)
        elif cmd == "get_take_lanes":
            return handlers.tracks.get_take_lanes(song, p.get("track_index", 0), ctrl)

        # --- Clips ---
        elif cmd == "get_clip_info":
            return handlers.clips.get_clip_info(song, p.get("track_index", 0), p.get("clip_index", 0), ctrl)

        # --- Mixer ---
        elif cmd == "get_scenes":
            return handlers.mixer.get_scenes(song, ctrl)
        elif cmd == "get_return_tracks":
            return handlers.mixer.get_return_tracks(song, ctrl)
        elif cmd == "get_return_track_info":
            return handlers.mixer.get_return_track_info(song, p.get("return_track_index", 0), ctrl)
        elif cmd == "get_master_track_info":
            return handlers.mixer.get_master_track_info(song, ctrl)

        # --- Devices ---
        elif cmd == "get_device_parameters":
            return handlers.devices.get_device_parameters(
                song, p.get("track_index", 0), p.get("device_index", 0),
                p.get("track_type", "track"), ctrl)
        elif cmd == "get_macro_values":
            return handlers.devices.get_macro_values(song, p.get("track_index", 0), p.get("device_index", 0), ctrl)
        elif cmd == "get_drum_pads":
            return handlers.devices.get_drum_pads(song, p.get("track_index", 0), p.get("device_index", 0), ctrl)
        elif cmd == "get_rack_variations":
            return handlers.devices.get_rack_variations(song, p.get("track_index", 0), p.get("device_index", 0), ctrl)
        elif cmd == "get_compressor_sidechain":
            return handlers.devices.get_compressor_sidechain(
                song, p.get("track_index", 0), p.get("device_index", 0), ctrl)
        elif cmd == "get_eq8_properties":
            return handlers.devices.get_eq8_properties(
                song, p.get("track_index", 0), p.get("device_index", 0), ctrl)
        elif cmd == "get_hybrid_reverb_ir":
            return handlers.devices.get_hybrid_reverb_ir(
                song, p.get("track_index", 0), p.get("device_index", 0), ctrl)
        elif cmd == "get_transmute_properties":
            return handlers.devices.get_transmute_properties(
                song, p.get("track_index", 0), p.get("device_index", 0), ctrl)
        elif cmd == "get_simpler_properties":
            return handlers.devices.get_simpler_properties(
                song, p.get("track_index", 0), p.get("device_index", 0), ctrl)

        # --- Browser ---
        elif cmd == "get_browser_item":
            return handlers.browser.get_browser_item(song, p.get("uri"), p.get("path"), ctrl)
        elif cmd == "get_browser_tree":
            return handlers.browser.get_browser_tree(song, p.get("category_type", "all"), ctrl)
        elif cmd == "get_browser_items_at_path":
            return handlers.browser.get_browser_items_at_path(song, p.get("path", ""), ctrl)
        elif cmd == "search_browser":
            return handlers.browser.search_browser(song, p.get("query", ""), p.get("category", "all"), ctrl)
        elif cmd == "get_user_library":
            return handlers.browser.get_user_library(song, ctrl)
        elif cmd == "get_user_folders":
            return handlers.browser.get_user_folders(song, ctrl)

        # --- MIDI ---
        elif cmd == "get_clip_notes":
            return handlers.midi.get_clip_notes(
                song, p.get("track_index", 0), p.get("clip_index", 0),
                p.get("start_time", 0.0), p.get("time_span", 0.0),
                p.get("start_pitch", 0), p.get("pitch_span", 128), ctrl)
        elif cmd == "get_notes_extended":
            return handlers.midi.get_notes_extended(
                song, p.get("track_index", 0), p.get("clip_index", 0),
                p.get("start_time", 0.0), p.get("time_span", 0.0), ctrl)

        # --- Automation ---
        elif cmd == "get_clip_automation":
            return handlers.automation.get_clip_automation(
                song, p.get("track_index", 0), p.get("clip_index", 0),
                p.get("parameter_name", ""), ctrl)
        elif cmd == "list_clip_automated_params":
            return handlers.automation.list_clip_automated_params(
                song, p.get("track_index", 0), p.get("clip_index", 0), ctrl)

        # --- Audio ---
        elif cmd == "get_audio_clip_info":
            return handlers.audio.get_audio_clip_info(song, p.get("track_index", 0), p.get("clip_index", 0), ctrl)
        elif cmd == "analyze_audio_clip":
            return handlers.audio.analyze_audio_clip(song, p.get("track_index", 0), p.get("clip_index", 0), ctrl)
        elif cmd == "get_warp_markers":
            return handlers.clips.get_warp_markers(song, p.get("track_index", 0), p.get("clip_index", 0), ctrl)

        # --- Arrangement ---
        elif cmd == "get_arrangement_clips":
            return handlers.arrangement.get_arrangement_clips(song, p.get("track_index", 0), ctrl)

        else:
            raise Exception("Unhandled read-only command: " + cmd)
