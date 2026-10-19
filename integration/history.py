import json
import os
from pathlib import Path
import tempfile

from .transformation import build_click_flow

# File path to the current session overview.
SESSION_OVERVIEW_FILE = "Session_overview.json"
MAX_HISTORY_ITEMS = 10
DATA_DIR = Path(os.environ.get("LOG2MOTION_DATA_DIR", Path(__file__).resolve().parents[1] / "runtime" / "figma"))


def data_path(file_path):
    """Resolve only file names under the configured history directory."""
    file_path = Path(file_path)
    if file_path.name != str(file_path) or file_path.name in (".", ".."):
        raise ValueError("History paths must be plain file names.")
    return DATA_DIR / file_path

# ----------------------------------------
# Generic JSON file I/O
# ----------------------------------------

def load_json_file(file_path, default_value=None):
    """Load the JSON from the given file_path, or return a default_value (an empty list,
    unless given) if the file doesn't exist or contains invalid JSON."""
    if default_value is None:
        default_value = []
    file_path = data_path(file_path)
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return default_value
    return default_value


def save_json_file(file_path, data):
    """Write the data to the file_path as JSON."""
    file_path = data_path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    # Replace atomically so an interrupted server cannot truncate session history.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=file_path.parent,
                                     suffix=".json.tmp", delete=False) as f:
        temporary = Path(f.name)
        try:
            json.dump(data, f, indent=2, ensure_ascii=False, allow_nan=False)
        except BaseException:
            f.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, file_path)
    finally:
        temporary.unlink(missing_ok=True)

# ----------------------------------------
# Session management
# ----------------------------------------

def get_session_filenames(session_id):
    """Build the three per-session history file names (Figma export, raw
    sim results, and the summarized change history)."""
    session_id = str(session_id)
    if not session_id.isdecimal():
        raise ValueError("Session IDs must be numeric.")
    return {
        "figma_history": f"FIGMA_HISTORY_session_{session_id}.json",
        "sim_history": f"SIM_HISTORY_session_{session_id}.json",
        "change_history": f"CHANGE_HISTORY_session_{session_id}.json"
    }


def get_session_overview():
    """Read the Session_overview.json file, or return the empty default shape if
    it doesn't exist yet."""
    default_overview = {
        "count": 0,
        "activeSessionId": None,
        "sessions": []
    }

    return load_json_file(SESSION_OVERVIEW_FILE, default_value=default_overview)


def update_overview_file(sessions, active_session_id=None):
    """
    Write the given session list to Session_overview.json and resolve the
    active_session_id to a still-valid session if the one requested (or
    previously active) no longer exists.
    """
    # Fall back to the previously active session if none was given.
    current_overview = get_session_overview()

    if active_session_id is None:
        active_session_id = current_overview.get("activeSessionId")

    # Check whether the activeSessionId is still present in the existing sessions.
    existing_ids = [s["id"] for s in sessions]
    if sessions and (active_session_id not in existing_ids):
        active_session_id = existing_ids[0]
    elif not sessions:
        active_session_id = None

    overview_data = {
        "count": len(sessions),
        "activeSessionId": active_session_id,
        "sessions": sessions
    }

    save_json_file(SESSION_OVERVIEW_FILE, overview_data)
    return overview_data


def set_active_session(session_id):
    """Mark the session_id as active, if it exists and return the overview either way."""
    overview = get_session_overview()
    existing_ids = [s["id"] for s in overview.get("sessions", [])]

    if session_id in existing_ids:
        overview["activeSessionId"] = session_id
        save_json_file(SESSION_OVERVIEW_FILE, overview)
    else:
        raise ValueError("The selected session does not exist.")

    return overview


def add_session(session_name=None):
    """Create a new session with a new id and create its three
    empty history files, and return the updated overview."""
    overview = get_session_overview()
    sessions = overview.get("sessions", [])

    # Determine the next available id (1-4).
    existing_ids = [int(s["id"]) for s in sessions if s["id"].isdigit()]
    next_id = str(max(existing_ids) + 1) if existing_ids else "1"

    if not session_name:
        session_name = f"Session {next_id}"

    files = get_session_filenames(next_id)

    new_session = {
        "id": next_id,
        "name": session_name,
        "total_run_counter": 0,
        "files": files
    }

    sessions.append(new_session)

    # Create the three history files for this session.
    for path in files.values():
        save_json_file(path, [])

    return update_overview_file(sessions, active_session_id=next_id)


