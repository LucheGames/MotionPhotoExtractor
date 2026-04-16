#!/usr/bin/env python3
"""
motion_extract_gui.py -- GUI for Google Pixel Motion Photo Extractor.
No dependencies beyond the Python standard library.
"""

import os
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, scrolledtext, ttk


# ---------------------------------------------------------------------------
# Extraction core (self-contained, returns paths instead of printing)
# ---------------------------------------------------------------------------

JPEG_EXTENSIONS = {'.jpg', '.jpeg'}
MP4_BOX_TYPES   = (b'ftyp', b'moov', b'mdat')


def _parse_container_items(data: bytes) -> list[dict]:
    items = []
    for m in re.finditer(rb'<Container:Item\b(.*?)/>', data, re.DOTALL):
        block = m.group(1)
        def attr(name: bytes, block: bytes = block) -> str:
            a = re.search(rb'Item:' + name + rb'="([^"]*)"', block)
            return a.group(1).decode() if a else ''
        items.append({
            'mime':     attr(b'Mime'),
            'semantic': attr(b'Semantic'),
            'length':   int(attr(b'Length')  or 0),
            'padding':  int(attr(b'Padding') or 0),
        })
    return items


def _find_video_new_format(data: bytes) -> bytes | None:
    if b'GCamera:MotionPhoto' not in data:
        return None
    items = _parse_container_items(data)
    non_primary = [i for i in items if i['semantic'] != 'Primary']
    if not non_primary:
        return None
    offset_from_eof = 0
    for item in reversed(non_primary):
        offset_from_eof += item['padding'] + item['length']
        if 'video' in item['mime']:
            video_start = len(data) - offset_from_eof
            candidate = data[video_start:]
            if len(candidate) >= 8 and candidate[4:8] in MP4_BOX_TYPES:
                return candidate
    return None


def _find_video_old_format(data: bytes) -> bytes | None:
    if b'MicroVideoOffset' not in data:
        return None
    m = re.search(rb'MicroVideoOffset="(\d+)"', data)
    if not m:
        return None
    video_start = len(data) - int(m.group(1))
    candidate = data[video_start:]
    if len(candidate) >= 8 and candidate[4:8] in MP4_BOX_TYPES:
        return candidate
    return None


def _find_video_fallback(data: bytes) -> bytes | None:
    pos = len(data) - 8
    while pos > 4:
        if data[pos:pos + 4] in MP4_BOX_TYPES:
            return data[pos - 4:]
        pos -= 1
    return None


def _unique_output_path(out_dir: Path, stem: str) -> Path:
    candidate = out_dir / f'{stem}_video.mp4'
    if not candidate.exists():
        return candidate
    counter = 2
    while True:
        candidate = out_dir / f'{stem}_video_{counter}.mp4'
        if not candidate.exists():
            return candidate
        counter += 1


def _extract_video(filepath: Path, output_dir: Path) -> Path | None:
    """Returns the output Path on success, None if not a motion photo."""
    data = filepath.read_bytes()
    if b'MotionPhoto' not in data and b'MicroVideo' not in data:
        return None
    video_data = (
        _find_video_new_format(data)
        or _find_video_old_format(data)
        or _find_video_fallback(data)
    )
    if not video_data or len(video_data) < 16:
        return None
    out_path = _unique_output_path(output_dir, filepath.stem)
    out_path.write_bytes(video_data)
    return out_path


def _collect_jpegs(target: Path, recursive: bool) -> list[Path]:
    pattern = '**/*' if recursive else '*'
    return sorted(
        f for f in target.glob(pattern)
        if f.is_file() and f.suffix.lower() in JPEG_EXTENSIONS
    )


# ---------------------------------------------------------------------------
# VLC / player helpers
# ---------------------------------------------------------------------------

