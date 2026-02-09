import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zlib
from dataclasses import dataclass
from typing import List, Tuple

import numpy as np
import pywt
from scipy.io import wavfile


# -----------------------------
# Utils
# -----------------------------
def run_ffmpeg(cmd: List[str]) -> None:
    """Run ffmpeg command; raise on failure."""
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, text=True)
    except FileNotFoundError:
        raise RuntimeError(
            "ffmpeg bulunamadı. Lütfen ffmpeg kurun ve PATH'e ekleyin.\n"
            "Windows (choco): choco install ffmpeg\n"
            "Ubuntu/Debian: sudo apt-get update && sudo apt-get install -y ffmpeg"
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ffmpeg hata verdi:\nCMD: {' '.join(cmd)}\nSTDERR:\n{e.stderr}") from e


def md5_file(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def bytes_to_bits(data: bytes) -> np.ndarray:
    arr = np.frombuffer(data, dtype=np.uint8)
    bits = np.unpackbits(arr)  # big-endian within each byte
    return bits.astype(np.uint8)


def bits_to_bytes(bits: np.ndarray) -> bytes:
    bits = bits.astype(np.uint8)
    # pad to multiple of 8
    if len(bits) % 8 != 0:
        pad = 8 - (len(bits) % 8)
        bits = np.concatenate([bits, np.zeros(pad, dtype=np.uint8)])
    arr = np.packbits(bits)
    return arr.tobytes()


def u32_to_bits(x: int) -> np.ndarray:
    return bytes_to_bits(int(x).to_bytes(4, "big", signed=False))


def bits_to_u32(bits: np.ndarray) -> int:
    b = bits_to_bytes(bits[:32])
    return int.from_bytes(b[:4], "big", signed=False)


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


# -----------------------------
# Watermark payload format
# -----------------------------
@dataclass
class WMConfig:
    wavelet: str = "db4"
    level: int = 2
    band: str = "d2"          # which detail band to embed in: d1 or d2 (for level>=2)
    stride: int = 8           # spacing between used coefficients
    repetition: int = 3       # repeat each bit r times (majority vote)
    strength: float = 0.6     # relative strength; higher = more robust, more audible risk


def build_payload(music_id: str, copyright_id: str, md5hex: str) -> bytes:
    obj = {
        "music_id": music_id,
        "copyright_id": copyright_id,
        "md5": md5hex,
        "v": 1,
    }
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    compressed = zlib.compress(raw, level=9)
    return compressed


def parse_payload(payload: bytes) -> dict:
    raw = zlib.decompress(payload)
    return json.loads(raw.decode("utf-8"))


# -----------------------------
# Core DWT-QIM watermarking
# -----------------------------
def dwt_split(x: np.ndarray, cfg: WMConfig):
    # pywt.wavedec returns [cA_n, cD_n, cD_{n-1}, ..., cD_1]
    coeffs = pywt.wavedec(x, cfg.wavelet, level=cfg.level)
    return coeffs


def choose_detail_band(coeffs, cfg: WMConfig) -> Tuple[int, np.ndarray]:
    """
    Returns index in coeffs list and the array reference.
    coeffs = [cA2, cD2, cD1] for level=2.
    """
    if cfg.level < 1:
        raise ValueError("level must be >= 1")
    # Mapping:
    # level=2 => indices: 0:cA2, 1:cD2, 2:cD1
    if cfg.band.lower() == "d1":
        idx = -1
    elif cfg.band.lower() == "d2":
        if cfg.level < 2:
            raise ValueError("band d2 requires level>=2")
        idx = 1
    else:
        raise ValueError("band must be 'd1' or 'd2'")
    return idx, coeffs[idx]


def compute_delta(detail: np.ndarray, cfg: WMConfig) -> float:
    # Robust but controlled: scale to std of chosen band
    sigma = float(np.std(detail)) + 1e-9
    # strength controls scaling; tweakable
    # keep delta not too tiny to survive re-encode, not too big to be audible
    delta = cfg.strength * (sigma * 0.02)
    # lower bound
    return max(delta, 1e-6)


def embed_bits_qim(detail: np.ndarray, bits: np.ndarray, cfg: WMConfig, delta: float) -> np.ndarray:
    """
    QIM embedding:
      c' = round(c/delta)*delta + (delta/4) if bit=0 else + (3*delta/4)
    Keep sign by operating on magnitude then restoring sign (helps stability).
    """
    out = detail.copy()
    usable = (len(out) // cfg.stride)
    if len(bits) > usable:
        raise ValueError(f"Kapasite yetersiz: {usable} bit sığar, istenen {len(bits)} bit.")

    for i, bit in enumerate(bits):
        k = i * cfg.stride
        c = out[k]
        s = 1.0 if c >= 0 else -1.0
        a = abs(c)
        q = np.round(a / delta) * delta
        offset = (delta / 4.0) if bit == 0 else (3.0 * delta / 4.0)
        out[k] = s * (q + offset)

    return out


def extract_bits_qim(detail: np.ndarray, nbits: int, cfg: WMConfig, delta: float) -> np.ndarray:
    bits = np.zeros(nbits, dtype=np.uint8)
    usable = (len(detail) // cfg.stride)
    if nbits > usable:
        raise ValueError(f"Kapasite yetersiz: {usable} bit okunabilir, istenen {nbits} bit.")

    for i in range(nbits):
        k = i * cfg.stride
        c = abs(float(detail[k]))
        # fraction within [0, delta)
        frac = (c % delta) / delta  # 0..1
        # decide closer to 0.25 or 0.75
        bit = 0 if abs(frac - 0.25) <= abs(frac - 0.75) else 1
        bits[i] = bit

    return bits


def repeat_encode(bits: np.ndarray, r: int) -> np.ndarray:
    if r <= 1:
        return bits
    return np.repeat(bits, r).astype(np.uint8)


def repeat_decode(bits: np.ndarray, r: int) -> np.ndarray:
    if r <= 1:
        return bits
    n = len(bits) // r
    bits = bits[: n * r].reshape(n, r)
    # majority vote
    return (np.sum(bits, axis=1) >= (r / 2)).astype(np.uint8)


# -----------------------------
# Audio IO helpers (mp3 <-> wav)
# -----------------------------
def mp3_to_wav_mono_44k(mp3_path: str, wav_path: str) -> None:
    # Force consistent decode: mono, 44100, 16-bit PCM
    cmd = [
        "ffmpeg", "-y",
        "-i", mp3_path,
        "-ac", "1",
        "-ar", "44100",
        "-c:a", "pcm_s16le",
        wav_path,
    ]
    run_ffmpeg(cmd)


def wav_to_mp3(wav_path: str, mp3_path: str, bitrate: str = "320k") -> None:
    cmd = [
        "ffmpeg", "-y",
        "-i", wav_path,
        "-c:a", "libmp3lame",
        "-b:a", bitrate,
        mp3_path,
    ]
    run_ffmpeg(cmd)


# -----------------------------
# Embed / Extract pipeline
# -----------------------------
def embed(mp3_in: str, mp3_out: str, music_id: str, copyright_id: str,
          md5_mode: str, cfg: WMConfig) -> None:

    if not os.path.exists(mp3_in):
        raise FileNotFoundError(f"Girdi bulunamadı: {mp3_in}")

    if md5_mode == "auto":
        md5hex = md5_file(mp3_in)
    else:
        md5hex = md5_mode

    payload = build_payload(music_id, copyright_id, md5hex)
    length_bits = u32_to_bits(len(payload))
    crc_bits = u32_to_bits(crc32(payload))
    data_bits = bytes_to_bits(payload)

    # Header + payload bits
    bits = np.concatenate([length_bits, crc_bits, data_bits]).astype(np.uint8)

    # Add repetition for robustness
    bits_rep = repeat_encode(bits, cfg.repetition)

    with tempfile.TemporaryDirectory() as td:
        wav_in = os.path.join(td, "in.wav")
        wav_out = os.path.join(td, "out.wav")

        mp3_to_wav_mono_44k(mp3_in, wav_in)
        sr, x = wavfile.read(wav_in)  # int16
        if x.dtype != np.int16:
            # normalize if needed
            x = x.astype(np.int16)

        # convert to float32 in [-1, 1]
        xf = (x.astype(np.float32) / 32768.0)

        coeffs = dwt_split(xf, cfg)
        idx, detail = choose_detail_band(coeffs, cfg)
        delta = compute_delta(detail, cfg)

        detail_marked = embed_bits_qim(detail, bits_rep, cfg, delta)
        coeffs[idx] = detail_marked

        yf = pywt.waverec(coeffs, cfg.wavelet)
        # match original length
        yf = yf[: len(xf)]
        # clip
        yf = np.clip(yf, -1.0, 1.0)

        y = (yf * 32767.0).astype(np.int16)
        wavfile.write(wav_out, sr, y)

        wav_to_mp3(wav_out, mp3_out, bitrate="320k")


def extract(mp3_in: str, cfg: WMConfig) -> dict:
    if not os.path.exists(mp3_in):
        raise FileNotFoundError(f"Girdi bulunamadı: {mp3_in}")

    with tempfile.TemporaryDirectory() as td:
        wav_in = os.path.join(td, "in.wav")
        mp3_to_wav_mono_44k(mp3_in, wav_in)

        sr, x = wavfile.read(wav_in)  # int16
        xf = (x.astype(np.float32) / 32768.0)

        coeffs = dwt_split(xf, cfg)
        idx, detail = choose_detail_band(coeffs, cfg)
        delta = compute_delta(detail, cfg)

        # First read enough bits for header (len + crc) with repetition
        header_bits_needed = (32 + 32) * cfg.repetition
        header_rep = extract_bits_qim(detail, header_bits_needed // cfg.repetition, cfg, delta)
        # Note: extract_bits_qim expects nbits in "symbol" units; we do it cleaner below:
        header_symbols = extract_bits_qim(detail, 64 * cfg.repetition, cfg, delta)
        header = repeat_decode(header_symbols, cfg.repetition)

        length = bits_to_u32(header[:32])
        expected_crc = bits_to_u32(header[32:64])

        payload_bits_len = length * 8
        total_symbols = (64 + payload_bits_len) * cfg.repetition

        symbols = extract_bits_qim(detail, total_symbols, cfg, delta)
        decoded = repeat_decode(symbols, cfg.repetition)

        decoded_header = decoded[:64]
        decoded_payload_bits = decoded[64: 64 + payload_bits_len]
        payload = bits_to_bytes(decoded_payload_bits)[:length]

        if crc32(payload) != expected_crc:
            raise RuntimeError(
                "CRC doğrulaması başarısız. Watermark bozulmuş olabilir veya strength/parametreler uyuşmuyor."
            )

        return parse_payload(payload)


# -----------------------------
# CLI
# -----------------------------
def make_parser():
    p = argparse.ArgumentParser(
        description="MP3 audio watermarking (DWT + QIM). Embed & Extract invisible IDs from audio content."
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("embed", help="Embed watermark into MP3")
    pe.add_argument("--in", dest="inp", default="music.mp3", help="Input MP3 (default: music.mp3)")
    pe.add_argument("--out", dest="out", default="watermarked.mp3", help="Output MP3 (default: watermarked.mp3)")
    pe.add_argument("--music-id", required=True, help="Music ID to embed")
    pe.add_argument("--copyright-id", required=True, help="Copyright/Telif ID to embed")
    pe.add_argument("--md5", default="auto", help="MD5 to embed (default: auto = md5 of input file)")
    pe.add_argument("--strength", type=float, default=0.6, help="Embedding strength (default: 0.6)")
    pe.add_argument("--stride", type=int, default=8, help="Coefficient stride (default: 8)")
    pe.add_argument("--repetition", type=int, default=3, help="Bit repetition factor (default: 3)")
    pe.add_argument("--band", default="d2", choices=["d1", "d2"], help="DWT detail band (default: d2)")
    pe.add_argument("--wavelet", default="db4", help="Wavelet (default: db4)")
    pe.add_argument("--level", type=int, default=2, help="DWT level (default: 2)")

    px = sub.add_parser("extract", help="Extract watermark from MP3")
    px.add_argument("--in", dest="inp", default="music.mp3", help="Input MP3 (default: music.mp3)")
    px.add_argument("--strength", type=float, default=0.6, help="Must match embed strength (default: 0.6)")
    px.add_argument("--stride", type=int, default=8, help="Must match embed stride (default: 8)")
    px.add_argument("--repetition", type=int, default=3, help="Must match embed repetition (default: 3)")
    px.add_argument("--band", default="d2", choices=["d1", "d2"], help="Must match embed band (default: d2)")
    px.add_argument("--wavelet", default="db4", help="Must match embed wavelet (default: db4)")
    px.add_argument("--level", type=int, default=2, help="Must match embed level (default: 2)")

    return p


def main():
    parser = make_parser()
    args = parser.parse_args()

    cfg = WMConfig(
        wavelet=args.wavelet,
        level=args.level,
        band=args.band,
        stride=args.stride,
        repetition=args.repetition,
        strength=args.strength,
    )

    try:
        if args.cmd == "embed":
            embed(
                mp3_in=args.inp,
                mp3_out=args.out,
                music_id=args.music_id,
                copyright_id=args.copyright_id,
                md5_mode=args.md5,
                cfg=cfg,
            )
            print(json.dumps({
                "status": "ok",
                "output": args.out,
                "embedded": {
                    "music_id": args.music_id,
                    "copyright_id": args.copyright_id,
                    "md5": md5_file(args.inp) if args.md5 == "auto" else args.md5
                },
                "params": {
                    "wavelet": cfg.wavelet, "level": cfg.level, "band": cfg.band,
                    "stride": cfg.stride, "repetition": cfg.repetition, "strength": cfg.strength
                }
            }, ensure_ascii=False, indent=2))

        elif args.cmd == "extract":
            data = extract(args.inp, cfg=cfg)
            print(json.dumps({"status": "ok", "extracted": data}, ensure_ascii=False, indent=2))

    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
