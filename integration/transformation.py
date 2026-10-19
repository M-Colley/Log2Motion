"""Translate ordered Figma selections to normalized Log2Motion tap actions."""

import math


def build_node_map(nodes):
    return {str(node["id"]): node for node in nodes}


def build_click_flow(nodes):
    """Retain repeated selections: a node ID is not a click's unique ID."""
    if isinstance(nodes, dict):
        nodes = nodes.values()
    path = [dict(node, number=index) for index, node in enumerate(nodes, 1)]
    if not path:
        raise ValueError("Select at least one element before starting a simulation.")
    return path


def get_frame_from_node(node, node_map):
    current = node
    seen = set()
    frame = node if node.get("type") == "FRAME" else None
    while current.get("parentId"):
        parent_id = str(current["parentId"])
        if parent_id in seen:
            raise ValueError("The Figma node hierarchy contains a cycle.")
        seen.add(parent_id)
        current = node_map.get(parent_id)
        if current is None:
            break
        if current.get("type") == "FRAME":
            frame = current
    return frame


def normalize(node, frame, node_map=None):
    if "normalizedCenter" in node:
        center = node["normalizedCenter"]
        if not isinstance(center, (list, tuple)) or len(center) != 2:
            raise ValueError("normalizedCenter must contain [y, x].")
        y, x = (float(value) for value in center)
    elif frame is not None:
        width, height = float(frame["width"]), float(frame["height"])
        if width <= 0 or height <= 0:
            raise ValueError("The containing Figma frame must have a positive size.")
        if node is frame:
            y, x = 0.5, 0.5
        else:
            cx = float(node.get("x", 0)) + float(node.get("width", 0)) / 2
            cy = float(node.get("y", 0)) + float(node.get("height", 0)) / 2
            current = node
            seen = set()
            while str(current.get("parentId")) != str(frame["id"]):
                parent_id = str(current.get("parentId"))
                if parent_id in seen:
                    raise ValueError("The Figma node hierarchy contains a cycle.")
                seen.add(parent_id)
                current = (node_map or {}).get(parent_id)
                if current is None:
                    raise ValueError("Missing ancestor geometry in the Figma export.")
                cx += float(current.get("x", 0))
                cy += float(current.get("y", 0))
            y, x = cy / height, cx / width
    else:
        raise ValueError(
            "The export has no screen coordinates. Build and import the plugin in "
            "integration/figma-plugin, then select elements inside a screen frame."
        )
    if not all(math.isfinite(value) and 0 <= value <= 1 for value in (y, x)):
        raise ValueError("Selected element centers must lie inside their screen frame.")
    return (round(y, 6), round(x, 6))


def transform_figma_data(data):
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        raise ValueError("The Figma export must contain a nodes list.")
    nodes = data["nodes"]
    node_map = build_node_map(nodes + data.get("ancestors", []))
    selections = data.get("clickPath", nodes)
    path = build_click_flow(selections)
    episode = []
    for item in path:
        node = node_map.get(str(item["id"]))
        if node is None:
            raise ValueError(f"Selected Figma node {item['id']} is missing.")
        touch = normalize(node, get_frame_from_node(node, node_map), node_map)
        episode.append({"time": [], "yx_touch": touch, "yx_lift": touch,
                        "action_type": "ActionType.DUAL_POINT"})
    return episode
