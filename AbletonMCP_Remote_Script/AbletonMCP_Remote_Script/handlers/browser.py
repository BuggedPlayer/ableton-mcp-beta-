"""Browser: tree, items at path, find by URI, load instrument/effect, search."""

from __future__ import absolute_import, print_function, unicode_literals

import traceback


def find_browser_item_by_uri(browser_or_item, uri, max_depth=10, current_depth=0, ctrl=None):
    """Find a browser item by its URI (recursive search)."""
    try:
        if hasattr(browser_or_item, "uri") and browser_or_item.uri == uri:
            return browser_or_item
        if current_depth >= max_depth:
            return None
        if hasattr(browser_or_item, "instruments"):
            categories = [
                browser_or_item.instruments,
                browser_or_item.sounds,
                browser_or_item.drums,
                browser_or_item.audio_effects,
                browser_or_item.midi_effects,
            ]
            for category in categories:
                item = find_browser_item_by_uri(category, uri, max_depth, current_depth + 1, ctrl)
                if item:
                    return item
            return None
        if hasattr(browser_or_item, "children") and browser_or_item.children:
            for child in browser_or_item.children:
                item = find_browser_item_by_uri(child, uri, max_depth, current_depth + 1, ctrl)
                if item:
                    return item
        return None
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error finding browser item by URI: {0}".format(str(e)))
        return None


def get_browser_item(song, uri, path, ctrl=None):
    """Get a browser item by URI or path."""
    try:
        if ctrl is None:
            raise RuntimeError("get_browser_item requires ctrl for application()")
        app = ctrl.application()
        if not app:
            raise RuntimeError("Could not access Live application")

        result = {"uri": uri, "path": path, "found": False}

        if uri:
            item = find_browser_item_by_uri(app.browser, uri, ctrl=ctrl)
            if item:
                result["found"] = True
                result["item"] = {
                    "name": item.name,
                    "is_folder": item.is_folder if hasattr(item, 'is_folder') else False,
                    "is_device": item.is_device if hasattr(item, 'is_device') else False,
                    "is_loadable": item.is_loadable if hasattr(item, 'is_loadable') else False,
                    "uri": item.uri,
                }
                return result

        if path:
            path_parts = path.split("/")
            root = path_parts[0].lower()
            current_item = None
            if root == "instruments" and hasattr(app.browser, 'instruments'):
                current_item = app.browser.instruments
            elif root == "sounds" and hasattr(app.browser, 'sounds'):
                current_item = app.browser.sounds
            elif root == "drums" and hasattr(app.browser, 'drums'):
                current_item = app.browser.drums
            elif root == "audio_effects" and hasattr(app.browser, 'audio_effects'):
                current_item = app.browser.audio_effects
            elif root == "midi_effects" and hasattr(app.browser, 'midi_effects'):
                current_item = app.browser.midi_effects
            else:
                current_item = app.browser.instruments
                path_parts = ["instruments"] + path_parts

            for i in range(1, len(path_parts)):
                part = path_parts[i]
                if not part:
                    continue
                found = False
                for child in current_item.children:
                    if child.name.lower() == part.lower():
                        current_item = child
                        found = True
                        break
                if not found:
                    result["error"] = "Path part '{0}' not found".format(part)
                    return result

            result["found"] = True
            result["item"] = {
                "name": current_item.name,
                "is_folder": current_item.is_folder if hasattr(current_item, 'is_folder') else False,
                "is_device": current_item.is_device if hasattr(current_item, 'is_device') else False,
                "is_loadable": current_item.is_loadable if hasattr(current_item, 'is_loadable') else False,
                "uri": current_item.uri if hasattr(current_item, 'uri') else None,
            }

        return result
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting browser item: " + str(e))
            ctrl.log_message(traceback.format_exc())
        raise


