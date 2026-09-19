"""
sfpilot.preflight - the checks that decide whether a real flight starts.
sfpilot.preflight - 実機の飛行を始めてよいかを決める点検。

Five checks, all of which must pass before anything takes off:

  (a) telemetry arrives, as the 140-byte v2 packet, with the battery and
      the downward ToF valid in it
  (b) the pack voltage is at or above `RealFlightConfig.battery_min_v`
  (c) `command` is answered, and the round trip's p95 is inside budget
  (d) Jev answers, and its round trip's p95 is inside the Judge's deadline
  (e) the vehicle reports IDLE_GROUND

5 つの点検。すべて通らなければ何も離陸しない:

  (a) テレメトリが届く。140 バイトの v2 パケットで、電池と下向き ToF が有効
  (b) パック電圧が `RealFlightConfig.battery_min_v` 以上
  (c) `command` に応答があり、往復時間の p95 が予算内
  (d) Jev が応答し、その往復時間の p95 が Judge の期限内
  (e) 機体が IDLE_GROUND を報告している

Why each check is refusal rather than a warning: every one of them is a
thing this package RELIES on while the aircraft is in the air and cannot
recover from afterwards. Without (a) the Monitor classifies nothing and
the immediate safety rules never fire; without (b) the flight starts
inside the band whose purpose is to end one; without (c) a `land` is not
known to reach the vehicle at all, and §2 records that the firmware will
NOT land itself when the PC goes quiet; without (d) the judging layer
hovers on every cycle and the hover-to-land timer lands the aircraft
anyway; without (e) the aircraft is not where the operator thinks it is.

どれも警告ではなく拒否にする理由: いずれも、機体が空中にある間に本パッケージが
**依拠する**ものであり、後から回復できないからである。(a) が無ければ Monitor は
何も区分できず、即時安全則は一度も働かない。(b) が無ければ、飛行を終わらせる
ためにある区分の中から飛行を始めることになる。(c) が無ければ `land` が機体に
届くかどうかすら分からず、しかも §2 は「PC が黙ってもファームは自分で着陸
しない」と記録している。(d) が無ければ判断層は毎周期待機し、待機継続の計時が
どのみち機体を着陸させる。(e) が無ければ、機体は操作者が思っている場所にいない。

Nothing here commands motion. The only thing sent to the vehicle is
`command` (enter SDK mode), which the API reference lists as a mode change
with no effect on the motors.

ここでは動作を一切指令しない。機体へ送るのは `command`（SDK モードへの移行）
だけであり、API リファレンスはこれをモータに影響しないモード変更としている。
"""

import time
from dataclasses import dataclass, field

from .config import DEFAULT_CONFIG
from .link import BATTERY_FULL_V, BATTERY_EMPTY_V

# The flight state the vehicle must be in to take off: on the ground, not
# armed. Spelled from the firmware's own FlightState name, which reaches a
# Sample through `sfcli.commands.telemetry.STATE_NAMES`.
# 離陸のために機体が居なければならない飛行状態: 接地・未 ARM。ファーム自身の
# FlightState 名をそのまま書く。`sfcli.commands.telemetry.STATE_NAMES` を通って
# Sample に届く名前である。
STATE_IDLE_GROUND = "IDLE_GROUND"

# Check identifiers, in the order they are run and printed. Named rather
# than spelled at each site so a result and the table that prints it cannot
# come apart.
# 点検の識別子。実行・表示の順に並べる。結果と、それを表示する表が食い違わない
# よう、各所に書かず名前にする。
CHECK_TELEMETRY = "telemetry"
CHECK_BATTERY = "battery"
CHECK_VEHICLE_LINK = "vehicle_link"
CHECK_JEV = "jev"
CHECK_STATE = "state"

CHECK_ORDER = (CHECK_TELEMETRY, CHECK_BATTERY, CHECK_VEHICLE_LINK,
               CHECK_JEV, CHECK_STATE)


