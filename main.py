#!/usr/bin/env python3
from __future__ import annotations
"""
Discord ボイスチェンジャー for Mac
BlackHole 仮想オーディオドライバを使ったリアルタイム音声変換

操作方法:
  1 : ノーマルモード（変換なし）
  2 : 高い声モード  (+6 半音)
  3 : 低い声モード  (-6 半音)
  4 : 異性の声モード
  q : 終了
"""

import sys
import time
import threading

import sounddevice as sd
import numpy as np

# ===== 設定 =================================================================

SAMPLE_RATE = 22050  # 44100 から下げて計算負荷と遅延を軽減
BLOCK_SIZE  = 1024   # 512 だと librosa で警告が出るため 1024 に戻して安定させる
CHANNELS    = 1
VOLUME_GAIN = 4.0    # さらに音量を 4.0倍 にブースト

# ===== モード定義 ===========================================================

MODE_NORMAL   = 0
MODE_HIGH     = 1
MODE_LOW      = 2
MODE_OPPOSITE = 3
MODE_CUSTOM   = 4

# 各モードの半音シフト量（MODE_OPPOSITE は起動時に性別設定で書き換える）
SEMITONE_MAP: dict[int, float] = {
    MODE_NORMAL:   0,
    MODE_HIGH:     6,
    MODE_LOW:     -6,
    MODE_OPPOSITE: 10,   # デフォルト: 男→女 (+10 半音)
    MODE_CUSTOM:   0.0,  # ユーザー入力
}

MODE_NAMES: dict[int, str] = {
    MODE_NORMAL:   "ノーマル（変換なし）",
    MODE_HIGH:     "高い声 (+6 半音)",
    MODE_LOW:      "低い声 (-6 半音)",
    MODE_OPPOSITE: "異性の声",   # 起動時に更新
    MODE_CUSTOM:   "カスタム設定",
}

current_mode: int = MODE_NORMAL
running: bool = True

# ===== ピッチシフトエンジン =================================================

# 優先順位: pyrubberband > librosa > scipy
# 品質は pyrubberband が最も高く、scipy が最も低い

try:
    import pyrubberband as pyrb   # type: ignore
    HAS_PYRUBBERBAND = True
except ImportError:
    HAS_PYRUBBERBAND = False

try:
    import librosa                # type: ignore
    HAS_LIBROSA = True
except ImportError:
    HAS_LIBROSA = False


def _pitch_shift_scipy(audio: np.ndarray, semitones: float) -> np.ndarray:
    """
    scipy のリサンプリングを使ったシンプルなピッチシフト（フォールバック用）。
    連続ブロック間の境界でアーティファクトが出る場合があります。
    """
    from scipy import signal as sg

    factor   = 2.0 ** (semitones / 12.0)
    n        = len(audio)
    new_len  = max(1, int(round(n / factor)))

    resampled = sg.resample(audio.astype(np.float64), new_len).astype(np.float32)

    if new_len >= n:
        return resampled[:n]
    else:
        # ピッチアップの場合はサンプルが不足するので繰り返しで補完
        repeats = int(np.ceil(n / new_len)) + 1
        return np.tile(resampled, repeats)[:n]


def _pitch_shift_librosa(audio: np.ndarray, semitones: float) -> np.ndarray:
    """librosa の位相ボコーダを使ったピッチシフト（高品質）。"""
    return librosa.effects.pitch_shift(
        audio.astype(np.float32),
        sr=SAMPLE_RATE,
        n_steps=semitones,
        bins_per_octave=24,
        n_fft=BLOCK_SIZE  # ブロックサイズに合わせて調整
    )


def _pitch_shift_rubberband(audio: np.ndarray, semitones: float) -> np.ndarray:
    """pyrubberband を使ったピッチシフト（最高品質）。"""
    return pyrb.pitch_shift(audio, SAMPLE_RATE, semitones).astype(np.float32)


def pitch_shift(audio: np.ndarray, semitones: float) -> np.ndarray:
    """利用可能な最良のエンジンでピッチシフトを実行する。"""
    if semitones == 0:
        return audio
    if HAS_PYRUBBERBAND:
        return _pitch_shift_rubberband(audio, semitones)
    if HAS_LIBROSA:
        return _pitch_shift_librosa(audio, semitones)
    return _pitch_shift_scipy(audio, semitones)


# ===== オーディオコールバック ===============================================