def load_browser_item(song, track_index, item_uri, ctrl=None):
    """Load a browser item onto a track by URI."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        track = song.tracks[track_index]
        if ctrl is None:
            raise RuntimeError("load_browser_item requires ctrl for application()")
        app = ctrl.application()

        item = find_browser_item_by_uri(app.browser, item_uri, ctrl=ctrl)
        if not item:
            raise ValueError("Browser item with URI '{0}' not found".format(item_uri))

        song.view.selected_track = track
        app.browser.load_item(item)

        return {
            "loaded": True,
            "item_name": item.name,
            "track_name": track.name,
            "uri": item_uri,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error loading browser item: {0}".format(str(e)))
            ctrl.log_message(traceback.format_exc())
        raise


def load_instrument_or_effect(song, track_index, uri, ctrl=None):
    """Load an instrument or effect onto a track by URI (alias)."""
    return load_browser_item(song, track_index, uri, ctrl)


def load_sample(song, track_index, sample_uri, ctrl=None):
    """Load a sample onto a track using its browser URI."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        track = song.tracks[track_index]
        if ctrl is None:
            raise RuntimeError("load_sample requires ctrl for application()")
        app = ctrl.application()

        item = find_browser_item_by_uri(app.browser, sample_uri, ctrl=ctrl)
        if not item:
            raise ValueError("Sample with URI '{0}' not found".format(sample_uri))

        song.view.selected_track = track
        app.browser.load_item(item)

        return {
            "loaded": True,
            "item_name": item.name,
            "track_name": track.name,
            "uri": sample_uri,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error loading sample: {0}".format(str(e)))
            ctrl.log_message(traceback.format_exc())
        raise


def _process_item(item):
    """Build a dict for a browser item (no children recursion)."""
    if not item:
        return None
    return {
        "name": item.name if hasattr(item, "name") else "Unknown",
        "is_folder": hasattr(item, "children") and bool(item.children),
        "is_device": hasattr(item, "is_device") and item.is_device,
        "is_loadable": hasattr(item, "is_loadable") and item.is_loadable,
        "uri": item.uri if hasattr(item, "uri") else None,
        "children": [],
    }


def get_browser_tree(song, category_type, ctrl=None):
    """Get a simplified tree of browser categories."""
    try:
        if ctrl is None:
            raise RuntimeError("get_browser_tree requires ctrl for application()")
        app = ctrl.application()
        if not app:
            raise RuntimeError("Could not access Live application")
        if not hasattr(app, "browser") or app.browser is None:
            raise RuntimeError("Browser is not available in the Live application")

        browser_attrs = [attr for attr in dir(app.browser) if not attr.startswith("_")]
        if ctrl:
            ctrl.log_message("Available browser attributes: {0}".format(browser_attrs))

        result = {
            "type": category_type,
            "categories": [],
            "available_categories": browser_attrs,
        }

        _categories = [
            ("instruments", "Instruments"),
            ("sounds", "Sounds"),
            ("drums", "Drums"),
            ("audio_effects", "Audio Effects"),
            ("midi_effects", "MIDI Effects"),
        ]
        for attr_name, display_name in _categories:
            if (category_type == "all" or category_type == attr_name) and hasattr(app.browser, attr_name):
                try:
                    item = _process_item(getattr(app.browser, attr_name))
                    if item:
                        item["name"] = display_name
                        result["categories"].append(item)
                except Exception as e:
                    if ctrl:
                        ctrl.log_message("Error processing {0}: {1}".format(attr_name, str(e)))

        # Try additional browser categories
        known = {"instruments", "sounds", "drums", "audio_effects", "midi_effects"}
        for attr in browser_attrs:
            if attr not in known and (category_type == "all" or category_type == attr):
                try:
                    bitem = getattr(app.browser, attr)
                    if hasattr(bitem, "children") or hasattr(bitem, "name"):
                        category = _process_item(bitem)
                        if category:
                            category["name"] = attr.capitalize()
                            result["categories"].append(category)
                except Exception as e:
                    if ctrl:
                        ctrl.log_message("Error processing {0}: {1}".format(attr, str(e)))

        if ctrl:
            ctrl.log_message("Browser tree generated for {0} with {1} root categories".format(
                category_type, len(result["categories"])
            ))
        return result
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting browser tree: {0}".format(str(e)))
            ctrl.log_message(traceback.format_exc())
        raise


def get_browser_items_at_path(song, path, ctrl=None):
    """Get browser items at a specific path."""
    try:
        if ctrl is None:
            raise RuntimeError("get_browser_items_at_path requires ctrl for application()")
        app = ctrl.application()
        if not app:
            raise RuntimeError("Could not access Live application")
        if not hasattr(app, "browser") or app.browser is None:
            raise RuntimeError("Browser is not available in the Live application")

        browser_attrs = [attr for attr in dir(app.browser) if not attr.startswith("_")]
        path_parts = path.split("/")
        if not path_parts:
            raise ValueError("Invalid path")

        root_category = path_parts[0].lower()
        current_item = None

        _category_map = {
            "instruments": "instruments",
            "sounds": "sounds",
            "drums": "drums",
            "audio_effects": "audio_effects",
            "midi_effects": "midi_effects",
        }
        if root_category in _category_map and hasattr(app.browser, _category_map[root_category]):
            current_item = getattr(app.browser, _category_map[root_category])
        else:
            found = False
            for attr in browser_attrs:
                if attr.lower() == root_category:
                    try:
                        current_item = getattr(app.browser, attr)
                        found = True
                        break
                    except Exception as e:
                        if ctrl:
                            ctrl.log_message("Error accessing browser attribute {0}: {1}".format(attr, str(e)))
            if not found:
                return {
                    "path": path,
                    "error": "Unknown or unavailable category: {0}".format(root_category),
                    "available_categories": browser_attrs,
                    "items": [],
                }

        # Navigate through path
        for i in range(1, len(path_parts)):
            part = path_parts[i]
            if not part:
                continue
            if not hasattr(current_item, "children"):
                return {
                    "path": path,
                    "error": "Item at '{0}' has no children".format("/".join(path_parts[:i])),
                    "items": [],
                }
            found = False
            for child in current_item.children:
                if hasattr(child, "name") and child.name.lower() == part.lower():
                    current_item = child
                    found = True
                    break
            if not found:
                return {
                    "path": path,
                    "error": "Path part '{0}' not found".format(part),
                    "items": [],
                }

        # Get items at current path
        items = []
        if hasattr(current_item, "children"):
            for child in current_item.children:
                item_info = {
                    "name": child.name if hasattr(child, "name") else "Unknown",
                    "is_folder": hasattr(child, "children") and bool(child.children),
                    "is_device": hasattr(child, "is_device") and child.is_device,
                    "is_loadable": hasattr(child, "is_loadable") and child.is_loadable,
                    "uri": child.uri if hasattr(child, "uri") else None,
                }
                items.append(item_info)

        result = {
            "path": path,
            "name": current_item.name if hasattr(current_item, "name") else "Unknown",
            "uri": current_item.uri if hasattr(current_item, "uri") else None,
            "is_folder": hasattr(current_item, "children") and bool(current_item.children),
            "is_device": hasattr(current_item, "is_device") and current_item.is_device,
            "is_loadable": hasattr(current_item, "is_loadable") and current_item.is_loadable,
            "items": items,
        }

        if ctrl:
            ctrl.log_message("Retrieved {0} items at path: {1}".format(len(items), path))
        return result
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting browser items at path: {0}".format(str(e)))
            ctrl.log_message(traceback.format_exc())
        raise


def search_browser(song, query, category, ctrl=None):
    """Search the browser for items matching a query."""
    try:
        if ctrl is None:
            raise RuntimeError("search_browser requires ctrl for application()")
        app = ctrl.application()
        if not app:
            raise RuntimeError("Could not access Live application")
        if not hasattr(app, "browser") or app.browser is None:
            raise RuntimeError("Browser is not available in the Live application")

        results = []
        query_lower = query.lower()

        def search_item(item, depth=0, max_depth=5):
            if depth >= max_depth:
                return
            if not item:
                return
            if hasattr(item, "name") and query_lower in item.name.lower():
                result_item = {
                    "name": item.name,
                    "is_folder": hasattr(item, "children") and bool(item.children),
                    "is_device": hasattr(item, "is_device") and item.is_device,
                    "is_loadable": hasattr(item, "is_loadable") and item.is_loadable,
                    "uri": item.uri if hasattr(item, "uri") else None,
                }
                results.append(result_item)
            if hasattr(item, "children"):
                try:
                    children = item.children
                except Exception:
                    return
                if children:
                    for child in children:
                        search_item(child, depth + 1, max_depth)

        _categories = {
            "instruments": "instruments",
            "sounds": "sounds",
            "drums": "drums",
            "audio_effects": "audio_effects",
            "midi_effects": "midi_effects",
        }
        if category == "all":
            for attr_name in _categories.values():
                if hasattr(app.browser, attr_name):
                    search_item(getattr(app.browser, attr_name))
        elif category in _categories and hasattr(app.browser, _categories[category]):
            search_item(getattr(app.browser, _categories[category]))
        else:
            for attr_name in _categories.values():
                if hasattr(app.browser, attr_name):
                    search_item(getattr(app.browser, attr_name))

        return {
            "query": query,
            "category": category,
            "results": results[:50],
            "total_found": len(results),
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error searching browser: {0}".format(str(e)))
            ctrl.log_message(traceback.format_exc())
        raise


def get_user_library(song, ctrl=None):
    """Get the user library browser tree."""
    try:
        if ctrl is None:
            raise RuntimeError("get_user_library requires ctrl for application()")
        app = ctrl.application()
        if not app:
            raise RuntimeError("Could not access Live application")

        items = []
        if hasattr(app.browser, "user_library"):
            user_lib = app.browser.user_library
            if hasattr(user_lib, "children"):
                for child in user_lib.children:
                    items.append({
                        "name": child.name if hasattr(child, "name") else "Unknown",
                        "is_folder": hasattr(child, "children") and bool(child.children),
                        "uri": child.uri if hasattr(child, "uri") else None,
                    })
        return {"items": items, "count": len(items)}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting user library: {0}".format(str(e)))
        raise


def get_user_folders(song, ctrl=None):
    """Get user-configured sample folders from Ableton's browser."""
    try:
        if ctrl is None:
            raise RuntimeError("get_user_folders requires ctrl for application()")
        app = ctrl.application()
        if not app:
            raise RuntimeError("Could not access Live application")

        items = []
        if hasattr(app.browser, "user_folders"):
            for folder in app.browser.user_folders:
                folder_items = []
                if hasattr(folder, "children"):
                    for child in folder.children:
                        folder_items.append({
                            "name": child.name if hasattr(child, "name") else "Unknown",
                            "uri": child.uri if hasattr(child, "uri") else None,
                        })
                items.append({
                    "name": folder.name if hasattr(folder, "name") else "Unknown",
                    "uri": folder.uri if hasattr(folder, "uri") else None,
                    "items": folder_items,
                })
        return {"folders": items, "count": len(items)}
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting user folders: {0}".format(str(e)))
        raise


def preview_browser_item(song, uri=None, action="preview", ctrl=None):
    """Preview (audition) a browser item, or stop the current preview.

    Args:
        uri: The URI of the browser item to preview (required for 'preview' action).
        action: 'preview' to start previewing, 'stop' to stop the current preview.
    """
    try:
        if ctrl is None:
            raise RuntimeError("preview_browser_item requires ctrl for application()")
        app = ctrl.application()
        if not app:
            raise RuntimeError("Could not access Live application")
        browser = app.browser
        if action == "stop":
            browser.stop_preview()
            return {"action": "stop", "previewing": False}
        elif action == "preview":
            if not uri:
                raise ValueError("uri is required for preview action")
            item = find_browser_item_by_uri(browser, uri, ctrl=ctrl)
            if item is None:
                raise Exception("Browser item not found for URI: {0}".format(uri))
            browser.preview_item(item)
            return {
                "action": "preview",
                "uri": uri,
                "name": getattr(item, "name", "Unknown"),
                "previewing": True,
            }
        else:
            raise ValueError("action must be 'preview' or 'stop', got '{0}'".format(action))
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error previewing browser item: {0}".format(str(e)))
        raise
