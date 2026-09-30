"""Stable file identity across local upload and backend job rows."""


def file_key(row):
    upload_id = row.get("upload_id") or ""
    if upload_id.startswith("ui-upload:"):
        return upload_id
    uuid = row["uuid"]
    if uuid.startswith("upload:"):
        return "ui-upload:" + uuid.removeprefix("upload:")
    return uuid


def current_selection(rows, selected):
    keys = {file_key(row) for row in selected}
    return [row for row in rows if file_key(row) in keys]
