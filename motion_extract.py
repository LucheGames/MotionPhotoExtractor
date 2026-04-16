#!/usr/bin/env python3
"""
motion_extract.py — Extract embedded MP4 video from Google Pixel motion photos.

No external dependencies — pure Python stdlib.

Supports both Pixel XMP motion photo formats:
  New format  GCamera:MotionPhoto + Container:Directory with Item:Length per item
  Old format  MicroVideoOffset attribute (older Pixel firmware)
  Fallback    backward scan for ftyp/moov MP4 box markers

Non-JPEG files (MP4, PNG, etc.) in a folder are silently skipped.
Plain JPEGs with no motion photo metadata are skipped quietly.
Running the tool twice won't overwrite existing output — a counter suffix is added.

Usage:
  Single file:   python3 motion_extract.py photo.jpg
  Folder:        python3 motion_extract.py /path/to/photos/
  Recursive:     python3 motion_extract.py /path/to/photos/ --recursive
  Custom output: python3 motion_extract.py /path/to/photos/ --output /path/to/out/
  Same folder:   python3 motion_extract.py /path/to/photos/ --output same

Output files are named  <stem>_video.mp4 (or _video_2.mp4, _video_3.mp4 if already exist).
"""

import re
import sys
from pathlib import Path

JPEG_EXTENSIONS = {'.jpg', '.jpeg'}
MP4_BOX_TYPES   = (b'ftyp', b'moov', b'mdat')


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def parse_container_items(data: bytes) -> list[dict]:
    """
    Parse all <Container:Item .../> blocks from embedded XMP.
    Returns list of dicts: mime, semantic, length, padding.
    """
    items = []
    for m in re.finditer(rb'<Container:Item\b(.*?)/>', data, re.DOTALL):
        block = m.group(1)
        def attr(name: bytes) -> str:
            a = re.search(rb'Item:' + name + rb'="([^"]*)"', block)
            return a.group(1).decode() if a else ''
        items.append({
            'mime':     attr(b'Mime'),
            'semantic': attr(b'Semantic'),
            'length':   int(attr(b'Length')  or 0),
            'padding':  int(attr(b'Padding') or 0),
        })
    return items


def find_video_new_format(data: bytes) -> bytes | None:
    """
    New format (GCamera:MotionPhoto / Container:Directory).
    Non-primary items are appended to the file in Container order.
    Walk non-primary items in reverse from EOF to locate video start.
    """
    if b'GCamera:MotionPhoto' not in data:
        return None

    items = parse_container_items(data)
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


def find_video_old_format(data: bytes) -> bytes | None:
    """
    Old format: MicroVideoOffset = number of bytes from EOF to video start.
    """
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


def find_video_fallback(data: bytes) -> bytes | None:
    """
    Last resort: scan backward for the first ftyp/moov/mdat box marker.
    Only used when XMP metadata is absent or unparseable.
    """
    pos = len(data) - 8
    while pos > 4:
        if data[pos:pos + 4] in MP4_BOX_TYPES:
            return data[pos - 4:]
        pos -= 1
    return None


# ---------------------------------------------------------------------------
# Output path helper
# ---------------------------------------------------------------------------

def unique_output_path(out_dir: Path, stem: str) -> Path:
    """
    Return a path like <out_dir>/<stem>_video.mp4 that does not yet exist.
    If it does exist, try _video_2.mp4, _video_3.mp4, etc.
    """
    candidate = out_dir / f'{stem}_video.mp4'
    if not candidate.exists():
        return candidate
    counter = 2
    while True:
        candidate = out_dir / f'{stem}_video_{counter}.mp4'
        if not candidate.exists():
            return candidate
        counter += 1


# ---------------------------------------------------------------------------
# Core extraction
# ---------------------------------------------------------------------------

