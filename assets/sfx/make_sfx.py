"""Generates the meme sound effects used by Zbir Cliping (100% synthesized here -> no copyright, royalty-free).
boom.wav = deep 'vine boom'-style hit, whoosh.wav = swoosh for zoom punch-ins, pop.wav = short pop for emojis."""
import wave
import numpy as np

SR = 48000


def save(name, x):
    x = x / (np.abs(x).max() + 1e-9) * 0.9
    pcm = (np.stack([x, x], 1) * 32767).astype(np.int16)
    with wave.open(name, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())


def boom():
    t = np.arange(int(SR * 1.3)) / SR
    f = 38 + 70 * np.exp(-t * 18)                       # pitch drop 108 Hz -> 38 Hz
    ph = 2 * np.pi * np.cumsum(f) / SR
    body = np.sin(ph) * np.exp(-t * 2.6)
    sub = 0.5 * np.sin(ph * 0.5) * np.exp(-t * 2.0)
    click = np.random.RandomState(1).randn(len(t)) * np.exp(-t * 160) * 0.4
    x = np.tanh(2.2 * (body + sub + click))            # warm saturation
    rev = np.convolve(x, np.exp(-np.arange(int(SR * 0.25)) / (SR * 0.06)) * 0.0015, mode="full")[: len(x)]
    return x + rev


def whoosh():
    n = int(SR * 0.45); t = np.arange(n) / SR
    noise = np.random.RandomState(2).randn(n)
    spec = np.fft.rfft(noise); fr = np.fft.rfftfreq(n, 1 / SR)
    out = np.zeros(n)
    for i in range(0, n, 1024):                         # band-pass sweeping 300 Hz -> 4 kHz
        c = 300 + 3700 * (i / n) ** 1.5
        band = np.exp(-((fr - c) / (c * 0.6)) ** 2)
        seg = np.fft.irfft(spec * band, n)[i:i + 1024]
        out[i:i + len(seg)] = seg
    env = np.sin(np.pi * np.clip(t / t[-1], 0, 1)) ** 1.5
    return out * env


def pop():
    t = np.arange(int(SR * 0.12)) / SR
    f = 900 * np.exp(-t * 30) + 300
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 45)


if __name__ == "__main__":
    save("boom.wav", boom()); save("whoosh.wav", whoosh()); save("pop.wav", pop())