@dataclass
class CheckResult:
    """One check's verdict, with the measurement it was reached from.
    1 つの点検の判定と、その根拠になった測定。"""

    name: str
    passed: bool
    detail: str = ""
    # The numbers behind `detail`, for the trace. Kept apart from the text
    # so a later run can be compared against this one without parsing a
    # sentence written for a person.
    # `detail` の背後にある数値。記録用。人向けに書いた文を解析せずに後の実行と
    # 比べられるよう、文とは分けて持つ。
    measured: dict = field(default_factory=dict)
    # True when the check could not be carried out at all, as distinct from
    # carried out and failed. Both refuse the flight — the difference is
    # what the operator should go and look at.
    # 点検そのものを実施できなかったとき True。実施して不合格だった場合とは
    # 区別する。どちらも飛行は拒否する。違うのは、操作者が何を見に行くべきかで
    # ある。
    skipped: bool = False


@dataclass
class PreflightReport:
    """Every check's result, and whether the flight may start.
    全点検の結果と、飛行を始めてよいかどうか。"""

    checks: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True only when every check passed. / 全点検が通ったときだけ True。"""
        return all(check.passed for check in self.checks)

    def get(self, name: str):
        """One check by name, or None. / 名前で 1 件引く。無ければ None。"""
        for check in self.checks:
            if check.name == name:
                return check
        return None

    def to_row(self) -> dict:
        """The report as one JSON-able object, for the trace.
        記録用に、報告全体を 1 つの JSON 化できるオブジェクトにする。"""
        return {
            "ok": self.ok,
            "checks": [
                {"name": c.name, "passed": c.passed, "skipped": c.skipped,
                 "detail": c.detail, "measured": c.measured}
                for c in self.checks
            ],
        }


def run_preflight(link, judge=None, config=DEFAULT_CONFIG, clock=None,
                  sleep=None) -> PreflightReport:
    """Run every check against a live link and return the report.

    `judge` may be None, which is what `--fake` supplies: the Jev check
    then records that there was nothing to reach rather than failing, since
    a rule-based judge has no network to be measured.

    The order matters. Telemetry comes first because three of the other
    four read it (the battery, the flight state, and the fact that the
    packet is v2 at all), and `command` comes before Jev because a link to
    the aircraft that does not work makes a working link to TypeSafe
    irrelevant.

    実際のリンクに対して全点検を走らせ、報告を返す。

    `judge` は None でもよい。`--fake` が渡すのがそれで、そのとき Jev の点検は、
    不合格ではなく「到達すべき相手が無かった」と記録する。規則ベースの judge に、
    測るべき網は無いからである。

    順序には意味がある。テレメトリが先なのは、残り 4 つのうち 3 つがそれを読む
    ためである（電池・飛行状態・そもそもパケットが v2 であること）。`command` が
    Jev より先なのは、機体へのリンクが働いていないなら、TypeSafe へのリンクが
    働いていても意味が無いからである。
    """
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    cfg = config.real

    telemetry, samples = _check_telemetry(link, cfg, clock, sleep)
    report = PreflightReport(checks=[telemetry])
    report.checks.append(_check_battery(samples, cfg))
    report.checks.append(_check_vehicle_link(link, cfg, clock))
    report.checks.append(_check_jev(judge, config, clock))
    report.checks.append(_check_state(samples))
    return report


# =============================================================================
# (a) telemetry / テレメトリ
# =============================================================================
def _check_telemetry(link, cfg, clock, sleep):
    """Listen for `telemetry_probe_s` and judge the stream. Returns
    (result, samples).

    Three things are judged from one listen, because they are three
    properties of the SAME stream and listening three times would judge
    three different moments: that packets arrive at all, that enough of
    them arrive (the delivery rate), and that they are the 140-byte v2
    packet rather than the 104-byte v1 one.

    The v1 packet is REFUSED rather than worked around. A v1 stream carries
    no battery voltage and no ToF, so `RealLink` fills those from the 10 Hz
    state string — which works, and is why `sf blocks` flies on old
    firmware — but it gives the battery at a tenth of the rate the trend
    window was measured at, and gives no forward distance at all. Flying
    the safety layer on that would be flying a different thing from the one
    every measurement in this plan describes.

    `telemetry_probe_s` の間だけ聞き、流れを判定する。(結果, サンプル) を返す。

    1 回の聴取から 3 つを判定する。それらは**同じ**流れの 3 つの性質であり、
    3 回聞けば 3 つの異なる瞬間を判定することになるからである: パケットがそもそも
    届くこと、十分な数が届くこと（到達率）、そしてそれが 104 バイトの v1 ではなく
    140 バイトの v2 であること。

    v1 のパケットは回避せず**拒否する**。v1 の流れは電池電圧も ToF も運ばないので、
    `RealLink` はそれらを 10Hz の状態文字列から補う —— 動きはするし、`sf blocks`
    が旧ファームで飛べる理由でもある —— が、傾向の窓を実測したときの 1/10 の周期で
    しか電池を与えず、前方距離はまったく与えない。その上で安全層を飛ばすことは、
    本計画のあらゆる実測が述べているものとは別のものを飛ばすことである。
    """
    samples = _listen(link, cfg.telemetry_probe_s, clock, sleep)
    expected = max(1, round(cfg.telemetry_probe_s * 50.0))
    rate = len(samples) / expected
    measured = {"received": len(samples), "expected": expected,
                "rate": round(rate, 3)}

    if not samples:
        return _failed(CHECK_TELEMETRY, measured, skipped=True, detail=(
            f"UDP:5005 に {cfg.telemetry_probe_s:g}s 間 1 件も届かない。"
            f"機体の電源・`wifi.mode=1`・Mac の接続先を確認する "
            f"/ nothing arrived on UDP:5005"
        )), samples

    # v2 is identified by what only v2 carries. A v1 packet has neither
    # field, and `RealLink` would have supplied them from the 10 Hz state
    # string — so the test is made on the NEWEST sample's own keys before
    # that merge could mask the difference... which it cannot, because the
    # merge only fills keys that are absent. What it would mask is the RATE,
    # and that is exactly why the version is checked separately.
    # v2 は「v2 にしか無いもの」で判別する。v1 のパケットはどちらの項目も持たず、
    # `RealLink` はそれらを 10Hz の状態文字列から補っていたはずである —— そこで
    # 判定は最新サンプル自身のキーに対して行う。併合が違いを覆い隠す前に、と
    # 言いたいところだが、併合は無いキーを埋めるだけなので覆い隠さない。覆い隠す
    # のは**周期**のほうであり、版を別に確かめるのはまさにそのためである。
    newest = samples[-1]
    has_v2_fields = "battery_v" in newest and "tof_m" in newest
    if not has_v2_fields:
        return _failed(CHECK_TELEMETRY, measured, detail=(
            "テレメトリに電池電圧か下向き ToF が無い（104B の v1、または有効ビットが "
            "落ちている）。実機の run は 140B の v2 を前提とする "
            "/ the 140-byte v2 packet is required"
        )), samples

    is_rate_enough = rate >= cfg.telemetry_rate_min
    if not is_rate_enough:
        return _failed(CHECK_TELEMETRY, measured, detail=(
            f"受信率 {rate * 100:.0f}%（下限 {cfg.telemetry_rate_min * 100:.0f}%）。"
            f"{len(samples)}/{expected} 件 / delivery rate too low"
        )), samples

    return CheckResult(
        name=CHECK_TELEMETRY, passed=True, measured=measured,
        detail=f"140B v2、受信率 {rate * 100:.0f}%（{len(samples)}/{expected} 件）",
    ), samples


def _listen(link, seconds: float, clock, sleep) -> list:
    """Collect every Sample that arrives over `seconds`.
    `seconds` の間に届いた Sample をすべて集める。

    Drained repeatedly rather than once at the end: `RealLink` queues
    without bound, but a single read at the end would also collect whatever
    was already queued from before the probe began, and the rate this
    measures would then include packets from a period nobody was timing.
    最後に 1 回ではなく繰り返し吸い出す。`RealLink` の待ち行列に上限は無いが、
    最後に 1 回読めば、点検が始まる前から溜まっていたものまで数えてしまい、
    ここで測る到達率が、誰も計時していない期間のパケットを含むことになる。
    """
    link.read_samples()        # discard anything queued before the probe / 開始前の分を捨てる
    deadline = clock() + seconds
    collected: list = []
    while clock() < deadline:
        collected.extend(link.read_samples())
        sleep(0.02)
    collected.extend(link.read_samples())
    return collected


# =============================================================================
# (b) battery / 電池
# =============================================================================
def _check_battery(samples: list, cfg):
    """The pack voltage against the takeoff minimum.

    Read from the telemetry rather than from `battery?`: the 140-byte
    packet carries the voltage the Monitor will be judging on for the whole
    flight, so checking THAT one closes the loop — a preflight that passed
    on a different source would not have checked the source the flight uses.

    離陸の下限に対するパック電圧。

    `battery?` ではなくテレメトリから読む。140 バイトのパケットが運ぶのは、
    飛行のあいだずっと Monitor が判定に使う当の電圧であり、**それ**を確かめて
    はじめて輪が閉じる。別の出どころで通った点検は、飛行が使う出どころを
    確かめていない。
    """
    voltages = [s["battery_v"] for s in samples if "battery_v" in s]
    if not voltages:
        return _failed(CHECK_BATTERY, {}, skipped=True, detail=(
            "電池電圧が読めない（テレメトリの点検を先に通すこと） "
            "/ no battery voltage in the telemetry"
        ))

    # The lowest reading seen, not the mean: the aircraft is on the ground
    # with the motors off, so there is no load transient to average away,
    # and a pack that dipped once will dip again under a hover.
    # 平均ではなく最も低い読み。機体は接地しモータは止まっているので、均して
    # 消すべき負荷の過渡は無い。一度落ち込んだパックは、ホバリングでも落ち込む。
    lowest = min(voltages)
    percent = _percent(lowest)
    measured = {"lowest_v": round(lowest, 3), "percent": round(percent, 1),
                "minimum_v": cfg.battery_min_v, "samples": len(voltages)}

    is_enough = lowest >= cfg.battery_min_v
    if not is_enough:
        return _failed(CHECK_BATTERY, measured, detail=(
            f"{lowest:.2f}V（約 {percent:.0f}%）は離陸の下限 "
            f"{cfg.battery_min_v:.2f}V を下回る。充電したパックに替える "
            f"/ pack voltage below the takeoff minimum"
        ))
    return CheckResult(
        name=CHECK_BATTERY, passed=True, measured=measured,
        detail=f"{lowest:.2f}V（約 {percent:.0f}%）≧ {cfg.battery_min_v:.2f}V",
    )


def _percent(voltage: float) -> float:
    """Pack voltage as a percentage, on the same map the Monitor uses.
    Monitor と同じ対応でパック電圧を百分率にする。"""
    ratio = (voltage - BATTERY_EMPTY_V) / (BATTERY_FULL_V - BATTERY_EMPTY_V)
    return max(0.0, min(100.0, ratio * 100.0))


# =============================================================================
# (c) the link to the vehicle / 機体へのリンク
# =============================================================================
def _check_vehicle_link(link, cfg, clock):
    """Send `command` N times and judge the round trip's p95.

    `command` is the one verb that can be asked repeatedly with no effect:
    the API reference lists it as entering SDK mode, and entering it again
    while already in it is answered `ok` and changes nothing. Everything
    else the aircraft answers either moves it or reads a sensor whose value
    would then need interpreting.

    A link that cannot be measured at all fails: the whole reason to
    measure it is that §2 records the firmware will NOT land itself when
    the PC goes quiet, so a `land` this side cannot deliver is a flight
    nobody can end from the PC.

    `command` を N 回送り、往復時間の p95 を判定する。

    `command` は、繰り返し問うても何も起きない唯一の verb である。API リファレンスは
    これを SDK モードへの移行としており、既にその状態で再度入れば `ok` が返るだけで
    何も変わらない。機体が答える他のものは、機体を動かすか、解釈の要る値を読むかの
    どちらかである。

    まったく測れないリンクは不合格とする。測る理由そのものが、「PC が黙っても
    ファームは自分で着陸しない」と §2 が記録していることだからである。こちらから
    届けられない `land` とは、PC からは誰も終わらせられない飛行のことである。
    """
    if not hasattr(link, "send"):
        return _failed(CHECK_VEHICLE_LINK, {}, skipped=True, detail=(
            "このリンクは応答を待つ送信を持たない（実機用ではない） "
            "/ this link has no request/reply path"
        ))

    latencies: list = []
    failures = 0
    for _ in range(cfg.command_probe_count):
        started = clock()
        status, _text = link.send("command", cfg.command_timeout_s)
        elapsed_ms = (clock() - started) * 1e3
        if status == "ok":
            latencies.append(elapsed_ms)
            continue
        failures += 1

    measured = {"sent": cfg.command_probe_count, "answered": len(latencies),
                "failures": failures,
                "p95_ms": round(percentile(latencies, 95), 1) if latencies else None,
                "p50_ms": round(percentile(latencies, 50), 1) if latencies else None,
                "budget_ms": round(cfg.command_p95_max_s * 1e3, 1)}

    if not latencies:
        return _failed(CHECK_VEHICLE_LINK, measured, detail=(
            f"`command` に {cfg.command_probe_count} 回とも応答が無い。"
            f"UDP:8889 が {cfg.host} に届いていない "
            f"/ no reply to `command`"
        ))
    if failures:
        return _failed(CHECK_VEHICLE_LINK, measured, detail=(
            f"`command` {cfg.command_probe_count} 回中 {failures} 回が無応答。"
            f"取りこぼすリンクでは `land` も取りこぼす "
            f"/ {failures} of {cfg.command_probe_count} unanswered"
        ))

    p95_ms = percentile(latencies, 95)
    budget_ms = cfg.command_p95_max_s * 1e3
    is_fast_enough = p95_ms <= budget_ms
    if not is_fast_enough:
        return _failed(CHECK_VEHICLE_LINK, measured, detail=(
            f"往復 p95 {p95_ms:.0f}ms が予算 {budget_ms:.0f}ms を超える "
            f"/ round-trip p95 over budget"
        ))
    return CheckResult(
        name=CHECK_VEHICLE_LINK, passed=True, measured=measured,
        detail=(f"`command` {len(latencies)}/{cfg.command_probe_count} 応答、"
                f"p50 {percentile(latencies, 50):.0f}ms / "
                f"p95 {p95_ms:.0f}ms ≦ {budget_ms:.0f}ms"),
    )


# =============================================================================
# (d) Jev / Jev への疎通
# =============================================================================
def _check_jev(judge, config, clock):
    """Ask Jev a few times and judge the p95 against the Judge's deadline.

    This repeats what `sf pilot bench` measures twenty times, and does so
    deliberately: the question here is not what the distribution is (§4 has
    that) but whether the network works right now, from this room, before
    the aircraft is in the air. A `--fake` run has no network and records
    that rather than failing.

    The state asked about is a stationary aircraft on the ground, so the
    answer is never used — only its timing is. Asking the real question
    shape rather than a stub is what makes the measurement the same one the
    flight will pay: the same token count, the same connection, the same
    warm-up.

    Jev に数回問い、p95 を Judge の期限に照らす。

    `sf pilot bench` が 20 回測るものの繰り返しであり、意図してそうしている。
    ここでの問いは分布がどうか（それは §4 にある）ではなく、機体が空へ上がる前に、
    いまこの部屋から網が働いているかである。`--fake` の実行には網が無く、それを
    不合格ではなく記録として残す。

    問う state は接地して静止した機体のものなので、答えは一切使わない。使うのは
    時間だけである。雛形ではなく本物の質問の形で問うことが、この測定を飛行が実際に
    払うものと同じにする —— 同じトークン数、同じ接続、同じ暖機である。
    """
    from .judge import SAFETY_ONLY

    cfg = config.real
    # A judge with no network to measure. `--fake` supplies one, and asking
    # it would produce a 0 ms "round trip" that looks like a passing network
    # check to anyone reading the table -- the one reading this check exists
    # to prevent. Reported as "not asked" instead, which is what happened.
    # 測るべき網を持たない judge。`--fake` が渡すのがそれで、それに問えば 0ms の
    #「往復時間」が出る。表を読む人には、網の点検が通ったように見える —— この点検が
    # 防ぐためにある、まさにその読み違いである。代わりに「問わなかった」と報告する。
    # 実際に起きたのがそれだからである。
    needs_no_network = judge is None or not _talks_to_jev(judge)
    if needs_no_network:
        return CheckResult(
            name=CHECK_JEV, passed=True, skipped=False,
            detail="--fake のため Jev へは問い合わせない（判断は規則ベース）"
                   " / not asked (rule-based judge)",
            measured={"asked": 0},
        )

    latencies, errors = _probe_jev(judge, cfg.jev_probe_count, clock)
    deadline_ms = config.judge.deadline_s * 1e3
    measured = {"asked": cfg.jev_probe_count, "answered": len(latencies),
                "p95_ms": round(percentile(latencies, 95), 1) if latencies else None,
                "deadline_ms": round(deadline_ms, 1), "errors": errors[:2]}

    if not latencies:
        first = errors[0] if errors else "理由不明 / no reason reported"
        return _failed(CHECK_JEV, measured, detail=(
            f"Jev に {cfg.jev_probe_count} 回とも到達できない: {first}。"
            f"規則ベースで飛ばすなら --fake / Jev unreachable"
        ))

    p95_ms = percentile(latencies, 95)
    is_fast_enough = p95_ms <= deadline_ms
    if not is_fast_enough:
        return _failed(CHECK_JEV, measured, detail=(
            f"Jev の往復 p95 {p95_ms:.0f}ms が期限 {deadline_ms:.0f}ms を超える。"
            f"この網では判断が毎回捨てられる / Jev p95 over the deadline"
        ))
    return CheckResult(
        name=CHECK_JEV, passed=True, measured=measured,
        detail=f"{len(latencies)}/{cfg.jev_probe_count} 応答、p95 {p95_ms:.0f}ms "
               f"≦ 期限 {deadline_ms:.0f}ms",
    )


def _probe_jev(judge, count: int, clock):
    """Ask `count` times; return (latencies [ms], reasons the asks failed).

    Every failure is collected rather than raised, because this runs
    BEFORE a flight to decide whether there will be one: a check that
    decides that must not itself be the thing that aborts. Whatever went
    wrong reaches the operator as a failed check with a reason, not as a
    traceback.

    `count` 回問い、(往復時間 [ms], 失敗した理由) を返す。

    失敗はすべて集め、投げない。ここは飛行の**前**に、飛行するかどうかを決める
    ために走るからである。それを決める点検が、中断させる当のものであってはならない。
    何が起きたにせよ、操作者にはトレースバックではなく、理由つきの不合格として届く。
    """
    from .judge import SAFETY_ONLY

    latencies: list = []
    errors: list = []
    for _ in range(count):
        started = clock()
        try:
            judgement = judge.ask(GROUND_STATE, SAFETY_ONLY)
        except Exception as exc:                      # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")
            continue
        # A judge that answers with nothing is a failed ask, not a fast
        # one. `ask` is documented to return a Judgement, but reading
        # `.error` off None would raise here -- on the ground, as a crash
        # rather than as a check that did not pass.
        # 何も返さない judge は、速い応答ではなく失敗した問い合わせである。
        # `ask` は Judgement を返すと文書化されているが、None から `.error` を
        # 読めばここで例外になる ―― 地上で、「通らなかった点検」ではなく異常終了
        # として。
        if judgement is None:
            errors.append("judge returned no answer（judge が答えを返さない）")
            continue
        if judgement.error:
            errors.append(judgement.error)
            continue
        latencies.append((clock() - started) * 1e3)
    return latencies, errors


# The situation the preflight asks Jev about: an aircraft sitting on the
# ground. Fixed, like `sf pilot bench`'s, so repeated preflights are
# comparable, and truthful, because it is what the aircraft is doing.
# 飛行前点検が Jev に問う状況: 接地している機体。`sf pilot bench` と同じく固定に
# して、繰り返した点検を比較できるようにする。かつ、それが機体の実際の状態なので
# 偽りが無い。
GROUND_STATE = {
    "flight": {
        "phase": "on the ground",
        "attitude": "close to level",
        "position_estimate": "reliable",
        "ground_distance_sensor": "working normally",
    },
    "battery": {"level": "good", "trend": "steady"},
}


# =============================================================================
# (e) the vehicle's own state / 機体自身の状態
# =============================================================================
def _check_state(samples: list):
    """The vehicle must report IDLE_GROUND.

    Anything else means the aircraft is not where the operator believes it
    is: FLYING means it is already in the air under someone else's control,
    and an error or calibration state means the takeoff would be refused
    anyway — but refused AFTER the operator had been told the checks passed.

    機体が IDLE_GROUND を報告していること。

    それ以外は、機体が操作者の思っている場所に居ないことを意味する。FLYING なら
    既に誰か別の制御下で空中にあり、異常や校正中の状態ならどのみち離陸は拒否
    される —— ただし、点検を通ったと操作者に告げた**後で**拒否される。
    """
    states = [s["flight_state"] for s in samples if "flight_state" in s]
    if not states:
        return _failed(CHECK_STATE, {}, skipped=True, detail=(
            "飛行状態が読めない（テレメトリの点検を先に通すこと） "
            "/ no flight state in the telemetry"
        ))

    newest = states[-1]
    measured = {"state": newest, "expected": STATE_IDLE_GROUND}
    is_on_the_ground = newest == STATE_IDLE_GROUND
    if not is_on_the_ground:
        return _failed(CHECK_STATE, measured, detail=(
            f"機体の状態が {newest} であり {STATE_IDLE_GROUND} ではない。"
            f"平らな床に置き、送信機で解除してから再実行する "
            f"/ the vehicle is not on the ground and idle"
        ))
    return CheckResult(name=CHECK_STATE, passed=True, measured=measured,
                       detail=f"{newest}")


# =============================================================================
# Shared / 共通
# =============================================================================
def _talks_to_jev(judge) -> bool:
    """Whether this judge reaches the network, so its timing means something.

    Asked of the judge itself rather than of the caller's `--fake` flag,
    because the check has to be right for every judge that exists -- the
    three rule-based ones (`FakeJudge` and its two subclasses) all answer
    instantly from a table, and a 0 ms reading from any of them would be
    reported as a healthy network.

    この judge が網に届くか、したがってその時間に意味があるか。

    呼び出し側の `--fake` ではなく judge 自身に問う。点検は、存在するすべての
    judge に対して正しくなければならないからである。規則ベースの 3 つ
    （`FakeJudge` とその派生 2 つ）はいずれも表から即答し、そのどれから出た 0ms も
    「健全な網」として報告されてしまう。

    Named as the rule-based ones rather than as `isinstance(judge,
    JevJudge)`, so the default for anything else is to MEASURE it. A judge
    this module has not been told about is far more likely to be a new way
    of reaching a network than a new table, and measuring something that
    needed no measuring costs a line in the report -- while skipping
    something that did costs the check.

    `isinstance(judge, JevJudge)` ではなく規則ベースのほうを名指しする。それ以外の
    既定が「測る」になるようにするためである。本モジュールが知らされていない judge は、
    新しい表であるより、網へ届く新しい手段である見込みのほうがはるかに高い。測る必要の
    無いものを測った代償は報告の 1 行だが、測るべきものを飛ばした代償は点検そのもので
    ある。
    """
    from .judge import FakeJudge

    return not isinstance(judge, FakeJudge)


def _failed(name: str, measured: dict, detail: str, skipped: bool = False):
    """A failing result, spelled once so every branch reads the same.
    不合格の結果。1 か所に書いて、どの分岐も同じ形になるようにする。"""
    return CheckResult(name=name, passed=False, detail=detail,
                       measured=measured, skipped=skipped)


def percentile(values: list, percent: float) -> float:
    """Nearest-rank percentile, with no numpy.

    The same definition `sfcli.commands.pilot._percentile` uses, so a
    preflight's p95 and a `sf pilot bench` p95 are the same statistic and
    can be compared. Kept here rather than imported from the CLI because
    `sfpilot` does not depend on `sfcli` in the other direction.

    最近傍順位の百分位数。numpy を使わない。

    `sfcli.commands.pilot._percentile` と同じ定義にして、点検の p95 と
    `sf pilot bench` の p95 が同じ統計量として比べられるようにする。`sfpilot` は
    逆向きに `sfcli` へ依存しないので、CLI から import せずここに置く。
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, int(round(percent / 100.0 * len(ordered))))
    return ordered[min(rank, len(ordered)) - 1]


def format_table(report: PreflightReport) -> list:
    """The report as lines for the terminal.
    報告を端末用の行にする。"""
    lines = ["  飛行前点検 / preflight", "  " + "-" * 62]
    for name in CHECK_ORDER:
        check = report.get(name)
        if check is None:
            continue
        mark = "PASS" if check.passed else ("SKIP" if check.skipped else "FAIL")
        lines.append(f"  [{mark}] {_LABELS[name]:<22} {check.detail}")
    lines.append("  " + "-" * 62)
    verdict = "離陸してよい / clear to fly" if report.ok else (
        "**離陸しない** / NOT clear to fly")
    lines.append(f"  {verdict}")
    return lines


# What each check is called on the table. Japanese first, matching the
# repository's documentation convention.
# 表に出す各点検の名前。リポジトリの文書の慣例どおり日本語を先にする。
_LABELS = {
    CHECK_TELEMETRY: "(a) テレメトリ",
    CHECK_BATTERY: "(b) 電池電圧",
    CHECK_VEHICLE_LINK: "(c) 機体との往復",
    CHECK_JEV: "(d) Jev への疎通",
    CHECK_STATE: "(e) 機体の状態",
}