def delete_session(session_id):
    """Delete a session and its three history files, and return the
    updated overview."""
    overview = get_session_overview()
    sessions = overview.get("sessions", [])

    # Find the session to be deleted.
    session_to_delete = next((s for s in sessions if s["id"] == session_id), None)

    if session_to_delete:
        # Physically delete the three history files.
        for file_path in session_to_delete["files"].values():
            file_path = data_path(file_path)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError as e:
                    print(f"Error whilst deleting {file_path}: {e}")

        # Remove the session from the in-memory list too.
        sessions = [s for s in sessions if s["id"] != session_id]
        return update_overview_file(sessions)

    return overview

# ----------------------------------------
# Per run history
# ----------------------------------------

def update_history_file(file_path, new_entry):
    """Append the new entry to the JSON array at the file_path and keep only the
    most recent MAX_HISTORY_ITEMS entries."""
    history = load_json_file(file_path, default_value=[])
    history.append(new_entry)
    if len(history) > MAX_HISTORY_ITEMS:
        history = history[-MAX_HISTORY_ITEMS:]
    save_json_file(file_path, history)
    return history


def create_click_path(figma_data):
    """Ensure the figma_data has a "clickPath" field or derive one from its
    raw "nodes" list if it's missing."""
    if not figma_data:
        return {"clickPath": []}

    if "clickPath" not in figma_data and "nodes" in figma_data:
        figma_data = dict(figma_data)
        figma_data["clickPath"] = build_click_flow(figma_data["nodes"])

    return figma_data


def extract_summary(sim_data):
    """Compute the aggregated and per-episode metrics (average muscle cost,
    total steps, total contacts) from the raw Log2Motion simulation results and
    tolerates a couple of different raw result shapes."""
    if not sim_data or "results" not in sim_data:
        return None

    episodes_summary = {}
    all_costs = []
    all_steps = 0
    total_contacts = 0

    results = sim_data.get("results", {})
    outcome_metadata = sim_data.get("episodes", {})

    items = (
        results.items()
        if isinstance(results, dict)
        else enumerate(results)
    )

    for ep_id, ep_content in items:
        costs = []
        traj = []
        contact_count = 0

        if isinstance(ep_content, dict):
            raw_costs = ep_content.get("muscle_costs") or []
            costs = [float(c) for c in raw_costs if c is not None]
            traj = ep_content.get("trajectory") or []
            raw_contact = ep_content.get("contact_information", 0)

            if isinstance(raw_contact, (int, float)):
                contact_count = int(raw_contact)
            elif isinstance(raw_contact, list):
                contact_count = sum(1 for c in raw_contact if c == 1 or c is True)
            else:
                contact_count = 1 if bool(raw_contact) else 0

        elif isinstance(ep_content, list):
            traj = ep_content

            costs = [
                float(step["muscle_cost"])
                for step in ep_content
                if step.get("muscle_cost") is not None
            ]

            contact_count = sum(
                1
                for step in ep_content
                if step.get("contact_information", 0) == 1
            )

        ep_avg_cost = round(sum(costs) / len(costs), 5) if len(costs) > 0 else 0.0

        all_costs.extend(costs)
        all_steps += len(traj)
        total_contacts += contact_count

        episodes_summary[str(ep_id)] = {
            "total_steps": len(traj),
            "contact_count": contact_count,
            "avg_muscle_cost": ep_avg_cost,
            "outcome": outcome_metadata.get(str(ep_id), outcome_metadata.get(ep_id)),
        }

    if len(all_costs) > 0:
        overall_avg_cost = round(sum(all_costs) / float(len(all_costs)), 5)
    else:
        overall_avg_cost = 0.0

    return {
        "avg_muscle_cost": overall_avg_cost,
        "total_steps": all_steps,
        "total_contacts": total_contacts,
        "episodes": episodes_summary,
        "outcome_summary": summarize_outcomes(sim_data),
    }