def extract_video(filepath: Path, output_dir: Path) -> bool:
    """
    Extract the embedded MP4 from a single JPEG motion photo.
    Returns True on success, False if not a motion photo.
    """
    data = filepath.read_bytes()

    # Quick bail — no motion photo markers at all
    if b'MotionPhoto' not in data and b'MicroVideo' not in data:
        return False

    video_data = (
        find_video_new_format(data)
        or find_video_old_format(data)
        or find_video_fallback(data)
    )

    if not video_data or len(video_data) < 16:
        print(f'  [!] {filepath.name}: markers found but could not locate MP4 data')
        return False

    out_path = unique_output_path(output_dir, filepath.stem)
    out_path.write_bytes(video_data)

    size_mb = len(video_data) / 1_048_576
    print(f'  [OK] {filepath.name}  ->  {out_path.name}  ({size_mb:.1f} MB)')
    return True


# ---------------------------------------------------------------------------
# Folder / file processing
# ---------------------------------------------------------------------------

def collect_jpegs(target: Path, recursive: bool) -> list[Path]:
    pattern = '**/*' if recursive else '*'
    return sorted(
        f for f in target.glob(pattern)
        if f.is_file() and f.suffix.lower() in JPEG_EXTENSIONS
    )


def process(target: Path, output_dir: Path, recursive: bool) -> tuple[int, int]:
    extracted = skipped = 0

    if target.is_file():
        if target.suffix.lower() in JPEG_EXTENSIONS:
            ok = extract_video(target, output_dir)
            extracted += ok
            skipped   += not ok
        else:
            print(f'[!] {target.name} - not a JPEG, skipping')
            skipped += 1
        return extracted, skipped

    # Directory
    jpegs = collect_jpegs(target, recursive)
    total = len(jpegs)
    if total == 0:
        print(f'[!] No JPEG files found in {target}')
        return 0, 0

    print(f'[>>] {total} JPEG(s) found in {target}'
          f'{"  (recursive)" if recursive else ""}\n')

    for f in jpegs:
        ok = extract_video(f, output_dir)
        extracted += ok
        skipped   += not ok

    return extracted, skipped


# ---------------------------------------------------------------------------
# Interactive output-folder prompt
# ---------------------------------------------------------------------------

def prompt_output_dir(source: Path) -> Path:
    """
    Ask the user where to save extracted videos.
    Default is a subfolder 'extracted_videos' beside the source.
    Entering '.' or 'same' uses the source folder itself (collisions handled).
    """
    source_dir = source if source.is_dir() else source.parent
    default    = source_dir / 'extracted_videos'

    print(f'\nWhere should extracted videos be saved?')
    print(f'  [Enter]  Create subfolder: {default}')
    print(f'  [same]   Same folder as source (files are renamed to avoid conflicts)')
    print(f'  [path]   Any custom path\n')

    raw = input('Output folder: ').strip()

    if raw == '' :
        return default
    if raw.lower() in ('same', '.'):
        return source_dir
    return Path(raw).expanduser().resolve()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='Extract embedded MP4 from Google Pixel motion photos.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('path',
                        help='JPEG file or folder containing photos')
    parser.add_argument('--output', '-o',
                        help='Output directory. Use "same" for source folder. '
                             'Omit to be prompted interactively.')
    parser.add_argument('--recursive', '-r', action='store_true',
                        help='Recurse into subdirectories')
    args = parser.parse_args()

    target = Path(args.path).expanduser().resolve()
    if not target.exists():
        print(f'[error] Path not found: {target}')
        sys.exit(1)

    # Determine output directory
    if args.output:
        source_dir = target if target.is_dir() else target.parent
        if args.output.lower() == 'same':
            output_dir = source_dir
        else:
            output_dir = Path(args.output).expanduser().resolve()
    else:
        output_dir = prompt_output_dir(target)

    output_dir.mkdir(parents=True, exist_ok=True)
    print(f'\n[output] {output_dir}\n')

    extracted, skipped = process(target, output_dir, args.recursive)

    motion_photos = extracted
    plain_jpegs   = skipped
    print(f'\n[done]  {motion_photos} video(s) extracted   '
          f'{plain_jpegs} non-motion JPEG(s) skipped')


if __name__ == '__main__':
    main()
