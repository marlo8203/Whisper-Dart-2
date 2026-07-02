"""Whisper subprocess driver — transcribe generator + cancel handler."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

import gradio as gr

from ui.audio import duration_seconds, store as store_audio
from ui.constants import INBOX_ID, PROJECT_ROOT
from ui.db import db
from ui.logging_setup import get_logger
from ui.render import meetings_html, progress_html

log = get_logger(__name__)

# Prefer faster-whisper (whisper-ctranslate2) — same CLI flags and identical
# segment output format, but ~2-4× faster on CPU via int8 quantisation. Fall
# back to the reference `whisper` binary if it isn't installed. All binaries are
# resolved next to the running interpreter so the app works without PATH setup.
def _find_backend():
    here = Path(sys.executable).parent
    for name in ("whisper-ctranslate2", "whisper"):
        p = here / name
        if p.exists():
            return str(p)
    return shutil.which("whisper-ctranslate2") or shutil.which("whisper") or "whisper"


_WHISPER = _find_backend()
_IS_CT2 = "ctranslate2" in _WHISPER            # faster-whisper backend?

_SEG_RE = re.compile(
    r"\[(\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})"
    r"\s*-->\s*"
    r"(\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})\]\s{1,2}(.+)"
)
# openai-whisper: "Detected language: Czech"
# faster-whisper: "Detected language 'cs'/'Czech' with probability ..."
_LANG_RE = re.compile(r"[Dd]etected language[:\s']+([A-Za-z][\w ]*)")

# Module-level state so the Cancel button can reach the running subprocess and
# so selection can show a live transcription's progress. Single-user local app,
# so per-session keying isn't necessary.
#   entry_id — id of the entry currently being transcribed (None when idle)
#   viewing  — id of the entry the user is currently looking at
#   live     — latest (log, transcript, info, segment_rows, pct) snapshot
_active = {
    "proc": None, "cancelled": False,
    "entry_id": None, "viewing": None,
    "live": ("", "", "", [], 0),
}


def running_entry():
    """Id of the entry being transcribed right now, or None."""
    return _active.get("entry_id")


def set_viewing(entry_id):
    """Tell the running generator which entry the user is looking at, so it only
    streams content into the output boxes when that entry is on screen."""
    _active["viewing"] = entry_id


def live_state():
    """Latest live (log, transcript, info, segment_rows, pct) of the running
    transcription — used to populate the boxes when switching back to it."""
    return _active.get("live") or ("", "", "", [], 0)


def _ts_to_seconds(ts: str) -> float:
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    m, s = parts
    return int(m) * 60 + float(s)


def _build_cmd(audio, model, language, task, output_format,
               save_dir, word_ts, temperature, beam_size,
               initial_prompt, condition_prev, no_speech,
               compute_type="int8", vad_filter=False, best_of=5,
               compression_ratio_threshold=2.4, logprob_threshold=-1.0) -> list[str]:
    cmd = [_WHISPER, audio, "--verbose", "True"]
    cmd += ["--model", model]
    if _IS_CT2:
        # faster-whisper only: quantisation (speed/accuracy) + optional VAD.
        cmd += ["--compute_type", str(compute_type or "int8"), "--device", "cpu"]
        if vad_filter:
            cmd += ["--vad_filter", "True"]
    if language != "Auto-detect":
        cmd += ["--language", language]
    cmd += ["--task", task]
    cmd += ["--output_format", output_format]
    cmd += ["--output_dir", save_dir]
    cmd += ["--temperature", f"{float(temperature):.2f}"]
    if int(beam_size) != 5:
        cmd += ["--beam_size", str(int(beam_size))]
    if int(best_of) != 5:
        cmd += ["--best_of", str(int(best_of))]
    if abs(float(compression_ratio_threshold) - 2.4) > 1e-6:
        cmd += ["--compression_ratio_threshold", f"{float(compression_ratio_threshold)}"]
    if abs(float(logprob_threshold) - (-1.0)) > 1e-6:
        cmd += ["--logprob_threshold", f"{float(logprob_threshold)}"]
    ns = float(no_speech)
    if abs(ns - 0.6) > 1e-6:
        cmd += ["--no_speech_threshold", f"{ns}"]
    if word_ts:
        cmd += ["--word_timestamps", "True"]
    if not condition_prev:
        cmd += ["--condition_on_previous_text", "False"]
    if initial_prompt and initial_prompt.strip():
        cmd += ["--initial_prompt", initial_prompt.strip()]
    return cmd


def transcribe(audio, current_id, project_id_for_new, model, language, task,
               output_format, save_dir, word_ts, temperature, beam_size,
               initial_prompt, condition_prev, no_speech,
               compute_type, vad_filter, best_of,
               compression_ratio_threshold, logprob_threshold,
               project_filter, search_query):
    """Generator yielding (log, transcript_partial, info, segments_rows,
    history_rows, current_entry_id, progress_html, transcribe_btn, cancel_btn,
    name_header).

    Streams partial transcript + progress as Whisper emits each segment, then
    persists the entry on completion. Honours `_active['cancelled']` so the
    Cancel button can break the loop early. The trailing element keeps the
    read-only name header current; only the final yield changes it.
    """
    nm = gr.update()  # no-op for the name header on all but the final yield
    projects = db.all_projects()
    history  = db.all_transcripts()
    # Idle list snapshot (no running marker) — used on early returns / errors.
    idle_rows = meetings_html(history, projects)
    # Live list snapshot with the running indicator next to current_id.
    live_rows = meetings_html(history, projects, running_id=current_id)
    btn_idle = (gr.update(interactive=True), gr.update(interactive=False))
    btn_run  = (gr.update(interactive=False), gr.update(interactive=True))

    if not audio:
        yield ("", "", "*Please upload or record audio first.*",
               [], idle_rows, current_id, progress_html(0), *btn_idle, nm)
        return

    os.makedirs(save_dir, exist_ok=True)
    cmd = _build_cmd(audio, model, language, task, output_format, save_dir,
                     word_ts, temperature, beam_size, initial_prompt,
                     condition_prev, no_speech,
                     compute_type, vad_filter, best_of,
                     compression_ratio_threshold, logprob_threshold)

    duration = duration_seconds(audio)
    console = "$ " + " ".join(f'"{a}"' if " " in str(a) else str(a) for a in cmd) + "\n\n"
    _active["cancelled"] = False
    _active["entry_id"] = current_id     # mark this entry as running (green icon)
    _active["viewing"] = current_id      # we start out looking at it
    me = current_id

    def gate(log_v, txt_v, info_v, segs_v, pct):
        """Remember the live snapshot and stream content into the boxes only
        when the user is viewing this transcription; otherwise no-op so another
        selected entry's view isn't clobbered."""
        _active["live"] = (log_v, txt_v, info_v, segs_v, pct)
        if _active.get("viewing") in (None, me):
            return log_v, txt_v, info_v, segs_v, progress_html(pct)
        return gr.update(), gr.update(), gr.update(), gr.update(), gr.update()

    log.info(
        "Transcription started — model=%s, lang=%s, task=%s, audio=%s (%.1fs)",
        model, language, task, Path(audio).name, duration or 0.0,
    )
    g = gate(console, "", "*Transcribing…*", [], 0)
    yield (*g[:4], live_rows, current_id, g[4], *btn_run, nm)

    try:
        # PYTHONUNBUFFERED forces whisper to flush each segment line as soon as
        # it's printed; without it, output is block-buffered into a pipe and the
        # whole transcript arrives at once at the end (no live streaming).
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, cwd=str(PROJECT_ROOT),
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        _active["proc"] = proc
        for line in proc.stdout:
            console += line
            if _active.get("cancelled"):
                break
            partial_segs = list(_SEG_RE.finditer(console))
            partial_text = " ".join(m.group(3).strip() for m in partial_segs)
            partial_rows = [[m.group(1), m.group(2), m.group(3)] for m in partial_segs]
            if duration and partial_segs:
                last_end = _ts_to_seconds(partial_segs[-1].group(2))
                pct = max(0, min(99, int(last_end / duration * 100)))
            else:
                pct = 0
            g = gate(console, partial_text, "*Transcribing…*", partial_rows, pct)
            # history_table = no-op in the loop: the running icon is already set,
            # and re-rendering every line would clobber list edits + be costly.
            yield (*g[:4], gr.update(), current_id, g[4], *btn_run, nm)
        proc.wait()
        if proc.returncode not in (0, None) and not _active.get("cancelled"):
            log.error(
                "whisper exited with code %s. Output tail:\n%s",
                proc.returncode, "\n".join(console.splitlines()[-15:]),
            )
    except OSError as exc:
        _active["proc"] = None
        _active["entry_id"] = None
        log.exception("Failed to run whisper subprocess: %s", exc)
        yield (console + f"\nError: {exc}", "", f"*Error: {exc}*",
               [], idle_rows, current_id, progress_html(0), *btn_idle, nm)
        return

    _active["proc"] = None
    if _active.get("cancelled"):
        _active["cancelled"] = False
        _active["entry_id"] = None
        log.info("Transcription cancelled by user")
        yield (console + "\n[cancelled]", "", "*Transcription cancelled.*",
               [], meetings_html(db.all_transcripts(), db.all_projects()),
               current_id, progress_html(0), *btn_idle, nm)
        return

    segments_raw = [
        {"start": m.group(1), "end": m.group(2), "text": m.group(3)}
        for m in _SEG_RE.finditer(console)
    ]
    full_text = " ".join(s["text"].strip() for s in segments_raw)
    segments_rows = [[s["start"], s["end"], s["text"]] for s in segments_raw]

    lang_match = _LANG_RE.search(console)
    detected = lang_match.group(1).upper() if lang_match else (language or "?").upper()
    n = len(segments_rows)
    info = (
        f"Language: **{detected}**  ·  {n} segment{'s' if n != 1 else ''}  ·  "
        f"Saved to `{save_dir}`"
    )

    existing = db.get_transcript(current_id) if current_id else None
    final_title = (existing.get("title") if existing else "") or Path(audio).stem
    final_project = (
        project_id_for_new
        if project_id_for_new and project_id_for_new != INBOX_ID
        else None
    )

    entry_data = {
        "audio_name": Path(audio).name,
        "title": final_title,
        "model": model, "language": language, "task": task,
        "detected_language": detected,
        "text": full_text, "log": console, "segments": segments_raw,
        "project_id": final_project,
        "config": {
            "output_format": output_format, "save_dir": save_dir,
            "word_timestamps": word_ts, "temperature": temperature,
            "beam_size": beam_size, "initial_prompt": initial_prompt,
            "condition_on_previous_text": condition_prev,
            "no_speech_threshold": no_speech,
            "compute_type": compute_type, "best_of": best_of,
            "vad_filter": vad_filter,
            "compression_ratio_threshold": compression_ratio_threshold,
            "logprob_threshold": logprob_threshold,
        },
    }

    if current_id and db.get_transcript(current_id):
        entry_id = current_id
    else:
        entry_id = str(uuid.uuid4())

    entry_data["audio_path"] = store_audio(entry_id, audio)

    try:
        if current_id and db.get_transcript(current_id):
            db.update_transcript(current_id, **entry_data)
        else:
            db.insert_transcript({
                "id": entry_id,
                "timestamp": datetime.now().isoformat(),
                **entry_data,
            })
    except Exception:
        _active["entry_id"] = None
        log.exception("Failed to persist transcript %s", entry_id)
        yield (console, full_text, "*Error: could not save transcript (see logs).*",
               segments_rows, idle_rows, entry_id, progress_html(100), *btn_idle, nm)
        return

    _active["entry_id"] = None   # done — clears the running icon on next render
    log.info(
        "Transcription complete — entry=%s, language=%s, %d segments",
        entry_id, detected, n,
    )
    new_html = meetings_html(db.all_transcripts(), projects, selected_id=entry_id)
    # Only push the final content into the boxes if the user is viewing this
    # entry; otherwise it's saved to the DB and loaded on next select.
    g = gate(console, full_text, info, segments_rows, 100)
    yield (*g[:4], new_html, entry_id, g[4], *btn_idle, gr.update(value=f"### {final_title}"))


def cancel():
    """Terminate the running whisper subprocess. Safe to call when nothing
    is running. Returns idle button state for (transcribe_btn, cancel_btn)."""
    proc = _active.get("proc")
    if proc and proc.poll() is None:
        _active["cancelled"] = True
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        except OSError:
            pass
    return gr.update(interactive=True), gr.update(interactive=False)
