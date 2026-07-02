"""Gradio event handlers — load/save/delete/new for transcripts and projects."""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path

import gradio as gr

from ui.audio import (
    delete_stored, duration_seconds, extract_audio, is_video, store as store_audio,
)
from ui.constants import (
    ALL_ID, DEFAULT_SAVE, INBOX_ID, MODEL_HINTS, SELECT_NONE, TAG_PALETTE,
)
from ui import transcription as tx
from ui.db import db
from ui.logging_setup import get_logger
from ui.render import (
    entry_title, meetings_html, project_dropdown_choices, project_name,
)

log = get_logger(__name__)

# Loading an entry sets components programmatically, which fires .change/.input
# events. We suppress the autosave + default-saving for a short window right
# after any load so a load doesn't wipe the transcript or overwrite the global
# defaults with the loaded entry's values.
_suppress_save_until = 0.0


def _suppress_autosave():
    global _suppress_save_until
    _suppress_save_until = time.time() + 2.0


# ── Transcribe settings that persist (as global defaults + per transcript) ──────
# Order here defines the order these components are wired as load/new outputs.
SETTING_KEYS = [
    "model", "language", "task", "output_format", "save_dir",
    "word_timestamps", "condition_on_previous_text", "temperature", "beam_size",
    "no_speech_threshold", "initial_prompt", "compute_type", "best_of",
    "vad_filter", "compression_ratio_threshold", "logprob_threshold",
]
_SETTING_DEFAULTS = {
    "model": "large-v3-turbo", "language": "Auto-detect", "task": "transcribe",
    "output_format": "txt", "save_dir": DEFAULT_SAVE,
    "word_timestamps": False, "condition_on_previous_text": True,
    "temperature": 0.0, "beam_size": 5, "no_speech_threshold": 0.6,
    "initial_prompt": "", "compute_type": "int8", "best_of": 5,
    "vad_filter": False, "compression_ratio_threshold": 2.4, "logprob_threshold": -1.0,
}


def defaults() -> dict:
    """Global default transcribe settings (saved values over hardcoded)."""
    d = dict(_SETTING_DEFAULTS)
    d.update(db.get_defaults() or {})
    return d


def _settings_from_entry(e: dict) -> dict:
    """Settings for a transcript: model/language/task are top-level columns, the
    rest live in its config; fall back to the hardcoded defaults."""
    cfg = e.get("config", {}) if e else {}
    vals = {}
    for k in SETTING_KEYS:
        if k in ("model", "language", "task"):
            vals[k] = (e.get(k) if e else None) or _SETTING_DEFAULTS[k]
        else:
            vals[k] = cfg.get(k, _SETTING_DEFAULTS[k])
    return vals


def _settings_updates(vals: dict) -> list:
    """gr.update list for the 16 setting components, in SETTING_KEYS order."""
    return [gr.update(value=vals[k]) for k in SETTING_KEYS]


def save_default(key, value):
    """Persist a user-changed setting as the global default for new transcripts.
    Skipped during the post-load suppression window so loading a transcript
    doesn't overwrite the defaults with that entry's values."""
    if time.time() < _suppress_save_until:
        return
    if key in SETTING_KEYS:
        db.set_default(key, value)


def list_html(selected_id: str = "") -> str:
    """Fresh meetings-list HTML from current DB state, marking any in-progress
    transcription with the running icon. `selected_id`: an id to highlight, ""
    to preserve the client's current selection, or SELECT_NONE to clear it."""
    return meetings_html(
        db.all_transcripts(), db.all_projects(),
        running_id=tx.running_entry(), selected_id=selected_id,
    )


def _tag_color(name: str) -> str:
    """Stable colour for a tag name, so the same tag always looks the same."""
    return TAG_PALETTE[sum(map(ord, name)) % len(TAG_PALETTE)]


# ── Loading ───────────────────────────────────────────────────────────────────

# Load / new / delete output order (23 editor comps + current_entry_id):
#   audio, name, project, log, transcript, info, segments, <16 settings>, current_id
_N_EDITOR = 7 + len(SETTING_KEYS)   # 23 components before current_entry_id


def load_history_by_id(entry_id):
    """Row clicked in sidebar → load an entry, restoring its saved transcribe
    settings too. Returns 24 values: 7 editor comps + 16 settings + current_id."""
    if not entry_id:
        return (gr.update(),) * (_N_EDITOR + 1)
    e = db.get_transcript(entry_id)
    if not e:
        return (gr.update(),) * (_N_EDITOR + 1)

    segs = e.get("segments", [])
    segment_rows = [[s.get("start", ""), s.get("end", ""), s.get("text", "")] for s in segs]
    audio_path = e.get("audio_path") or None
    if audio_path and not Path(audio_path).exists():
        log.warning("Audio missing for entry %s: %s", entry_id, e.get("audio_path"))
        audio_path = None
    return (
        gr.update(value=audio_path),                          # audio_in
        gr.update(value=_name_md(e)),                         # name_header
        gr.update(value=e.get("project_id") or INBOX_ID),     # project_select
        gr.update(value=e.get("log") or ""),                  # log_out
        gr.update(value=e.get("text") or ""),                 # transcript_out
        gr.update(value=_info_line(e)),                       # info_md
        gr.update(value=segment_rows),                        # segments_out
        *_settings_updates(_settings_from_entry(e)),          # 16 settings
        e.get("id"),                                          # current_entry_id
    )