def audio_callback(
    indata: np.ndarray,
    outdata: np.ndarray,
    frames: int,
    time_info: object,
    status: sd.CallbackFlags,
) -> None:
    """sounddevice のリアルタイムストリームコールバック。"""
    if status:
        print(f"\nAudio status: {status}", file=sys.stderr)

    audio    = indata[:, 0].copy()
    semitones = SEMITONE_MAP[current_mode]

    if semitones != 0:
        try:
            processed = pitch_shift(audio, semitones)
        except Exception as exc:
            print(f"\n[pitch_shift error] {exc}", file=sys.stderr)
            processed = audio
    else:
        processed = audio

    # 音量を大幅に増幅
    processed = processed * VOLUME_GAIN

    # ソフトリミッター（音割れをマイルドにする）
    processed = np.tanh(processed)

    outdata[:] = 0.0
    copy_len = min(len(processed), frames)
    outdata[:copy_len, 0] = processed[:copy_len]


# ===== キーボード監視 =======================================================

def start_keyboard_listener() -> object | None:
    """pynput でキーボード入力を監視し、モード切り替えを行う。"""
    try:
        from pynput import keyboard  # type: ignore
    except ImportError:
        print("⚠  pynput がインストールされていません。キーボード制御は無効です。")
        print("   pip install pynput でインストールしてください。\n")
        return None

    def on_press(key: object) -> None:
        global current_mode
        try:
            ch = key.char  # type: ignore[union-attr]
            new_mode = None
            if   ch == "1": new_mode = MODE_NORMAL
            elif ch == "2": new_mode = MODE_HIGH
            elif ch == "3": new_mode = MODE_LOW
            elif ch == "4": new_mode = MODE_OPPOSITE
            elif ch == "5":
                print("\n\n新しいピッチ（半音）を入力してください（例: 12.0）: ", end="", flush=True)
                try:
                    # 入力待ちでブロックしないように注意が必要だが、pynput のリスナー内での input は避けた方がよい
                    # 今回は簡易的に 12 半音にセットするコマンドにするか、別の方法を検討
                    # → 簡易化のため「5」は 12 半音（1オクターブ）固定にするか、
                    # あるいは起動時に聞いておく。ここでは「1オクターブ上（固定）」とする。
                    SEMITONE_MAP[MODE_CUSTOM] = 12.0
                    MODE_NAMES[MODE_CUSTOM] = "カスタム (1オクターブ上)"
                    new_mode = MODE_CUSTOM
                except Exception:
                    pass
            elif ch == "q":
                print("\n\n終了要求を受け取りました。")
                global running
                running = False
                return False  # リスナーを停止

            if new_mode is not None:
                current_mode = new_mode
                # 変更を即座に表示するためにフラグを立てるか、ここでプリント
                print(f"\n  [Key] モード切替 ➡ \033[1;32m{MODE_NAMES[current_mode]}\033[0m")
        except AttributeError:
            pass  # 特殊キーは無視

    listener = keyboard.Listener(on_press=on_press, suppress=False)
    listener.daemon = True
    listener.start()
    return listener


# ===== デバイス選択ヘルパー =================================================

def list_devices() -> None:
    """利用可能なオーディオデバイスを一覧表示する。"""
    print("\n=== 利用可能なオーディオデバイス ===")
    devices = sd.query_devices()
    default_in, default_out = sd.default.device  # type: ignore[misc]

    for i, dev in enumerate(devices):
        in_ch  = dev["max_input_channels"]
        out_ch = dev["max_output_channels"]
        tags: list[str] = []
        if "BlackHole" in dev["name"]:
            tags.append("★ BlackHole")
        if i == default_in:
            tags.append("デフォルト入力")
        if i == default_out:
            tags.append("デフォルト出力")
        tag_str = f"  [{', '.join(tags)}]" if tags else ""
        print(f"  [{i:2d}] {dev['name']}{tag_str}")
        print(f"        入力 {in_ch}ch  出力 {out_ch}ch")
    print()


def find_blackhole() -> tuple[int | None, str | None]:
    """BlackHole 出力デバイスを自動検出する。"""
    for i, dev in enumerate(sd.query_devices()):
        if "BlackHole" in dev["name"] and dev["max_output_channels"] > 0:
            return i, dev["name"]
    return None, None


def ask_device(prompt: str, default_idx: int | None) -> int | None:
    """デバイス番号を入力させる（Enter でデフォルト）。"""
    hint = f"デフォルト [{default_idx}]" if default_idx is not None else "デフォルト"
    print(f"{prompt} (Enter で{hint}): ", end="")
    val = input().strip()
    if not val:
        return None
    try:
        return int(val)
    except ValueError:
        print("無効な入力のためデフォルトを使用します。")
        return None


# ===== メイン ===============================================================

