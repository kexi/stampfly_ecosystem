"""
test_credentials.py - the API key comes from the environment first, from
the macOS keychain second, and never appears in an error message.
test_credentials.py - API キーは環境変数を最優先、次に macOS キーチェーンから
取得され、エラー文には決して現れないこと。
"""

import subprocess

import pytest

from sfpilot import credentials
from sfpilot.config import DEFAULT_CONFIG
from sfpilot.credentials import (
    API_KEY_ENV, KEYCHAIN_SERVICE_ENV, MissingApiKey,
    keychain_service, read_keychain_key, resolve_api_key,
)

# A value shaped like a real key, so a test that checks it does not leak
# fails for the right reason rather than because the string was too short
# to appear anywhere.
# 本物のキーらしい形の値。漏れないことを確かめる試験が、正しい理由で失敗する
# ようにするため（短すぎてどこにも現れなかった、では確かめたことにならない）。
_SECRET = "sk-test-0123456789abcdef-never-print-me"


def _no_keychain(monkeypatch) -> None:
    """Make the keychain answer "nothing here". / キーチェーンを「無し」にする。"""
    monkeypatch.setattr(credentials, "read_keychain_key", lambda service: "")


def test_the_environment_variable_wins(monkeypatch):
    """An environment variable is used without consulting the keychain.

    Ordering matters for a reason the operator feels: exporting a key for
    one command must override whatever is stored, so a second key can be
    tried without disturbing the saved one.

    環境変数があれば、キーチェーンを見ずにそれを使うこと。

    順序には操作者が実感する理由がある。1 回の実行のために export したキーは、
    保存されているものより優先されなければならない。そうであってはじめて、
    保存済みのキーを触らずに別のキーを試せる。
    """
    monkeypatch.setenv(API_KEY_ENV, _SECRET)
    called = []
    monkeypatch.setattr(credentials, "read_keychain_key",
                        lambda service: called.append(service) or "other")

    assert resolve_api_key() == _SECRET
    assert called == [], "the keychain must not be consulted at all"


def test_the_keychain_is_used_when_the_environment_is_empty(monkeypatch):
    """With no environment variable, the key comes from `security`.
    環境変数が無ければ、キーは `security` から取得されること。"""
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    monkeypatch.setattr(credentials, "read_keychain_key", lambda service: _SECRET)

    assert resolve_api_key() == _SECRET


def test_the_keychain_is_read_through_security(monkeypatch):
    """`read_keychain_key` shells out to `security` and returns its output.

    The subprocess is mocked rather than run, so the test passes on a
    machine with no such keychain item and never needs a real key.

    `read_keychain_key` が `security` を呼び、その出力を返すこと。

    subprocess は実行せず差し替える。その項目を持たないマシンでも通り、本物の
    キーを必要としないようにするためである。
    """
    monkeypatch.setattr(credentials.platform, "system", lambda: "Darwin")
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout=_SECRET + "\n", stderr="")

    monkeypatch.setattr(credentials.subprocess, "run", fake_run)

    assert read_keychain_key("typesafe-api-key") == _SECRET
    assert seen["command"][:2] == ["security", "find-generic-password"]
    assert "-w" in seen["command"], "-w is what prints the password"
    assert "typesafe-api-key" in seen["command"]


def test_a_keychain_miss_is_not_an_error(monkeypatch):
    """A non-zero `security` exit means "no key", not a crash.

    Every failure mode reads the same way -- no such item, locked and the
    prompt refused -- because the caller's next step is identical in all of
    them.

    `security` が非ゼロで終了した場合は「キーが無い」であって異常終了ではない
    こと。

    どの失敗も同じに読む（項目が無い、施錠されていて問い合わせが断られた）。
    いずれでも呼び出し側の次の一手は同じだからである。
    """
    monkeypatch.setattr(credentials.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        credentials.subprocess, "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 44, "", "not found"),
    )

    assert read_keychain_key("typesafe-api-key") == ""


def test_the_keychain_is_not_touched_off_macos(monkeypatch):
    """`security` is a macOS program, so it is never invoked elsewhere.

    Calling it on Linux would raise FileNotFoundError inside a subprocess
    layer, turning "no key configured" into an obscure crash on every
    machine that is not a Mac.

    `security` は macOS のプログラムなので、それ以外では呼ばないこと。

    Linux で呼べば subprocess 層で FileNotFoundError になり、Mac でない
    すべての環境で「キーが未設定」が分かりにくい異常終了に化けてしまう。
    """
    monkeypatch.setattr(credentials.platform, "system", lambda: "Linux")

    def explode(*args, **kwargs):
        raise AssertionError("security must not be run off macOS")

    monkeypatch.setattr(credentials.subprocess, "run", explode)

    assert read_keychain_key("typesafe-api-key") == ""


def test_the_service_name_can_be_overridden(monkeypatch):
    """A different keychain label is usable without editing the code.
    別のキーチェーン名を、コードを編集せずに使えること。"""
    monkeypatch.delenv(KEYCHAIN_SERVICE_ENV, raising=False)
    assert keychain_service() == DEFAULT_CONFIG.judge.keychain_service

    monkeypatch.setenv(KEYCHAIN_SERVICE_ENV, "my-own-label")
    assert keychain_service() == "my-own-label"


def test_the_error_says_how_to_set_a_key_and_carries_no_secret(monkeypatch):
    """The failure names both ways to supply a key and leaks nothing.

    The message is the one users paste into bug reports, so it must teach
    the fix without ever quoting a key -- including one that happened to be
    set but was rejected somewhere else.

    失敗時に、キーを渡す 2 通りの方法を示し、秘密は一切含まないこと。

    この文は利用者が不具合報告に貼り付けるものなので、キーを引用せずに解決策を
    伝えなければならない（たまたま設定されていて別の場所で弾かれたキーも含む）。
    """
    monkeypatch.setenv(API_KEY_ENV, _SECRET)
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    _no_keychain(monkeypatch)

    with pytest.raises(MissingApiKey) as raised:
        resolve_api_key()

    message = str(raised.value)
    assert _SECRET not in message
    assert API_KEY_ENV in message, "it must name the environment variable"
    assert "add-generic-password" in message, "and the keychain command"


def test_no_key_anywhere_raises_rather_than_returning_empty(monkeypatch):
    """An absent key is an exception, never an empty string.

    Returning "" would send an unauthenticated request and surface as an
    HTTP 401 several layers away from the thing that is actually wrong.

    キーが無い場合は例外にし、空文字を返さないこと。

    "" を返せば認証なしのリクエストを送ることになり、本当の原因から何層も
    離れた HTTP 401 として現れてしまう。
    """
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    _no_keychain(monkeypatch)

    with pytest.raises(MissingApiKey):
        resolve_api_key()