# ── Command bus ─────────────────────────────────────────────────────────────
#
# The custom HTML meetings list can't fire Gradio events directly, so its JS
# writes a JSON command into a hidden textbox whose `.input` runs this handler.
# Commands: select | rename | delete | tag_toggle | tag_create.

_NOOP_EDITOR = (gr.update(),) * _N_EDITOR  # 23 editor+settings components


def dispatch_command(cmd_json, current_id):
    """Parse a JSON command from the list JS and mutate state accordingly.

    Always returns (meetings_html, <23 editor+settings updates>, current_id).
    Editor components are left untouched (no-op) unless the action loads or
    clears the currently-open transcript.
    """
    try:
        cmd = json.loads(cmd_json) if cmd_json else {}
    except (ValueError, TypeError):
        cmd = {}
    action = cmd.get("action")
    tid = cmd.get("id")

    if action == "set_sidebar_width":
        try:
            w = int(float(cmd.get("value", 0)))
            if 220 <= w <= 900:
                db.set_setting("sidebar_width", w)
        except (ValueError, TypeError):
            pass
        # Light no-op: don't re-render the list or touch the editor.
        return (gr.update(), *_NOOP_EDITOR, current_id)

    if action == "select" and tid:
        _suppress_autosave()                      # the load sets components → don't autosave/clobber defaults
        tx.set_viewing(tid)                       # tell the running generator what's on screen
        editor = list(load_history_by_id(tid))    # 23 editor+settings + current_id
        if tid == tx.running_entry():
            # This entry is being transcribed right now — show its live progress
            # (log/transcript/info/segments) instead of the stale saved copy.
            live_log, live_txt, live_info, live_segs, _pct = tx.live_state()
            editor[3] = gr.update(value=live_log)          # log_out
            editor[4] = gr.update(value=live_txt)          # transcript_out
            editor[5] = gr.update(value=live_info or "*Transcribing…*")  # info_md
            editor[6] = gr.update(value=live_segs)         # segments_out
        return (list_html(selected_id=tid), *editor[:_N_EDITOR], editor[_N_EDITOR])

    if action == "rename" and tid:
        new_title = (cmd.get("value") or "").strip()
        db.update_transcript(tid, title=new_title)
        log.info("Renamed transcript %s → %r", tid, new_title)
        editor = list(_NOOP_EDITOR)
        # If the renamed entry is the one loaded, refresh name header (idx 1) +
        # info line (idx 5) so both stay current.
        if tid == current_id:
            e = db.get_transcript(tid)
            editor[1] = gr.update(value=_name_md(e))
            editor[5] = gr.update(value=_info_line(e))
        return (list_html(), *editor, current_id)

    if action == "delete" and tid:
        entry = db.get_transcript(tid)
        if entry:
            delete_stored(entry.get("audio_path", ""))
        db.delete_transcript(tid)
        log.info("Deleted transcript %s", tid)
        if tid == current_id:
            cleared = list(_NOOP_EDITOR)
            cleared[0] = gr.update(value=None)             # audio_in
            cleared[1] = gr.update(value="### Untitled")   # name_header
            cleared[2] = gr.update(value=INBOX_ID)         # project_select
            cleared[3] = gr.update(value="")               # log_out
            cleared[4] = gr.update(value="")               # transcript_out
            cleared[5] = gr.update(value="*Transcript deleted.*")  # info_md
            cleared[6] = gr.update(value=[])               # segments_out
            return (list_html(selected_id=SELECT_NONE), *cleared, None)
        return (list_html(), *_NOOP_EDITOR, current_id)

    if action == "tag_toggle" and tid:
        tag_id = cmd.get("tag_id")
        if tag_id:
            now_on = db.toggle_transcript_tag(tid, tag_id)
            log.info("Tag %s %s transcript %s", tag_id, "→" if now_on else "✕", tid)
        return (list_html(), *_NOOP_EDITOR, current_id)

    if action == "tag_create" and tid:
        name = (cmd.get("value") or "").strip()
        if name:
            tag = db.get_or_create_tag(name, _tag_color(name))
            if tag:
                db.toggle_transcript_tag(tid, tag["id"])
                log.info("Created + attached tag %r to %s", name, tid)
        return (list_html(), *_NOOP_EDITOR, current_id)

    # Unknown / empty command — just re-render, preserving selection.
    return (list_html(), *_NOOP_EDITOR, current_id)


