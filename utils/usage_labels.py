"""Fixed labels; never use paths, filenames or other user-provided values."""
SECTIONS = [
    ("Successfully queued jobs", [("queued.transcript", "Transcript"), ("queued.subtitles", "Subtitles")]),
    ("Files per upload batch", [(f"upload.batch.{n}", f"{n} file" + ("s" if n != 1 else "")) for n in range(1, 6)]),
    ("Completed upload sizes", list(zip([f"upload.size.{n}" for n in range(5)],
        ["Under 100 MiB", "100–500 MiB", "500 MiB–1 GiB", "1–2 GiB", "2 GiB and above"]))),
    ("Group features", [("group.created", "Groups created"), ("group.member_added", "Members added"), ("group.limit_blocked", "Requests blocked by group limits")]),
    ("User provisioning", [("provision.matched", "Rule matches"), ("provision.changed", "Changes applied")]),
    ("Subtitle exports", [(f"export.subtitles.{f}", f.upper()) for f in ("srt", "vtt")]),
    ("Transcript exports", [(f"export.transcript.{f}", f.upper()) for f in ("txt", "json", "rtf", "csv", "tsv")]),
    ("Editor tools", [("preview.enabled", "Preview subtitles enabled"), ("confidence.adjusted", "Confidence slider adjustments"),
                      ("confidence.listen", "Listen used"), ("subtitles.checked", "Check subtitles used")]),
]

SECTION_HELP = {
    "Files per upload batch": "How often an upload was started with this many files selected. An upload can still fail or be cancelled afterwards.",
    "Completed upload sizes": "Number of successfully uploaded files in each size range.",
    "Subtitle exports": "Number of files requested for download in each format. Downloading five files together counts as five exports.",
    "Transcript exports": "Number of files requested for download in each format. Downloading five files together counts as five exports.",
    "User provisioning": "Rule matches: how often an enabled rule matched a user during sign-in. Changes applied: how often rules changed access or group membership. Test runs are excluded.",
    "Editor tools": "How often each tool was used, rather than how many different people used it.",
}
