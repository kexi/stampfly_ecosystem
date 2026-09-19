"""
sfpilot.credentials - where the Jev API key comes from, in one place.
sfpilot.credentials - Jev の API キーの取得を 1 か所にまとめる。

Every entry point that talks to Jev (`sf pilot bench` / `run` / `say` /
`mission`) resolves the key through `resolve_api_key()` and nowhere else.
One path means one place to audit: the key is read here, handed to
`JevJudge`, and never stored, logged, traced or put in an exception.

Jev と通信する入口（`sf pilot bench`・`run`・`say`・`mission`）はすべて
`resolve_api_key()` からキーを得る。経路を 1 本にすれば点検箇所も 1 つで済む。
キーはここで読み、`JevJudge` に渡すだけで、保存・記録・例外文のいずれにも
残さない。

Two sources, in order:

  1. the environment variable `TYPESAFE_API_KEY`
  2. on macOS only, the login keychain, via `security find-generic-password`

取得元は 2 つ、この順で試す:

  1. 環境変数 `TYPESAFE_API_KEY`
  2. macOS に限り、ログインキーチェーン（`security find-generic-password`）

Why the keychain: the key then survives a new shell without being typed or
pasted again, and macOS guards it with the login password rather than with
file permissions.

キーチェーンを使う理由: 新しいシェルでも打ち直し・貼り直しをせずに済み、
macOS がファイル権限ではなくログインパスワードで保護してくれる。

Why NOT a `.env` file, which is the usual third option: a `.env` is plain
text living next to the repository, so the key is one `git add .` or one
shared archive away from being published, and a file that is meant to stay
untracked is only untracked until somebody's editor writes it somewhere
else. The environment variable and the keychain both keep the secret out
of the working tree entirely.

3 つ目の選択肢として普通に挙がる `.env` ファイルを使わない理由: `.env` は
リポジトリの隣に置かれた平文であり、`git add .` 1 回、あるいは書庫の共有 1 回で
公開されうる。追跡しない約束のファイルは、誰かのエディタが別の場所へ書き出す
までしか追跡されない。環境変数もキーチェーンも、秘密を作業ツリーの外に置く。
"""

import os
import platform
import subprocess

from .config import DEFAULT_CONFIG

# The environment variable, which is also what the error message teaches.
# 環境変数。エラー文が案内するのもこれである。
API_KEY_ENV = "TYPESAFE_API_KEY"

# Overrides the keychain service name, for anyone who stores it under a
# different label. The default lives in config.JudgeConfig.
# キーチェーンのサービス名を上書きする環境変数。別の名前で保存している人の
# ためのもの。既定値は config.JudgeConfig にある。
KEYCHAIN_SERVICE_ENV = "SF_TYPESAFE_KEYCHAIN_SERVICE"

# How long `security` may take. It answers instantly when the keychain is
# unlocked and prompts when it is not; the ceiling stops a pilot command
# hanging forever on a prompt nobody is looking at.
# `security` に許す時間 [s]。キーチェーンが解錠されていれば即答し、されて
# いなければ問い合わせる。上限は、誰も見ていない問い合わせで操縦コマンドが
# 永久に止まるのを防ぐためにある。
KEYCHAIN_TIMEOUT_S = 10.0


class MissingApiKey(RuntimeError):
    """No key in the environment and none in the keychain.

    The message carries only instructions, never a key or any part of one.
    環境変数にもキーチェーンにもキーが無い。

    メッセージに載せるのは手順だけで、キーもその一部も載せない。
    """


def resolve_api_key(config=DEFAULT_CONFIG) -> str:
    """Return the Jev API key, or raise `MissingApiKey` explaining how to set one.

    Jev の API キーを返す。無ければ設定方法を添えて `MissingApiKey` を投げる。
    """
    from_env = os.environ.get(API_KEY_ENV)
    if from_env:
        return from_env

    from_keychain = read_keychain_key(keychain_service(config))
    if from_keychain:
        return from_keychain

    raise MissingApiKey(_how_to_set_it(keychain_service(config)))


def keychain_service(config=DEFAULT_CONFIG) -> str:
    """The keychain service name to look the key up under.
    キーを引くキーチェーンのサービス名。"""
    return os.environ.get(KEYCHAIN_SERVICE_ENV) or config.judge.keychain_service


def read_keychain_key(service: str) -> str:
    """The key stored in the login keychain under `service`, or "".

    Returns "" for every failure -- not macOS, no such item, the keychain
    locked and the prompt refused, `security` missing. The caller's next
    step is the same in all of them (say how to set a key), and telling
    them apart here would only add ways for this to raise on a machine that
    simply does not use a keychain.

    `service` でログインキーチェーンに保存されたキー。無ければ ""。

    どの失敗でも "" を返す — macOS でない・項目が無い・施錠されていて問い合わせ
    が断られた・`security` が無い。いずれの場合も呼び出し側の次の一手は同じ
    （キーの設定方法を案内する）であり、ここで区別しても、キーチェーンを使って
    いないだけの環境で例外を増やすことにしかならない。
    """
    is_macos = platform.system() == "Darwin"
    if not is_macos:
        return ""

    command = [
        "security", "find-generic-password",
        "-a", os.environ.get("USER", ""),
        "-s", service,
        "-w",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=KEYCHAIN_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _how_to_set_it(service: str) -> str:
    """The two ways to supply a key, as the operator would run them.

    The `add-generic-password` line ends at a bare `-w` on purpose: given
    no value, `security` reads it from a prompt, so the key never reaches
    the shell history.

    キーを渡す 2 通りの方法を、操作者がそのまま実行できる形で示す。

    `add-generic-password` の行が引数なしの `-w` で終わるのは意図的である。値を
    与えなければ `security` は対話で読むので、キーがシェルの履歴に残らない。
    """
    return (
        f"Jev の API キーが見つかりません（環境変数 {API_KEY_ENV} も "
        f"キーチェーン（サービス名 {service}）も設定されていません）。"
        f"次のいずれかで設定してください:\n"
        f"  1. 環境変数: {API_KEY_ENV}=... sf pilot ...\n"
        f"  2. キーチェーン（macOS。値は対話入力され履歴に残りません）:\n"
        f'     security add-generic-password -U -a "$USER" -s {service} -w\n'
        f"No Jev API key found: neither the environment variable "
        f"{API_KEY_ENV} nor the keychain (service {service}) is set."
    )