def _name_md(e: dict | None) -> str:
    """Markdown for the read-only name header above the Audio box."""
    return f"### {entry_title(e)}" if e else "### Untitled"


def _info_line(e: dict | None) -> str:
    if not e:
        return ""
    projects = db.all_projects()
    det = e.get("detected_language", "?")
    n = len(e.get("segments", []))
    return (
        f"**{entry_title(e)}**  ·  📁 {project_name(e.get('project_id'), projects)}  \n"
        f"`{e.get('audio_name', '')}` · "
        f"Language: **{det}** · {n} segment{'s' if n != 1 else ''}"
    )


# ── Creating + saving ─────────────────────────────────────────────────────────

def new_transcript(project_filter, search_query):
    """Insert an empty draft, reset the editor, and load the saved global
    default settings so a fresh transcript starts from your preferred config.

    Returns: 7 editor comps + 16 settings + current_id + meetings list."""
    _suppress_autosave()   # the settings reset below must not re-save defaults
    d = defaults()
    default_project = (
        project_filter if project_filter and project_filter != ALL_ID else None
    )
    entry_id = str(uuid.uuid4())
    db.insert_transcript({
        "id": entry_id,
        "timestamp": datetime.now().isoformat(),
        "title": "",
        "audio_name": "",
        "model": d["model"],
        "language": d["language"],
        "task": d["task"],
        "detected_language": "?",
        "text": "",
        "log": "",
        "segments": [],
        "project_id": default_project,
        "config": {k: d[k] for k in SETTING_KEYS if k not in ("model", "language", "task")},
    })
    return (
        gr.update(value=None),                                  # audio_in
        gr.update(value="### Untitled"),                        # name_header
        gr.update(value=default_project or INBOX_ID),           # project_select
        gr.update(value=""),                                    # log_out
        gr.update(value=""),                                    # transcript_out
        gr.update(value="*New draft created — add audio, then Transcribe. Rename it from the list.*"),  # info_md
        gr.update(value=[]),                                    # segments_out
        *_settings_updates(d),                                  # 16 settings → defaults
        entry_id,                                               # current_entry_id
        list_html(selected_id=entry_id),                        # meetings list
    )


def create_project(name, current_filter, search_query):
    """Create a new project, then refresh dropdown + sidebar list."""
    project = db.add_project(name)
    projects = db.all_projects()
    return (
        "",  # clear new-project input
        gr.update(
            choices=project_dropdown_choices(projects),
            value=(project["id"] if project else INBOX_ID),
        ),
        list_html(),
    )


# ── Media upload ──────────────────────────────────────────────────────────────

def load_media(file_path):
    """Handle a file chosen via the Upload button. Videos get their audio track
    extracted; audio passes through. Returns updates for (audio_in, info_md) —
    the resulting audio is pushed into the player, ready to transcribe.
    """
    if not file_path:
        return gr.update(), gr.update()
    name = Path(file_path).name
    if is_video(file_path):
        log.info("Video uploaded (%s) — extracting audio track", name)
        try:
            audio_path = extract_audio(file_path)
        except RuntimeError as exc:
            log.error("Audio extraction failed for %s: %s", name, exc)
            return gr.update(), gr.update(value=f"*Error: {exc}*")
        log.info("Extracted audio from %s → %s", name, Path(audio_path).name)
        info = f"*Audio extracted from `{name}` — ready to transcribe.*"
        return gr.update(value=audio_path), gr.update(value=info)
    log.info("Audio file uploaded: %s", name)
    return gr.update(value=file_path), gr.update(value=f"*Added `{name}` — ready to transcribe.*")


# ── Auto-save handlers ────────────────────────────────────────────────────────

