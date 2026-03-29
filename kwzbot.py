import os
import sys
import uuid
import asyncio
import tempfile
import subprocess
import random
from pathlib import Path

import discord
from discord.ext import commands

# ====== 設定（ここだけ自分の環境に合わせる） ======
BASE_DIR = Path(__file__).parent

KWZVIDEO = BASE_DIR / "kwzVideo.py"
KWZAUDIO = BASE_DIR / "kwzAudio.py"

# Render / Linux 用
FFMPEG = Path("/usr/bin/ffmpeg")


MAX_KWZ_MB = 40           # 入力サイズ上限（適宜）
CONVERT_TIMEOUT = 180     # 変換タイムアウト秒（適宜）

# 同時変換数（PCがキツいなら 1 推奨）
MAX_CONCURRENT_CONVERSIONS = 1
# ===============================================

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True  # Developer Portal側でもON必要

# !random と -random の両方を使えるようにする
bot = commands.Bot(command_prefix=["!", "-"], intents=intents)

# 変換の同時実行を制限（これで「2回目で詰まる」が激減）
_convert_sem = asyncio.Semaphore(MAX_CONCURRENT_CONVERSIONS)


def run_cmd(cmd, cwd=None, timeout=None):
    """
    Windows: timeout時にプロセスツリー(子ffmpeg含む)を確実に終了させる
    """
    # CREATE_NEW_PROCESS_GROUP = 0x00000200
    creationflags = 0x00000200 if os.name == "nt" else 0

    p = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=creationflags,
    )
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # 子プロセス(ffmpeg)ごと落とす
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(p.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            p.kill()
        raise

    if p.returncode != 0:
        e = subprocess.CalledProcessError(p.returncode, cmd, output=out, stderr=err)
        raise e

    # subprocess.run互換の戻り値っぽくしたいなら必要に応じて
    class R:
        pass

    r = R()
    r.stdout = out
    r.stderr = err
    r.returncode = p.returncode
    return r


def kwz_to_mp4_silent(input_kwz: Path, silent_mp4: Path):
    # 実行中のPythonで呼ぶ（環境ズレ対策）
    cmd = [sys.executable, str(KWZVIDEO), str(input_kwz), str(silent_mp4)]
    run_cmd(cmd, cwd=BASE_DIR, timeout=CONVERT_TIMEOUT)


def kwz_to_wav_track0(input_kwz: Path, wav: Path) -> bool:
    cmd = [sys.executable, str(KWZAUDIO), str(input_kwz), "0", str(wav)]
    try:
        run_cmd(cmd, cwd=BASE_DIR, timeout=CONVERT_TIMEOUT)
        return wav.exists() and wav.stat().st_size > 44  # wavヘッダ以上
    except subprocess.CalledProcessError:
        return False


def mux_mp4_with_audio(silent_mp4: Path, wav: Path, out_mp4: Path):
    # 48kに整えてAAC化して合体（軽くリミッタ入れて割れにくく）
    cmd = [
        str(FFMPEG),
        "-y",
        "-i", str(silent_mp4),
        "-i", str(wav),
        "-c:v", "copy",
        "-af", "aresample=48000,alimiter=limit=0.95:level=disabled",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(out_mp4),
    ]
    run_cmd(cmd, cwd=silent_mp4.parent, timeout=CONVERT_TIMEOUT)


@bot.command()
async def ping(ctx):
    await ctx.reply("pong")


@bot.command(name="random")
async def random_cmd(ctx):
    n = random.randint(1, 830)
    url = f"https://aiueo9999.pythonanywhere.com/detail/Q{n}"
    await ctx.reply(url)


@bot.event
async def on_ready():
    # 起動確認
    print(f"Logged in as: {bot.user} (id={bot.user.id})")


async def handle_one_attachment(message: discord.Message, att: discord.Attachment):
    size_mb = att.size / (1024 * 1024)
    if size_mb > MAX_KWZ_MB:
        await message.reply(f"このkwzは {size_mb:.1f}MB で大きすぎるので変換できないよ（上限 {MAX_KWZ_MB}MB）")
        return

    # 進捗メッセージ（編集して更新する方式にすると見やすい）
    status_msg = await message.reply(f"🎞️ kwzを察知！出力シチャウヨン：`{att.filename}`（ファイル名は無視して処理するよ）")

    # 同時変換制限（ここが“詰まらない”の肝）
    async with _convert_sem:
        with tempfile.TemporaryDirectory(prefix="kwzbot_") as td_str:
            td = Path(td_str)

            uid = uuid.uuid4().hex
            in_kwz      = td / f"input_{uid}.kwz"
            silent_mp4  = td / f"silent_{uid}.mp4"
            bgm_wav     = td / f"bgm_{uid}.wav"
            out_mp4     = td / f"out_{uid}.mp4"

            try:
                # ダウンロード
                await att.save(in_kwz)
                await status_msg.edit(content="📥 ダウンロード完了 → 変換中…")

                # ① 映像（無音mp4）
                await asyncio.to_thread(kwz_to_mp4_silent, in_kwz, silent_mp4)

                # ② 音声（BGM）
                has_bgm = await asyncio.to_thread(kwz_to_wav_track0, in_kwz, bgm_wav)

                # ③ 合体（BGMが無ければ無音のまま返す）
                if has_bgm:
                    await asyncio.to_thread(mux_mp4_with_audio, silent_mp4, bgm_wav, out_mp4)
                    result = out_mp4
                else:
                    result = silent_mp4

                await status_msg.edit(content="📤 Discordへアップロード中…")

                # 送信
                await message.reply(file=discord.File(result, filename="converted.mp4"))
                await message.reply("出力終わりました！↑いいさくひんだねまじで")

                # ステータス更新
                await status_msg.edit(content="✅ 変換完了！")

            except subprocess.TimeoutExpired:
                await status_msg.edit(content="⏱️ 変換がタイムアウトした…（重い作品かも）")
            except subprocess.CalledProcessError as e:
                err = (e.stderr or e.stdout or "unknown error")[:1800]
                await status_msg.edit(content=f"❌ 変換失敗：\n```{err}```")
            except discord.HTTPException as e:
                await status_msg.edit(content=f"❌ Discord送信に失敗（サイズ超え等）: `{e}`")
            except Exception as e:
                await status_msg.edit(content=f"❌ 予期しないエラー：{type(e).__name__}: {e}")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    kwz_attachments = [a for a in message.attachments if a.filename.lower().endswith(".kwz")]
    if kwz_attachments:
        # 複数来ても「詰まらない」ようにタスクとして投げる（on_messageを即返す）
        for att in kwz_attachments:
            asyncio.create_task(handle_one_attachment(message, att))

    # コマンドを生かす
    await bot.process_commands(message)


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN が環境変数に設定されてないよ（例: setx DISCORD_TOKEN \"...\"）")
    if not FFMPEG.exists():
        raise SystemExit(f"ffmpeg が見つからない: {FFMPEG}")
    if not KWZVIDEO.exists() or not KWZAUDIO.exists():
        raise SystemExit(f"kwzVideo.py / kwzAudio.py が見つからない: {BASE_DIR}")

    bot.run(DISCORD_TOKEN)
