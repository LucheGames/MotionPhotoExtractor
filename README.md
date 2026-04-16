# Motion Photo Extractor

Extract the embedded MP4 video from Google Pixel motion photos.

**No dependencies. Pure Python.**

---

## What is a motion photo?

Pixel phones can shoot *motion photos* — a JPEG still with a short MP4 clip (typically 1–3 seconds) silently embedded at the end of the file. The file looks and behaves like a normal JPEG everywhere, but the video is in there.

This tool finds the video and saves it as a standalone MP4, leaving the original file untouched.

---

## Requirements

- Python 3.10 or later
- Nothing else — no pip installs, no ffmpeg

Download Python from [python.org](https://python.org) if you don't have it.

---

## Installation

```bash
git clone https://github.com/LucheGames/MotionPhotoExtractor.git
cd MotionPhotoExtractor
```

That's it.

---

## Usage

### Single file

```bash
python3 motion_extract.py photo.jpg
```

### Whole folder

```bash
python3 motion_extract.py /path/to/photos/
```

### Folder — recurse into subfolders

```bash
python3 motion_extract.py /path/to/photos/ --recursive
```

### Specify output folder upfront

```bash
python3 motion_extract.py /path/to/photos/ --output /path/to/videos/
```

### Save to same folder as source

```bash
python3 motion_extract.py /path/to/photos/ --output same
```

---

## Interactive mode

If you omit `--output`, the tool prompts you:

```
Where should extracted videos be saved?
  [Enter]  Create subfolder: /path/to/photos/extracted_videos
  [same]   Same folder as source (files renamed to avoid conflicts)
  [path]   Any custom path

Output folder:
```

- Press **Enter** to save into a new `extracted_videos` subfolder (recommended)
- Type **same** to save alongside the originals
- Type any path to save somewhere else

---

## Output filenames

Videos are saved as `<original_stem>_video.mp4`.

```
PXL_20251130_140645906.MP.jpg  →  PXL_20251130_140645906.MP_video.mp4
```

If the output file already exists (e.g. you run the tool twice), a counter is appended rather than overwriting:

```
PXL_20251130_140645906.MP_video.mp4
PXL_20251130_140645906.MP_video_2.mp4
PXL_20251130_140645906.MP_video_3.mp4
```

---

## What gets skipped

| File type | Behaviour |
|-----------|-----------|
| Plain JPEG (no motion data) | Skipped silently |
| MP4, PNG, HEIC, or any non-JPEG | Ignored entirely |
| Motion photo — video already extracted | Safe — output gets a `_2` suffix |

The tool only processes `.jpg` / `.jpeg` files. Everything else in the folder is left alone.

---

## Format support

The tool handles both Pixel motion photo formats automatically:

| Format | How it works |
|--------|-------------|
| **New** (Pixel 6+) | Reads `GCamera:MotionPhoto` + `Container:Directory` in XMP metadata |
| **Old** (earlier Pixels) | Reads `MicroVideoOffset` attribute in XMP metadata |
| **Fallback** | Scans backward through the file for MP4 box markers (`ftyp`/`moov`) |

You don't need to know which format your photos are — it's detected automatically.

---

## Example output

```
[→] 47 JPEG(s) found in /Users/dave/Photos

  [✓] PXL_20251130_140645906.MP.jpg  →  PXL_20251130_140645906.MP_video.mp4  (2.5 MB)
  [✓] PXL_20251203_184331147.MP.jpg  →  PXL_20251203_184331147.MP_video.mp4  (1.8 MB)
  [✓] PXL_20251204_173217796.MP.jpg  →  PXL_20251204_173217796.MP_video.mp4  (3.1 MB)

[done]  3 video(s) extracted   44 non-motion JPEG(s) skipped
```

---

## Notes

- The extracted MP4 is the **raw embedded clip** — no re-encoding, no quality loss
- The video is H.264 compressed (that's how the Pixel records it). "Uncompressed" would require ffmpeg to transcode and produce very large files
- `.trashed-` prefixed files (Android deleted photos transferred via USB) are processed normally — the `.trashed-` prefix doesn't affect extraction
- On Windows, use `python` instead of `python3` if that's how your installation is set up
