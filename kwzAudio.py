# =========================
# kwzAudio.py (mix version)
# =========================
#
# Exports KWZ audio and converts it to WAV
# - track id: 0(BGM) / 1-4(SE)
# - track id: "mix" to mix all available tracks
#
# Usage:
#   python kwzAudio.py <input.kwz> <track id|mix> <output.wav>

from sys import argv
from kwz import KWZParser
import wave
import numpy as np

WAV_RATE = 16364  # Flipnote Studio 3D rate (as used in original script)

def write_wav(path, pcm_i16, rate=WAV_RATE):
    with wave.open(path, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)  # int16
        audio.setframerate(rate)
        audio.writeframes(pcm_i16.tobytes())

def mix_tracks(parser: KWZParser):
    tracks = []
    max_len = 0

    for i in range(5):  # 0..4
        try:
            if hasattr(parser, "has_audio_track") and not parser.has_audio_track(i):
                continue
            t = parser.get_audio_track(i)
            if t is None or len(t) == 0:
                continue
            # 念のため int16 化
            t = np.asarray(t, dtype=np.int16)
            tracks.append(t)
            if len(t) > max_len:
                max_len = len(t)
        except Exception:
            # どれかのトラックが壊れてても他を使う
            continue

    if not tracks:
        return np.zeros(1, dtype=np.int16)

    # int32で足してクリップ防止
    mix = np.zeros(max_len, dtype=np.int32)
    for t in tracks:
        mix[:len(t)] += t.astype(np.int32)

    # クリップ防止：まず int16 範囲でクリップ
    mix = np.clip(mix, -32768, 32767).astype(np.int16)

    # さらに軽く音割れしにくくするために、ピークが大きい時だけ少し下げる
    peak = int(np.max(np.abs(mix)))
    if peak > 30000:
        gain = 30000 / peak
        mix = (mix.astype(np.float32) * gain).astype(np.int16)

    return mix

if len(argv) != 4:
    print("\nUsage: python kwzAudio.py <input.kwz> <track id|mix> <output.wav>\n")
    raise SystemExit(1)

infile = argv[1]
track_arg = argv[2].lower()
outfile = argv[3]

with open(infile, "rb") as kwz:
    parser = KWZParser(kwz)

    if track_arg == "mix":
        pcm = mix_tracks(parser)
        write_wav(outfile, pcm)
        print("\nFinished conversion (mix)!\n")
    else:
        track_index = int(track_arg)
        pcm = np.asarray(parser.get_audio_track(track_index), dtype=np.int16)
        if pcm.size == 0:
            pcm = np.zeros(1, dtype=np.int16)
        write_wav(outfile, pcm)
        print("\nFinished conversion!\n")
