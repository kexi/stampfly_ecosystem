"""
sfpilot.config - every threshold, deadline and envelope in one place.
sfpilot.config - しきい値・期限・包絡をここ 1 か所に集める。

Why a config module rather than constants next to each user: Monitor,
Arbiter and Executor must agree on the same numbers (a deadline the
Judge uses and the Arbiter checks; an altitude band the Monitor
classifies against and the Executor refuses to leave). Duplicating them
lets the two drift apart silently.

なぜ利用箇所ごとの定数ではなく設定モジュールか: Monitor・Arbiter・Executor は
同じ数値を共有する必要がある（Judge が使う期限を Arbiter が照合する、Monitor が
区分に使う高度帯を Executor が逸脱拒否に使う）。それぞれに書くと、片方だけ
変更されて食い違っても気づけない。

Why not a YAML/TOML file: the values below are still provisional (the
design says they are to be decided with SILS and log replay). A dataclass
keeps them type-checked and greppable until the measurements exist; a
config file can wrap this later without changing any caller.

なぜ YAML/TOML ファイルにしないか: 下の値は暫定であり、設計では SILS と
ログ再生で決めるとしている。実測が出るまでは dataclass のほうが型検査と
grep が効く。後から設定ファイルで包んでも、呼び出し側は変えずに済む。
"""

from dataclasses import dataclass, field

# Frequencies / 周期
MONITOR_HZ = 50.0      # telemetry sampling rate (UDP:5005) / テレメトリ受信周期
EXECUTOR_RC_HZ = 20.0  # rc command resend rate / rc 指令の送信周期
JUDGE_PERIODIC_HZ = 1.0  # ask Jev at least this often / 定期的に Jev へ問う下限周期


@dataclass(frozen=True)
class JudgeConfig:
    """Deadlines and confidence gates for the Jev judging layer.
    Jev 判断層の期限と確信度の関門。"""

    # One request carries every question, and we wait this long for it.
    # 500ms: the design's provisional budget, to be revised from the
    # `sf pilot bench` p95 recorded in docs/plans/jev-autopilot.md.
    # 1 リクエストに全質問を載せ、この時間だけ待つ。500ms は設計の暫定値で、
    # `sf pilot bench` の p95 実測を計画文書に記録したうえで見直す。
    deadline_s: float = 0.5

    # A Choice answer below this confidence is not trusted.
    # この確信度に満たない Choice の答えは採用しない。
    min_confidence: float = 0.6

    # "Continue" and "land" within this margin of each other means the
    # model is torn between opposites -- treat as no answer, not as a
    # coin flip, because the two actions are not interchangeable.
    # 「継続」と「着陸」の確率差がこの範囲なら、正反対の選択肢で拮抗して
    # いるということ。二択の当てずっぽうにはせず、答え無しとして扱う
    # （この 2 つは互いに代替できる行動ではないため）。
    tie_margin: float = 0.15

    # Retries are deliberately absent: a retried answer arrives against a
    # situation that has already moved on. The next cycle re-asks anyway.
    # 再試行はあえて行わない。再試行の答えが届く頃には状況が変わっている。
    # 次の周期で自然に問い直される。
    model: str = "jev-latest"


@dataclass(frozen=True)
class EnvelopeConfig:
    """The physical box the vehicle is allowed to move in.
    機体の移動を許す物理的な範囲。

    Any action that would leave this box is refused by the Arbiter,
    whoever proposed it. 提案者が誰であれ、この範囲を出る行動は Arbiter が却下する。
    """

    altitude_min_m: float = 0.3
    altitude_max_m: float = 1.5
    radius_max_m: float = 2.0        # from the takeoff point / 離陸点からの半径
    speed_max_mps: float = 0.5       # horizontal command ceiling / 水平指令の上限
    climb_rate_max_mps: float = 0.3  # vertical command ceiling / 垂直指令の上限


@dataclass(frozen=True)
class MonitorConfig:
    """Thresholds the Monitor uses to turn numbers into words.
    Monitor が数値を言葉の区分に変える際のしきい値。"""

    # Altitude bands, relative to the altitude the operator asked for.
    # 高度の区分（指示された目標高度に対する差）。
    altitude_on_target_m: float = 0.15   # within this: "目標どおり"
    altitude_deviation_m: float = 0.40   # beyond this: immediate stop / 即時停止

    # A trend must hold for this long before it is reported, so a single
    # noisy sample never becomes "sinking".
    # 傾向はこの時間続いて初めて報告する。1 サンプルのノイズが「下降中」に
    # ならないようにするため。
    trend_hold_s: float = 0.6
    trend_rate_mps: float = 0.15         # |d(alt)/dt| above this is a trend / これ以上を傾向とみなす

    # Horizontal drift bands [m/s]. / 水平の流れの区分。
    drift_slow_mps: float = 0.10
    drift_fast_mps: float = 0.35

    # Attitude bands [rad]. 0.26 rad is about 15 deg.
    # 姿勢の区分 [rad]。0.26 rad は約 15 度。
    attitude_level_rad: float = 0.09     # about 5 deg / 約 5 度
    attitude_steep_rad: float = 0.26     # about 15 deg / 約 15 度

    # Position-estimate health: velocity magnitude beyond this, or a
    # non-finite value, means the estimate has diverged.
    # 推定の健全性: 速度の大きさがこれを超える、または有限でない値が出たら
    # 推定が発散したとみなす。
    estimate_diverged_speed_mps: float = 5.0

    # Battery bands [%]. "danger" lands without waiting for Jev.
    # 電池の区分 [%]。"danger" は Jev を待たず着陸する。
    battery_low_pct: float = 30.0
    battery_danger_pct: float = 15.0
    battery_drop_fast_pct: float = 5.0   # this much within the window / 窓内でこれだけ低下
    battery_trend_window_s: float = 10.0

    # ToF (ground distance sensor) plausibility [m]. Outside this the
    # reading is reported as unreliable rather than used.
    # ToF（対地距離センサ）の妥当範囲 [m]。外れた値は使わず「当てにならない」と報告する。
    tof_min_m: float = 0.02
    tof_max_m: float = 4.0

    # How stale a sample may be before the link counts as lost [s].
    # サンプルがこれ以上古くなったら通信断とみなす [s]。
    sample_timeout_s: float = 0.5


@dataclass(frozen=True)
class ArbiterConfig:
    """Rules for accepting or replacing a Jev proposal.
    Jev の提案を採否する規則。"""

    # Consecutive hovering for this long means the situation is not
    # resolving itself -- land rather than hover until the battery dies.
    # これだけ連続で待機した場合、状況は自然には解消しないと判断する。
    # 電池が尽きるまで浮いているより着陸する。
    hover_to_land_s: float = 10.0


@dataclass(frozen=True)
class PilotConfig:
    """The whole configuration, passed as one object.
    設定一式。1 つのオブジェクトとして受け渡す。"""

    judge: JudgeConfig = field(default_factory=JudgeConfig)
    envelope: EnvelopeConfig = field(default_factory=EnvelopeConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    arbiter: ArbiterConfig = field(default_factory=ArbiterConfig)

    monitor_hz: float = MONITOR_HZ
    executor_rc_hz: float = EXECUTOR_RC_HZ
    judge_periodic_hz: float = JUDGE_PERIODIC_HZ


DEFAULT_CONFIG = PilotConfig()
