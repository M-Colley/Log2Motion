"""Bound chart payload size while retaining the full movement and contacts."""


def reduce_data_for_figma(simulation_data, max_points=300, precision=4):
    if not simulation_data:
        raise ValueError("No simulation data was returned for this action.")
    if max_points < 1:
        raise ValueError("max_points must be positive.")
    count = min(len(simulation_data), max_points)
    if count == 1:
        indices = [0]
    else:
        indices = [round(i * (len(simulation_data) - 1) / (count - 1))
                   for i in range(count)]
    points = [simulation_data[index] for index in indices]
    # Associate every original contact with its nearest retained chart point.
    contacts = set()
    cursor = 0
    for index, point in enumerate(simulation_data):
        while cursor + 1 < count and index > (indices[cursor] + indices[cursor + 1]) / 2:
            cursor += 1
        # Telemetry distinguishes button contact (1) from other screen contact (2).
        if point.get("contact_information") == 1:
            contacts.add(cursor)
    start = points[0]["time"]
    dt = ((points[-1]["time"] - start) / (count - 1)) if count > 1 else None
    return {"status": "success", "chart_data": {
        "start_time": round(start, precision),
        "dt": round(dt, precision) if dt is not None else None,
        # Rounded sample indices aren't evenly spaced. Preserve actual times so
        # the UI doesn't shift contacts or infer an inflated gesture duration.
        "times": [round(float(point["time"]), precision) for point in points],
        "duration": round(float(simulation_data[-1]["time"]), precision),
        "trajectory": [[round(float(v), precision) for v in point["IFtip_pos"]]
                       for point in points],
        "muscle_costs": [round(float(point["muscle_cost"]), precision) for point in points],
        "contact_indices": sorted(contacts),
    }}