def main() -> None:
    global current_mode, running, SEMITONE_MAP

    print("=" * 58)
    print("  Discord ボイスチェンジャー for Mac  (BlackHole 対応)")
    print("=" * 58)

    # エンジン表示
    if HAS_PYRUBBERBAND:
        engine_name = "pyrubberband（最高品質）"
    elif HAS_LIBROSA:
        engine_name = "librosa（高品質）"
    else:
        engine_name = "scipy（標準品質）"
    print(f"ピッチシフトエンジン: {engine_name}")
    if not HAS_LIBROSA and not HAS_PYRUBBERBAND:
        print("  高品質化: pip install librosa")
    print()

    # デバイス一覧表示
    list_devices()

    # 入力デバイス（マイク）
    default_in = sd.default.device[0]  # type: ignore[index]
    input_device = ask_device("マイクのデバイス番号を入力", default_in)

    # 出力デバイス（BlackHole）
    bh_idx, bh_name = find_blackhole()
    if bh_idx is not None:
        print(f"\nBlackHole を自動検出: [{bh_idx}] {bh_name}")
        output_device = bh_idx
        print(f"自動的に BlackHole [{bh_idx}] に出力します。")
    else:
        print("\n⚠  BlackHole が見つかりません。")
        default_out = sd.default.device[1]  # type: ignore[index]
        output_device = ask_device("出力デバイス番号を入力", default_out)

    # 異性の声モード — 性別設定
    print("\n=== 異性の声モード 設定 ===")
    print("あなたの声の性別を選択してください:")
    print("  1: 男性 → 女性に変換 (+10 半音)")
    print("  2: 女性 → 男性に変換 (-10 半音)")
    print("選択 [1/2] (Enter で 1): ", end="")
    gender = input().strip()
    if gender == "2":
        SEMITONE_MAP[MODE_OPPOSITE] = -10
        MODE_NAMES[MODE_OPPOSITE]   = "異性の声: 女→男 (-10 半音)"
    else:
        SEMITONE_MAP[MODE_OPPOSITE] = 10
        MODE_NAMES[MODE_OPPOSITE]   = "異性の声: 男→女 (+10 半音)"

    # 操作説明
    print("\n" + "=" * 30)
    print("      操作方法 (Enter不要)")
    print("=" * 30)
    for idx in range(4):
        print(f"  [{idx + 1}] {MODE_NAMES[idx]}")
    print(f"  [5] {MODE_NAMES[MODE_CUSTOM]}")
    print("  [q] 終了")
    print("-" * 30)
    print()

    # キーボードリスナー起動
    start_keyboard_listener()

    # ストリーム開始
    print("音声変換を開始します...")
    print(f"現在のモード: {MODE_NAMES[MODE_NORMAL]}", end="", flush=True)

    try:
        with sd.Stream(
            samplerate=SAMPLE_RATE,
            blocksize=BLOCK_SIZE,
            dtype="float32",
            channels=CHANNELS,
            device=(input_device, output_device),
            callback=audio_callback,
        ):
            print("\n" + "★" * 40)
            print("  [操作方法]")
            print("  1〜5 のキー を押して Enter を入力してください。")
            print("  （アクセシビリティ許可済みの場合は、押すだけでOK！）")
            print("  q + Enter で終了します。")
            print("★" * 40 + "\n")

            last_mode = -1
            while running:
                if current_mode != last_mode:
                    print(f"\n  ▶ 現在のモード: \033[1;32m{MODE_NAMES[current_mode]}\033[0m")
                    print("  [変更: 1-5, 終了: q] ➡ ", end="", flush=True)
                    last_mode = current_mode
                
                # input() で入力を待つ
                try:
                    user_input = input().strip().lower()
                    if user_input == "q":
                        running = False
                        break
                    if user_input in ["1", "2", "3", "4"]:
                        current_mode = int(user_input) - 1
                    elif user_input == "5":
                        print("新しいピッチ（半音）を入力してください (例: 12.0): ", end="", flush=True)
                        try:
                            val = float(input().strip())
                            SEMITONE_MAP[MODE_CUSTOM] = val
                            MODE_NAMES[MODE_CUSTOM] = f"カスタム ({val:+.1f} 半音)"
                            current_mode = MODE_CUSTOM
                        except ValueError:
                            print("無効な数値です。")
                except (EOFError, KeyboardInterrupt):
                    running = False
                    break

        print("\nストリームを停止しました。")

    except KeyboardInterrupt:
        print("\n\n終了しました。")
    except sd.PortAudioError as exc:
        print(f"\nオーディオエラー: {exc}")
        print("デバイス設定を確認してください（README.md 参照）。")
        sys.exit(1)
    except Exception as exc:
        print(f"\nエラー: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
