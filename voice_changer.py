"""
音声変換モジュール (Discord ボット向け)

ピッチシフトエンジン優先順位:
  1. pyrubberband  — 最高品質 (brew install rubberband && pip install pyrubberband)
  2. librosa       — 高品質   (pip install librosa)
  3. scipy         — 標準品質 (フォールバック)
"""

import numpy as np

# ---------- Discord 音声フォーマット定数 ----------------------------------------
# 48 kHz, 16-bit signed PCM, stereo, 20 ms フレーム
DISCORD_SAMPLE_RATE  = 48_000
DISCORD_CHANNELS     = 2
DISCORD_FRAME_MS     = 20
DISCORD_FRAME_SAMPLES = DISCORD_SAMPLE_RATE * DISCORD_FRAME_MS // 1000  # 960
DISCORD_FRAME_BYTES   = DISCORD_FRAME_SAMPLES * DISCORD_CHANNELS * 2    # 3840

# ---------- エンジン自動選択 ----------------------------------------------------
try:
    import pyrubberband as _pyrb   # type: ignore
    _ENGINE = "pyrubberband"
except ImportError:
    _pyrb = None
    try:
        import librosa as _librosa  # type: ignore
        _ENGINE = "librosa"
    except ImportError:
        _librosa = None
        _ENGINE = "scipy"

print(f"[VoiceChanger] ピッチシフトエンジン: {_ENGINE}")


def _pitch_shift(mono: np.ndarray, semitones: float) -> np.ndarray:
    """モノラル float32 配列にピッチシフトを適用する。"""
    if _ENGINE == "pyrubberband":
        return _pyrb.pitch_shift(mono, DISCORD_SAMPLE_RATE, semitones).astype(np.float32)

    if _ENGINE == "librosa":
        return _librosa.effects.pitch_shift(
            mono.astype(np.float32),
            sr=DISCORD_SAMPLE_RATE,
            n_steps=semitones,
        )

    # ---- scipy フォールバック ----
    from scipy import signal as _sg

    factor  = 2.0 ** (semitones / 12.0)
    n       = len(mono)
    new_len = max(1, int(round(n / factor)))

    resampled = _sg.resample(mono.astype(np.float64), new_len).astype(np.float32)
    if new_len >= n:
        return resampled[:n]
    # 短い場合は繰り返してパディング
    repeats = int(np.ceil(n / new_len)) + 1
    return np.tile(resampled, repeats)[:n]


# ---------- VoiceChanger --------------------------------------------------------

class VoiceChanger:
    """音声変換設定を管理し、PCM バイト列にピッチシフトを適用するクラス。"""

    # 固定モードと半音シフト量
    _FIXED: dict[str, float] = {
        "normal":  0,
        "high":    6,   # +6 半音（高い声）
        "low":    -6,   # -6 半音（低い声）
    }

    _DESC: dict[str, str] = {
        "normal": "ノーマル（変換なし）",
        "high":   "高い声 (+6 半音)",
        "low":    "低い声 (-6 半音)",
    }

    def __init__(self) -> None:
        self.mode: str   = "normal"
        self._gender_st: float = 10.0  # +10: 男→女 / -10: 女→男
        self._custom_st: float = 0.0

    # ---- プロパティ ----

    @property
    def semitones(self) -> float:
        if self.mode == "gender":
            return self._gender_st
        if self.mode == "custom":
            return self._custom_st
        return self._FIXED.get(self.mode, 0.0)

    @property
    def description(self) -> str:
        if self.mode == "gender":
            label = "男→女 (+10 半音)" if self._gender_st > 0 else "女→男 (-10 半音)"
            return f"異性の声 ({label})"
        if self.mode == "custom":
            return f"カスタム ({self._custom_st:+.1f} 半音)"
        return self._DESC.get(self.mode, self.mode)

    # ---- モード設定 ----

    def set_normal(self)  -> None: self.mode = "normal"
    def set_high(self)    -> None: self.mode = "high"
    def set_low(self)     -> None: self.mode = "low"

    def set_gender(self, male_to_female: bool = True) -> None:
        self._gender_st = 10.0 if male_to_female else -10.0
        self.mode = "gender"

    # ---- 音声処理 ----

    def process(self, pcm_bytes: bytes) -> bytes:
        """
        Discord PCM バイト列にピッチシフトを適用して返す。
        入出力フォーマット: 48 kHz, 16-bit signed PCM, stereo interleaved
        """
        st = self.semitones
        if st == 0.0:
            return pcm_bytes

        # int16 → float32 ([-1.0, 1.0])
        arr  = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        # stereo interleaved → mono (L + R を平均)
        mono = (arr[0::2] + arr[1::2]) * 0.5

        # ピッチシフト
        shifted = _pitch_shift(mono, st)

        # float32 → int16 (クリップ)、mono → stereo (L=R)
        out_i16 = np.clip(shifted * 32768.0, -32768, 32767).astype(np.int16)
        stereo  = np.repeat(out_i16, 2)   # [s0, s0, s1, s1, ...] (L/R 同一)

        return stereo.tobytes()