def _find_vlc() -> str | None:
    if sys.platform == 'darwin':
        p = Path('/Applications/VLC.app/Contents/MacOS/VLC')
        return str(p) if p.exists() else None
    if sys.platform == 'win32':
        for base in filter(None, [os.environ.get('PROGRAMFILES'), os.environ.get('PROGRAMFILES(X86)')]):
            p = Path(base) / 'VideoLAN' / 'VLC' / 'vlc.exe'
            if p.exists():
                return str(p)
        return None
    return 'vlc'  # Linux: assume on PATH


def _open_default(path: Path) -> None:
    if sys.platform == 'darwin':
        subprocess.Popen(['open', str(path)])
    elif sys.platform == 'win32':
        os.startfile(str(path))
    else:
        subprocess.Popen(['xdg-open', str(path)])


def _open_playlist_in_vlc(paths: list[Path]) -> None:
    if not paths:
        return
    playlist = paths[0].parent / 'extracted_videos.m3u'
    playlist.write_text('#EXTM3U\n' + '\n'.join(str(p) for p in paths) + '\n')
    vlc = _find_vlc()
    if vlc:
        subprocess.Popen([vlc, str(playlist)])
    else:
        _open_default(playlist)


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

def _worker(source: Path, output_dir: Path, recursive: bool, log_q: queue.Queue) -> None:
    """
    Runs extraction on a background thread.
    Puts (tag, payload) onto log_q.
    Tags: 'ok', 'skip', 'warn', 'info', 'done'
    'done' payload is a list[Path] of extracted files.
    """
    if source.is_file():
        jpegs = [source] if source.suffix.lower() in JPEG_EXTENSIONS else []
        if not jpegs:
            log_q.put(('warn', f'{source.name} is not a JPEG.'))
            log_q.put(('done', []))
            return
    else:
        jpegs = _collect_jpegs(source, recursive)

    if not jpegs:
        log_q.put(('warn', 'No JPEG files found in the selected folder.'))
        log_q.put(('done', []))
        return

    log_q.put(('info', f'{len(jpegs)} JPEG(s) found\n'))
    output_dir.mkdir(parents=True, exist_ok=True)

    extracted: list[Path] = []
    skipped = 0

    for f in jpegs:
        out = _extract_video(f, output_dir)
        if out:
            size_mb = out.stat().st_size / 1_048_576
            log_q.put(('ok', f'[OK]  {f.name}  ->  {out.name}  ({size_mb:.1f} MB)'))
            extracted.append(out)
        else:
            log_q.put(('skip', f'[--]  {f.name}'))
            skipped += 1

    log_q.put(('info', f'\nDone.  {len(extracted)} extracted   {skipped} skipped'))
    log_q.put(('done', extracted))


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title('Motion Photo Extractor')
        self.minsize(640, 480)
        self.resizable(True, True)
        self._log_q: queue.Queue = queue.Queue()
        self._extracted: list[Path] = []
        self._build_ui()
        self._poll()

    # ── Layout ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        pad = dict(padx=12, pady=6)

        # Source folder row
        src_frame = ttk.LabelFrame(self, text='Source folder')
        src_frame.pack(fill='x', **pad)
        self._src_var = tk.StringVar()
        ttk.Entry(src_frame, textvariable=self._src_var).pack(
            side='left', fill='x', expand=True, padx=(6, 2), pady=6)
        ttk.Button(src_frame, text='Browse...', command=self._browse_src).pack(
            side='left', padx=(0, 6), pady=6)

        # Output folder row
        out_frame = ttk.LabelFrame(self, text='Output folder')
        out_frame.pack(fill='x', **pad)
        self._out_var = tk.StringVar()
        ttk.Entry(out_frame, textvariable=self._out_var).pack(
            side='left', fill='x', expand=True, padx=(6, 2), pady=6)
        ttk.Button(out_frame, text='Browse...', command=self._browse_out).pack(
            side='left', padx=(0, 6), pady=6)

        # Options + Extract button row
        ctrl_frame = ttk.Frame(self)
        ctrl_frame.pack(fill='x', padx=12, pady=(0, 6))
        self._recursive_var = tk.BooleanVar()
        ttk.Checkbutton(
            ctrl_frame, text='Include subfolders', variable=self._recursive_var
        ).pack(side='left')
        self._extract_btn = ttk.Button(
            ctrl_frame, text='Extract', command=self._start_extraction)
        self._extract_btn.pack(side='right')

        # Progress log
        log_frame = ttk.LabelFrame(self, text='Progress')
        log_frame.pack(fill='both', expand=True, **pad)
        self._log = scrolledtext.ScrolledText(
            log_frame, state='disabled', height=16,
            font=('Courier', 11), wrap='none')
        self._log.pack(fill='both', expand=True, padx=6, pady=6)
        self._log.tag_config('ok',   foreground='#2a9d2a')
        self._log.tag_config('skip', foreground='#888888')
        self._log.tag_config('warn', foreground='#cc5500')
        self._log.tag_config('info', foreground='#000000')

        # Action buttons (post-extraction)
        action_frame = ttk.Frame(self)
        action_frame.pack(fill='x', padx=12, pady=(0, 12))
        self._vlc_btn = ttk.Button(
            action_frame, text='Open playlist in VLC',
            command=self._open_vlc, state='disabled')
        self._vlc_btn.pack(side='left', padx=(0, 6))
        self._preview_btn = ttk.Button(
            action_frame, text='Preview last video',
            command=self._preview_last, state='disabled')
        self._preview_btn.pack(side='left')

    # ── Actions ─────────────────────────────────────────────────────────────

    def _browse_src(self) -> None:
        d = filedialog.askdirectory(title='Select source folder')
        if d:
            self._src_var.set(d)
            if not self._out_var.get():
                self._out_var.set(str(Path(d) / 'extracted_videos'))

    def _browse_out(self) -> None:
        d = filedialog.askdirectory(title='Select output folder')
        if d:
            self._out_var.set(d)

    def _start_extraction(self) -> None:
        src_raw = self._src_var.get().strip()
        if not src_raw:
            self._log_line('warn', 'Please select a source folder first.')
            return

        source = Path(src_raw).expanduser().resolve()
        if not source.exists():
            self._log_line('warn', f'Path not found: {source}')
            return

        out_raw = self._out_var.get().strip()
        if out_raw:
            output_dir = Path(out_raw).expanduser().resolve()
        else:
            base = source if source.is_dir() else source.parent
            output_dir = base / 'extracted_videos'
            self._out_var.set(str(output_dir))

        self._clear_log()
        self._extracted = []
        self._extract_btn.config(state='disabled')
        self._vlc_btn.config(state='disabled')
        self._preview_btn.config(state='disabled')

        threading.Thread(
            target=_worker,
            args=(source, output_dir, self._recursive_var.get(), self._log_q),
            daemon=True,
        ).start()

    def _open_vlc(self) -> None:
        _open_playlist_in_vlc(self._extracted)

    def _preview_last(self) -> None:
        if self._extracted:
            _open_default(self._extracted[-1])

    # ── Log helpers ──────────────────────────────────────────────────────────

    def _log_line(self, tag: str, text: str) -> None:
        self._log.config(state='normal')
        self._log.insert('end', text + '\n', tag)
        self._log.see('end')
        self._log.config(state='disabled')

    def _clear_log(self) -> None:
        self._log.config(state='normal')
        self._log.delete('1.0', 'end')
        self._log.config(state='disabled')

    # ── Queue poll (runs on main thread every 50 ms) ─────────────────────────

    def _poll(self) -> None:
        try:
            while True:
                tag, payload = self._log_q.get_nowait()
                if tag == 'done':
                    self._extracted = payload
                    self._extract_btn.config(state='normal')
                    if payload:
                        self._vlc_btn.config(state='normal')
                        self._preview_btn.config(state='normal')
                else:
                    self._log_line(tag, payload)
        except queue.Empty:
            pass
        self.after(50, self._poll)


# ---------------------------------------------------------------------------

if __name__ == '__main__':
    App().mainloop()