def _fmt_duration(seconds: float | None) -> str:
    if not seconds:
        return ""
    m, s = divmod(int(round(seconds)), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def save_audio(entry_id, audio):
    """Persist a freshly uploaded/recorded audio file into RECORDING_STORAGE
    and link it to the loaded entry.

    When the audio genuinely changes on an entry that was already transcribed,
    the old transcript no longer matches the audio, so it's cleared (in the DB
    and the UI) to keep the entry consistent — otherwise you'd see new audio
    next to a stale transcript.

    Returns updates for (transcript_out, log_out, segments_out, info_md).
    Idempotent on load: fires early with no-op updates when the file is already
    inside RECORDING_STORAGE (i.e. the change came from loading an entry, not a
    new upload/recording)."""
    noop = (gr.update(), gr.update(), gr.update(), gr.update())
    if not entry_id or not audio:
        return noop
    if time.time() < _suppress_save_until:
        return noop  # fired from loading an entry, not a real upload/recording
    entry = db.get_transcript(entry_id)
    if not entry:
        return noop

    old = entry.get("audio_path", "")
    # If the incoming path is exactly what's already linked, this fired from a
    # load (the row-click sets audio_in to the stored path), not a new
    # upload/recording — do nothing. Comparing against the entry's own path is
    # safer than a RECORDING_STORAGE check, which would misfire on legacy
    # entries whose audio lives in an old/relocated folder.
    if audio == old:
        return noop

    src = Path(audio)
    if old:
        delete_stored(old)
    new_path = store_audio(entry_id, audio)

    dur = duration_seconds(new_path)
    dur_str = _fmt_duration(dur)
    had_transcript = bool((entry.get("text") or "").strip())

    fields = {"audio_path": new_path, "audio_name": src.name}
    if had_transcript:
        # Old transcript belongs to the old audio — drop it so state is consistent.
        fields.update(text="", log="", segments=[], detected_language="?")
        log.info("Audio replaced on transcribed entry %s — cleared stale transcript", entry_id)

    db.update_transcript(entry_id, **fields)

    if dur and dur > 1800:  # 30 min — likely a runaway/left-on recording
        log.warning("Long audio added to %s: %s (%.0f s)", entry_id, dur_str, dur)

    info = (
        f"*Audio added"
        + (f" · **{dur_str}**" if dur_str else "")
        + "* — click **Transcribe** to generate the transcript."
    )
    if had_transcript:
        return (gr.update(value=""), gr.update(value=""), gr.update(value=[]),
                gr.update(value=info))
    return (gr.update(), gr.update(), gr.update(), gr.update(value=info))


def save_project(entry_id, new_project_id, project_filter, search_query):
    if not entry_id:
        return gr.update()
    pid = None if not new_project_id or new_project_id == INBOX_ID else new_project_id
    db.update_transcript(entry_id, project_id=pid)
    return list_html()


# ── Delete ────────────────────────────────────────────────────────────────────

def delete_current(entry_id, project_filter, search_query):
    """Delete the currently loaded transcript and clear the editor."""
    if not entry_id:
        return (gr.update(),) * 9
    entry = db.get_transcript(entry_id)
    if entry:
        delete_stored(entry.get("audio_path", ""))
    db.delete_transcript(entry_id)
    return (
        gr.update(value=None),                  # audio_in
        gr.update(value="### Untitled"),        # name_header
        gr.update(value=INBOX_ID),              # project_select
        "", "",                                 # log_out, transcript_out
        "*Transcript deleted.*",                # info_md
        [],                                     # segments_out
        None,                                   # current_entry_id
        list_html(selected_id=SELECT_NONE),     # meetings list
    )


# ── Misc UI ───────────────────────────────────────────────────────────────────

def model_hint(name: str) -> str:
    return f"*{MODEL_HINTS.get(name, '')}*"


def toggle_translate(name: str):
    """Turbo models don't support translate — disable it when one is chosen."""
    disabled = "turbo" in name
    return gr.update(
        choices=["transcribe"] if disabled else ["transcribe", "translate"],
        value="transcribe",
        interactive=not disabled,
    )


def toggle_sidebar(is_open: bool):
    new_open = not is_open
    icon = "☰" if new_open else "▶"
    return gr.update(visible=new_open), new_open, gr.update(value=icon)


def toggle_config(is_open: bool):
    """Show/hide the settings column (mirrors the sidebar toggle)."""
    new_open = not is_open
    return gr.update(visible=new_open), new_open, gr.update(value="⚙️")


def restart_server() -> str:
    """Restart the server by re-executing the process.

    Runs the actual re-exec on a short-delayed daemon thread so this click's
    HTTP response reaches the browser first (which then polls + reloads). Any
    in-flight transcription is terminated so its subprocess doesn't linger.
    `os.execv` replaces the current image in place (same PID); the listening
    socket is close-on-exec, so port 7860 is freed for the fresh process.
    """
    import os
    import sys
    import threading

    from ui.constants import PROJECT_ROOT

    proc = tx._active.get("proc")
    if proc is not None:
        tx._active["cancelled"] = True
        try:
            proc.terminate()
        except Exception:
            pass

    def _reexec():
        time.sleep(1.0)
        log.info("Restarting Whisper Dart server (re-exec)")
        os.chdir(str(PROJECT_ROOT))
        os.execv(sys.executable, [sys.executable, "-m", "ui.whisper_app", *sys.argv[1:]])

    threading.Thread(target=_reexec, daemon=True).start()
    return "🔄 **Restarting server…** this page will reload automatically."


def pick_folder(current: str) -> str:
    """macOS native folder picker. Returns the chosen path, or `current` on cancel."""
    result = subprocess.run(
        ["osascript", "-e", "POSIX path of (choose folder)"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip().rstrip("/")
    return current
