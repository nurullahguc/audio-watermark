```markdown
# Audio Watermarking (MP3) — DWT + QIM (Python)

This project embeds an **invisible, robust watermark** inside an MP3’s **audio content** (not filename or ID3 tags).  
Even if the file name changes or metadata is removed, the watermark can still be extracted from the audio.

It works by decoding MP3 → PCM (WAV), embedding the watermark in the **DWT (Discrete Wavelet Transform) domain** using **QIM (Quantization Index Modulation)**, then encoding back to MP3.

---

## Requirements

- **Python 3.9+**
- **FFmpeg** (must be accessible from terminal)
- Python packages: `numpy`, `scipy`, `pywavelets`

---

## Project Structure

Place your input MP3 in the same folder:
```

audio-watermark/
├─ app.py
└─ music.mp3

````

---

## Install Dependencies

### 1) Install Python packages
```bash
pip install numpy scipy pywavelets
````

### 2) Verify FFmpeg

```bash
ffmpeg -version
```

If you see FFmpeg version info, you're good to go.

---

## Usage

### Embed Watermark (Create `watermarked.mp3`)

This embeds `music_id` and `copyright_id` invisibly into the audio:

```bash
python app.py embed --in music.mp3 --out watermarked.mp3 --music-id "MUSIC-001" --copyright-id "TELIF-2026" --strength 1.8 --repetition 9 --stride 5 --band d1
```

Expected result:

- A new file named `watermarked.mp3` is created.
- Output JSON shows `"status": "ok"`.

---

### Extract Watermark (Read embedded data)

Use the **same parameters** you used for embedding:

```bash
python app.py extract --in watermarked.mp3 --strength 1.8 --repetition 9 --stride 5 --band d1
```

Expected output:

```json
{
  "status": "ok",
  "extracted": {
    "music_id": "MUSIC-001",
    "copyright_id": "TELIF-2026",
    "md5": "...",
    "v": 1
  }
}
```

---

## Parameter Notes (Important)

When extracting, these must match the embed parameters:

- `--strength`
- `--repetition`
- `--stride`
- `--band` (`d1` or `d2`)
- (if changed) `--wavelet`, `--level`

Recommended robust settings that worked reliably:

- `--strength 1.8`
- `--repetition 9`
- `--stride 5`
- `--band d1`

---

## Common Issues

### CRC verification failed

If you see an error like:

```
CRC verification failed
```

It usually means the watermark could not be reliably recovered (often due to MP3 re-encoding effects or mismatched parameters).

Fix:

- Ensure extract uses the **same parameters** as embed
- Increase robustness:
  - increase `--repetition` (e.g., 9 → 11)
  - slightly increase `--strength` (e.g., 1.8 → 2.0)
  - reduce `--stride` (e.g., 5 → 4)

---

## License / Notes

This is a practical watermarking prototype for identification and tracking.
For production-level deployments, consider adding:

- Payload encryption (AES)
- Error correction codes (BCH/Reed-Solomon)
- Spread-spectrum embedding for even stronger robustness

```

```