def summarize_outcomes(sim_data):
    """Keep process completion separate from successful physical gestures."""
    metadata = sim_data.get("episodes", {})
    summary = {"total": 0, "successful": 0, "failed": 0, "timed_out": 0, "unknown": 0}
    results = sim_data.get("results", {})
    episode_ids = results if isinstance(results, dict) else range(len(results))
    for episode_id in episode_ids:
        summary["total"] += 1
        outcome = metadata.get(str(episode_id), metadata.get(episode_id))
        if not isinstance(outcome, dict) or not isinstance(outcome.get("is_success"), bool):
            summary["unknown"] += 1
        elif outcome["is_success"]:
            summary["successful"] += 1
        else:
            summary["failed"] += 1
            if outcome.get("truncated"):
                summary["timed_out"] += 1
    if summary["failed"]:
        status = "partial_failure" if summary["successful"] or summary["unknown"] else "failed"
    elif summary["unknown"] or not summary["total"]:
        status = "unknown"
    else:
        status = "success"
    return dict(summary, status=status)


def record_simulation_run(current_figma_data, current_sim_data, timestamp="", session_id=None):
    """
    Append the current run to the active session's three history files
    (Figma export, raw sim results, and the summarized change-history
    entry) and return the new runs id.
    """
    overview = get_session_overview()
    active_id = session_id or overview.get("activeSessionId")

    if not active_id:
        raise ValueError("Select or create a session before running a simulation.")

    sessions = overview.get("sessions", [])
    active_session = next((s for s in overview["sessions"] if s["id"] == active_id), None)
    if not active_session:
        raise ValueError("The simulation's session no longer exists.")

    files = active_session["files"]
    figma_file = files["figma_history"]
    sim_file = files["sim_history"]
    change_file = files["change_history"]

    run_counter = active_session.get("total_run_counter", 0) + 1
    active_session["total_run_counter"] = run_counter
    update_overview_file(sessions)

    # Create the clickpath from the given nodes.
    current_figma_data = create_click_path(current_figma_data)

    # Update figma and sim file for the current session.
    update_history_file(figma_file, current_figma_data)
    update_history_file(sim_file, current_sim_data)

    metrics_data = extract_summary(current_sim_data)

    run_entry = {
        "id": str(run_counter),
        "name": f"Run {run_counter}",
        "timestamp": timestamp,
        "selected_elements": current_figma_data.get("clickPath", []),
        "metrics": metrics_data,
        "chart_images": {}
    }

    update_history_file(change_file, run_entry)

    return run_entry["id"]


def get_formatted_history_for_ui(session_id=None):
    """Reads the given (or active) sessions change history file and
    returns it."""

    overview = get_session_overview()
    active_id = session_id or overview.get("activeSessionId", "1")

    active_session = next(
        (s for s in overview.get("sessions", []) if s["id"] == active_id), None
    )
    if not active_session:
        return []

    change_file = active_session["files"]["change_history"]
    return load_json_file(change_file, [])

# ----------------------------------------
# Cross run operations
# ----------------------------------------


