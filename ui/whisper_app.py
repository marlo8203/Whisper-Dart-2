#!/usr/bin/env python3
"""Whisper Dart — Gradio UI entry point.

Run with `python -m ui.whisper_app` or `gradio ui/whisper_app.py`. The
latter enables hot-reload; `demo` is exposed at module scope so the CLI
can find it.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make `ui` importable when this file is run directly (e.g. by
# `gradio ui/whisper_app.py`, which doesn't add the project root to sys.path).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gradio as gr

from ui import handlers as h
from ui.constants import (
    ALL_ID, APP_DIR, DEFAULT_SAVE, INBOX_ID, LANGUAGES,
    MODEL_HINTS, MODELS, OUTPUT_FORMATS, PURPLE, RECORDING_STORAGE,
)
from ui.db import db
from ui.logging_setup import get_logger, setup_logging
from ui.render import meetings_html, progress_html, project_dropdown_choices
from ui.transcription import cancel as cancel_transcribe, transcribe

# Configure logging before anything else runs so early failures are captured.
setup_logging()
log = get_logger(__name__)


# ── Asset loading ─────────────────────────────────────────────────────────────

def _load_styles() -> str:
    css = (APP_DIR / "styles.css").read_text().replace("{PURPLE}", PURPLE)
    # Apply the user's saved sidebar width (persisted when they drag the handle).
    w = db.get_setting("sidebar_width")
    if w:
        css += (
            f"\n#sidebar {{ flex: 0 0 {int(w)}px !important; "
            f"min-width: {int(w)}px !important; max-width: {int(w)}px !important; }}\n"
        )
    return css


def _load_script() -> str:
    return (APP_DIR / "script.js").read_text()


# ── UI build ──────────────────────────────────────────────────────────────────

def build_ui() -> gr.Blocks:
    initial_projects = db.all_projects()
    initial_history  = db.all_transcripts()
    d = h.defaults()   # saved global default transcribe settings (or hardcoded)

    with gr.Blocks(title="Whisper Dart") as demo:

        sidebar_open     = gr.State(True)
        config_open      = gr.State(False)   # settings panel starts collapsed
        current_entry_id = gr.State(None)
        # Search box covers all filtering now; this is kept as a constant
        # so handler signatures don't churn.
        project_filter   = gr.State(ALL_ID)

        with gr.Row(equal_height=False):

            # ── Toggle column ─────────────────────────────────────
            with gr.Column(elem_id="toggle-col"):
                sidebar_btn = gr.Button("☰", elem_id="btn-sidebar")

            # ── Purple sidebar ────────────────────────────────────
            with gr.Column(scale=3, min_width=340, elem_id="sidebar") as sidebar_col: #zmena default sirky laveho panelu
                gr.Markdown("🎙 **Whisper Dart**", elem_id="sidebar-title")

                # Search bar with a compact "+" (new transcript) button beside it.
                # Per-row deletion lives in each card's "…" menu.
                with gr.Row(elem_id="search-row"):
                    search_box = gr.Textbox(
                        placeholder="🔍  Search by name or project…",
                        elem_id="search-box",
                        container=False,
                        show_label=False,
                        scale=8,
                    )
                    new_tx_btn = gr.Button(
                        "＋",
                        elem_id="btn-new-tx",
                        scale=0,
                        min_width=44,
                    )

                # Custom HTML meetings list (replaces gr.Dataframe). Row
                # interactions (select / rename / delete / tag) are sent from
                # script.js into the hidden command bus below.
                history_table = gr.HTML(
                    value=meetings_html(initial_history, initial_projects),
                    elem_id="meetings-list",
                )
                # Command bus: script.js stashes a JSON command in
                # window.__wd_cmd, then clicks cmd_trigger. The click's `js`
                # reads the global back as the handler input (Gradio 6 ignores
                # programmatic Textbox edits, but button clicks fire reliably).
                cmd_bus = gr.Textbox(
                    elem_id="cmd-bus", show_label=False, container=False,
                )
                cmd_trigger = gr.Button("cmd", elem_id="cmd-trigger")

                project_select = gr.Dropdown(
                    label="Project",
                    choices=project_dropdown_choices(initial_projects),
                    value=INBOX_ID,
                    elem_id="sb-project-select",
                )

                with gr.Row(elem_id="new-project-row"):
                    new_proj_input = gr.Textbox(
                        placeholder="New project name…",
                        scale=4,
                        container=False,
                        show_label=False,
                        elem_id="new-project-input",
                    )
                    new_proj_btn = gr.Button(
                        "＋", scale=0, min_width=36,
                        elem_id="btn-new-project",
                    )

            # ── Centre: audio + outputs ───────────────────────────
            with gr.Column(scale=6, elem_id="outputs-col"):
                # Read-only name header (renaming happens in the sidebar list);
                # kept in sync whenever an entry is loaded/renamed/transcribed.
                name_header = gr.Markdown(
                    "### Untitled", elem_id="name-header",
                )
                # Mic recording + playback only. Uploads (incl. video) go through
                # the UploadButton below — gr.Audio's own upload can't handle
                # video and shows "unsupported format", so it's disabled here.
                audio_in = gr.Audio(
                    label="Audio",
                    type="filepath",
                    sources=["microphone"],
                )
                # Single compact upload path for BOTH audio and video (mp4, mov,
                # …). Video files have their audio track extracted server-side.
                media_upload = gr.UploadButton(
                    "⬆  Upload audio / video (mp4, mov, wav…)",
                    file_types=["audio", "video"],
                    file_count="single",
                    type="filepath",
                    elem_id="upload-btn",
                    size="sm",
                )

                # Transcribe / Cancel live here (not in the collapsible settings)
                # so they're always reachable.
                with gr.Row(elem_id="action-row"):
                    transcribe_btn = gr.Button(
                        "Transcribe", variant="primary", size="lg",
                        scale=3, elem_id="btn-transcribe",
                    )
                    cancel_btn = gr.Button(
                        "Cancel", variant="stop", size="lg",
                        scale=1, elem_id="btn-cancel",
                        interactive=False,
                    )

                # Status line, then the % progress bar right below it.
                info_md = gr.Markdown(
                    value="*Click '+ New Transcript' to start, or pick an existing card.*",
                    elem_id="info-bar",
                )
                progress_bar = gr.HTML(value=progress_html(0), elem_id="progress-bar")
                transcript_out = gr.Textbox(
                    label="Full Transcript", lines=12,
                    placeholder="Clean transcript appears after completion…",
                    buttons=["copy"],
                )
                # Raw whisper output — collapsed by default, below Full Transcript.
                with gr.Accordion("Live Output", open=False):
                    log_out = gr.Textbox(
                        label="Live Output", lines=12, elem_id="log-box",
                        show_label=False,
                    )
                with gr.Accordion("Segment Timeline", open=False):
                    segments_out = gr.Dataframe(
                        headers=["Start", "End", "Text"],
                        datatype=["str", "str", "str"],
                        wrap=True,
                    )

            # ── Config toggle column ──────────────────────────────
            with gr.Column(elem_id="config-toggle-col"):
                config_btn = gr.Button("⚙️", elem_id="btn-config")

            # ── Right: settings (hideable, collapsed by default) ──
            with gr.Column(scale=4, elem_id="config-col", visible=False) as config_col:
                # Save destination — one line. The "Save To:" label is drawn via
                # CSS (#save-to-group::before) so it stays inline with the field.
                with gr.Row(elem_id="save-to-group"):
                    save_dir = gr.Textbox(
                        value=d["save_dir"],
                        scale=5,
                        container=False,
                        show_label=False,
                        elem_id="save-dir-tb",
                    )
                    browse_btn = gr.Button(
                        "📂  Browse",
                        scale=0, min_width=104, elem_id="btn-browse",
                    )

                with gr.Column():
                    model_dd = gr.Dropdown(
                        label="Model", choices=MODELS, value=d["model"],
                    )
                    model_hint_md = gr.Markdown(f"*{MODEL_HINTS.get(d['model'], '')}*")

                lang_dd = gr.Dropdown(
                    label="Language", choices=LANGUAGES, value=d["language"],
                    info="Auto = detected from first 30 s",
                )

                with gr.Row():
                    task_radio = gr.Radio(
                        label="Task",
                        choices=["transcribe", "translate"],
                        value=d["task"],
                        info="translate → English (turbo: transcribe only)",
                    )
                    fmt_dd = gr.Dropdown(
                        label="Output Format", choices=OUTPUT_FORMATS,
                        value=d["output_format"], info="all = txt + srt + vtt + tsv + json",
                    )

                with gr.Accordion("Advanced Options", open=False):
                    with gr.Row():
                        word_ts = gr.Checkbox(
                            label="Word-level Timestamps", value=d["word_timestamps"],
                            info="Timestamp for every word, not just per segment",
                        )
                        condition_prev = gr.Checkbox(
                            label="Condition on Previous Text", value=d["condition_on_previous_text"],
                            info="Use prior text as context — smoother flow, but can repeat mistakes",
                        )
                    with gr.Row():
                        temperature = gr.Slider(
                            label="Temperature", minimum=0.0, maximum=1.0,
                            step=0.05, value=d["temperature"],
                            info="0 = most stable/deterministic · higher = more creative guessing",
                        )
                        beam_size = gr.Slider(
                            label="Beam Size", minimum=1, maximum=10,
                            step=1, value=d["beam_size"],
                            info="Search width — higher = more accurate but slower",
                        )
                    no_speech = gr.Slider(
                        label="No-speech Threshold", minimum=0.0, maximum=1.0,
                        step=0.05, value=d["no_speech_threshold"],
                        info="Above this silence probability a segment is dropped — higher = keep more",
                    )
                    initial_prompt = gr.Textbox(
                        label="Initial Prompt",
                        placeholder="Custom vocabulary, proper nouns, style hints…",
                        lines=2, value=d["initial_prompt"],
                        info="Give the model hints (names, terms, spelling) to improve accuracy",
                    )

                    # ── faster-whisper tuning ──────────────────────
                    with gr.Row():
                        compute_type = gr.Dropdown(
                            label="Compute Type",
                            choices=["int8", "int8_float32", "float32"],
                            value=d["compute_type"],
                            info="int8 = fastest · float32 = most accurate, slower",
                        )
                        best_of = gr.Slider(
                            label="Best Of", minimum=1, maximum=10, step=1, value=d["best_of"],
                            info="sampling candidates (temperature > 0)",
                        )
                    vad_filter = gr.Checkbox(
                        label="VAD filter (skip silence)", value=d["vad_filter"],
                        info="detects speech, skips silent gaps — faster, fewer hallucinations",
                    )
                    with gr.Row():
                        compression_ratio_threshold = gr.Slider(
                            label="Compression Ratio Threshold",
                            minimum=1.0, maximum=5.0, step=0.1, value=d["compression_ratio_threshold"],
                            info="higher = keep more (drops gibberish above this)",
                        )
                        logprob_threshold = gr.Slider(
                            label="Logprob Threshold",
                            minimum=-5.0, maximum=0.0, step=0.1, value=d["logprob_threshold"],
                            info="lower = keep more low-confidence text",
                        )

                # ── Server admin ───────────────────────────────────
                gr.Markdown("---")
                restart_btn = gr.Button(
                    "🔄  Restart Server", elem_id="btn-restart", variant="secondary",
                )
                restart_status = gr.Markdown("", elem_id="restart-status")

                gr.Markdown(
                    '<div id="author-credit">Built by Marian Lojka ®</div>',
                    elem_id="author-credit-md",
                )

        # Settings that persist as global defaults + per transcript, in
        # handlers.SETTING_KEYS order (used for load/new output wiring below).
        settings_components = [
            model_dd, lang_dd, task_radio, fmt_dd, save_dir,
            word_ts, condition_prev, temperature, beam_size, no_speech,
            initial_prompt, compute_type, best_of, vad_filter,
            compression_ratio_threshold, logprob_threshold,
        ]

        # ════════════════════════════════════════════════════════════
        # EVENT WIRING
        # ════════════════════════════════════════════════════════════

        model_dd.change(h.model_hint, model_dd, model_hint_md)

        # Persist each user-changed setting as the global default for new
        # transcripts. User-only events (input/release/blur) don't fire on the
        # programmatic updates from loading an entry, so loads never clobber the
        # defaults (belt-and-braces: save_default also honours the load window).
        import functools as _ft
        _dropdowns = {"model": model_dd, "language": lang_dd, "output_format": fmt_dd,
                      "compute_type": compute_type}
        _radios = {"task": task_radio}
        _checkboxes = {"word_timestamps": word_ts, "condition_on_previous_text": condition_prev,
                       "vad_filter": vad_filter}
        _sliders = {"temperature": temperature, "beam_size": beam_size,
                    "no_speech_threshold": no_speech, "best_of": best_of,
                    "compression_ratio_threshold": compression_ratio_threshold,
                    "logprob_threshold": logprob_threshold}
        _textboxes = {"save_dir": save_dir, "initial_prompt": initial_prompt}
        for _key, _c in {**_dropdowns, **_radios, **_checkboxes}.items():
            _c.input(_ft.partial(h.save_default, _key), inputs=[_c], outputs=[])
        for _key, _c in _sliders.items():
            _c.release(_ft.partial(h.save_default, _key), inputs=[_c], outputs=[])
        for _key, _c in _textboxes.items():
            _c.blur(_ft.partial(h.save_default, _key), inputs=[_c], outputs=[])
        model_dd.change(h.toggle_translate, model_dd, task_radio)

        sidebar_btn.click(
            h.toggle_sidebar,
            inputs=[sidebar_open],
            outputs=[sidebar_col, sidebar_open, sidebar_btn],
        )
        config_btn.click(
            h.toggle_config,
            inputs=[config_open],
            outputs=[config_col, config_open, config_btn],
        )
        restart_btn.click(
            h.restart_server,
            inputs=[],
            outputs=[restart_status],
            # After the process re-execs, poll the server until it answers, then
            # reload the page onto the fresh instance.
            js="""() => {
                setTimeout(function poll() {
                    fetch(window.location.href, {method: 'HEAD', cache: 'no-store'})
                        .then(function() { window.location.reload(); })
                        .catch(function() { setTimeout(poll, 1000); });
                }, 3000);
            }""",
        )

        new_tx_btn.click(
            h.new_transcript,
            inputs=[project_filter, search_box],
            outputs=[
                audio_in, name_header, project_select,
                log_out, transcript_out, info_md, segments_out,
                *settings_components,
                current_entry_id,
                history_table,
            ],
        )

        browse_btn.click(
            h.pick_folder,
            inputs=[save_dir],
            outputs=[save_dir],
            show_progress="hidden",
        )

        # Search + column sort are handled entirely client-side in script.js
        # (filtering/sorting the rendered rows), so no server wiring here.

        new_proj_btn.click(
            h.create_project,
            inputs=[new_proj_input, project_filter, search_box],
            outputs=[new_proj_input, project_select, history_table],
        )
        new_proj_input.submit(
            h.create_project,
            inputs=[new_proj_input, project_filter, search_box],
            outputs=[new_proj_input, project_select, history_table],
        )

        # ── Media upload (audio or video → extract audio) ───────────
        media_upload.upload(
            h.load_media,
            inputs=[media_upload],
            outputs=[audio_in, info_md],
        )

        # ── Auto-save ──────────────────────────────────────────────
        audio_in.change(
            h.save_audio,
            inputs=[current_entry_id, audio_in],
            outputs=[transcript_out, log_out, segments_out, info_md],
            show_progress="hidden",
        )
        project_select.change(
            h.save_project,
            inputs=[current_entry_id, project_select, project_filter, search_box],
            outputs=[history_table],
            show_progress="hidden",
        )

        # ── Transcription ──────────────────────────────────────────
        transcribe_btn.click(
            fn=transcribe,
            inputs=[
                audio_in, current_entry_id, project_select,
                model_dd, lang_dd, task_radio, fmt_dd, save_dir,
                word_ts, temperature, beam_size, initial_prompt,
                condition_prev, no_speech,
                compute_type, vad_filter, best_of,
                compression_ratio_threshold, logprob_threshold,
                project_filter, search_box,
            ],
            outputs=[
                log_out, transcript_out, info_md, segments_out,
                history_table, current_entry_id,
                progress_bar, transcribe_btn, cancel_btn, name_header,
            ],
        )

        cancel_btn.click(
            fn=cancel_transcribe,
            inputs=[],
            outputs=[transcribe_btn, cancel_btn],
            show_progress="hidden",
        )

        # ── Command bus: row select / rename / delete / tag ─────────
        # The `js` fn reads the command JSON stashed by script.js and passes it
        # as the first handler arg (overriding the placeholder cmd_bus value).
        cmd_trigger.click(
            fn=h.dispatch_command,
            inputs=[cmd_bus, current_entry_id],
            outputs=[
                history_table,
                audio_in, name_header, project_select,
                log_out, transcript_out, info_md, segments_out,
                *settings_components,
                current_entry_id,
            ],
            js="(c, id) => [window.__wd_cmd || '', id]",
            show_progress="hidden",
        )

        # ── JS bridge (sidebar resize handle + meetings list controller) ──
        demo.load(fn=None, inputs=None, outputs=None, js=_load_script())

    return demo


# ── Entry ─────────────────────────────────────────────────────────────────────

# Module-level so the `gradio` CLI's hot-reload can find it.
demo = build_ui()


if __name__ == "__main__":
    RECORDING_STORAGE.mkdir(parents=True, exist_ok=True)
    log.info("Starting Whisper Dart on http://127.0.0.1:7860")
    try:
        demo.launch(
            server_name="127.0.0.1",
            server_port=7860,
            inbrowser=True,
            show_error=True,
            share=False,
            css=_load_styles(),
            allowed_paths=[str(RECORDING_STORAGE)],
        )
    except Exception:
        log.exception("Failed to launch the Gradio server")
        raise