def compare_runs(run_a, run_b, session_id=None):
    """
    Compare two runs and return their added/removed/reordered/modified elements
    plus both runs chart images.
    """

    if isinstance(run_a, (str, int)) or isinstance(run_b, (str, int)):
        overview = get_session_overview()
        active_id = str(session_id) if session_id is not None else str(overview.get("activeSessionId", "1"))

        active_session = next(
            (s for s in overview.get("sessions", []) if str(s.get("id")) == active_id),
            None
        )

        if not active_session or "files" not in active_session or "change_history" not in active_session["files"]:
            return {}

        run_history_file = active_session["files"]["change_history"]
        all_runs = load_json_file(run_history_file, [])

        if not all_runs:
            return {}

        if isinstance(run_a, (str, int)):
            target_id_a = str(run_a)
            run_a = next((r for r in all_runs if str(r.get("id")) == target_id_a), {})

        if isinstance(run_b, (str, int)):
            target_id_b = str(run_b)
            run_b = next((r for r in all_runs if str(r.get("id")) == target_id_b), {})

    run_a = run_a or {}
    run_b = run_b or {}

    path_a = run_a.get("selected_elements", [])
    path_b = run_b.get("selected_elements", [])

    def index_occurrences(path):
        counts = {}
        indexed = {}
        for position, item in enumerate(path, 1):
            item = dict(item) if isinstance(item, dict) else {"id": str(item), "name": str(item)}
            node_id = str(item.get("id"))
            counts[node_id] = counts.get(node_id, 0) + 1
            item["number"] = position
            item["occurrence"] = counts[node_id]
            indexed[(node_id, counts[node_id])] = item
        return indexed

    map_a = index_occurrences(path_a)
    map_b = index_occurrences(path_b)

    def identity(item):
        return {key: item.get(key) for key in ("id", "name", "number", "occurrence")}

   # In B but not A
    added_elements = [identity(item) for key, item in map_b.items() if key not in map_a]

    # In A but not B
    removed_elements = [identity(item) for key, item in map_a.items() if key not in map_b]

    # Present in both, but at a different position in the click path.
    reordered_elements = []
    for key, item_b in map_b.items():
        if key in map_a:
            p_num = map_a[key].get("number")
            c_num = item_b.get("number")
            if p_num is not None and c_num is not None and p_num != c_num:
                reordered_elements.append({
                    **identity(item_b),
                    "from_number": p_num,
                    "to_number": c_num
                })

    # Present in both, but moved and/or resized on the canvas.
    modified_elements = []
    for key, item_b in map_b.items():
        if key in map_a:
            item_a = map_a[key]

            p_x, c_x = item_a.get("x"), item_b.get("x")
            p_y, c_y = item_a.get("y"), item_b.get("y")
            if (p_x is not None and c_x is not None and p_x != c_x) or (p_y is not None and c_y is not None and p_y != c_y):
                modified_elements.append({
                    **identity(item_b),
                    "property": "Position",
                    "old_v1": p_x,
                    "old_v2": p_y,
                    "new_v1": c_x,
                    "new_v2": c_y
                })

            previous_center = item_a.get("normalizedCenter")
            current_center = item_b.get("normalizedCenter")
            if (isinstance(previous_center, (list, tuple)) and len(previous_center) == 2
                    and isinstance(current_center, (list, tuple)) and len(current_center) == 2
                    and tuple(previous_center) != tuple(current_center)):
                modified_elements.append({
                    **identity(item_b),
                    "property": "Screen position",
                    "old_v1": previous_center[1],
                    "old_v2": previous_center[0],
                    "new_v1": current_center[1],
                    "new_v2": current_center[0],
                })

            p_w, c_w = item_a.get("width"), item_b.get("width")
            p_h, c_h = item_a.get("height"), item_b.get("height")
            if (p_w is not None and c_w is not None and p_w != c_w) or (p_h is not None and c_h is not None and p_h != c_h):
                modified_elements.append({
                    **identity(item_b),
                    "property": "Size",
                    "old_v1": p_w,
                    "old_v2": p_h,
                    "new_v1": c_w,
                    "new_v2": c_h
                })

    return {
        "added_elements": added_elements,
        "removed_elements": removed_elements,
        "order_changed": len(reordered_elements) > 0,
        "reordered_elements": reordered_elements,
        "modified_elements": modified_elements,
        "chart_images_a": run_a.get("chart_images", []),
        "chart_images_b": run_b.get("chart_images", [])
    }


def save_charts_to_history(session_id, run_id, chart_images):
    """
    Attache the chart images to a runs change history entry - the most
    recent run by default, or a specific one if run_id matches - and
    return the refreshed history for the UI.
    """
    overview = get_session_overview()
    active_id = session_id or overview.get("activeSessionId", "1")
    active_session = next((s for s in overview.get("sessions", []) if s["id"] == active_id), None)

    if not active_session:
        return []

    change_file = active_session["files"]["change_history"]
    change_hist = load_json_file(change_file, [])

    if change_hist:
        target_run = change_hist[-1]
        if run_id is not None:
            target_run = next((run for run in change_hist if str(run.get("id")) == str(run_id)), None)
            if target_run is None:
                raise ValueError("The requested run is no longer in history.")

        target_run["chart_images"] = chart_images
        save_json_file(change_file, change_hist)

    return get_formatted_history_for_ui(active_id)
