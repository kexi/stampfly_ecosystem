"""
sfpilot.config - every threshold, deadline and envelope in one place.
sfpilot.config - しきい値・期限・飛行領域をここ 1 か所に集める。

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

    # The macOS keychain service name the API key is stored under, when it
    # is not in the environment. Overridable per machine through
    # SF_TYPESAFE_KEYCHAIN_SERVICE (see credentials.py).
    # 環境変数に無い場合に API キーを引く、macOS キーチェーンのサービス名。
    # マシンごとに SF_TYPESAFE_KEYCHAIN_SERVICE で変更できる（credentials.py）。
    keychain_service: str = "typesafe-api-key"


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

    # The battery TREND is judged on pack VOLTAGE, not on the percentage.
    #
    # The percentage is a linear map of the instantaneous, LOADED voltage
    # (link.battery_percent), so it moves for two reasons that have nothing
    # to do with energy spent: the motors spinning up, and the ADC's 0.01 V
    # quantisation. Measured in SILS on 2026-09-19: `takeoff` alone drops
    # the reading 4.19 V -> 3.79 V inside one second, which is 99% -> 55%,
    # a 44-point fall with the pack essentially full. Against the old
    # `battery_drop_fast_pct = 5.0` over a 10 s window, that guaranteed
    # "fell sharply" on every healthy flight -- and a steady hover alone
    # drains 0.35 pct/s, reaching 8.9 points in the worst 10 s window, over
    # the threshold again. This is why `run nominal` landed at 7.4 s.
    #
    # Volts avoid the amplification: the same steady hover moves 0.0033 V/s,
    # so the window below sees about 0.07 V of honest drain against a
    # threshold of 0.25 V, while a genuinely failing pack (the
    # `battery_drop` scene walks 4.05 V -> 3.35 V) crosses it.
    #
    # 電池の**傾向**はパック電圧で判定する。百分率では判定しない。
    #
    # 百分率は、その瞬間の**負荷がかかった**電圧を線形に写したものであり
    #（link.battery_percent）、消費エネルギーとは無関係な 2 つの理由で動く:
    # モータの起動と、ADC の 0.01V 量子化である。2026-09-19 の SILS 実測:
    # `takeoff` だけで読みが 1 秒以内に 4.19V → 3.79V、すなわち 99% → 55% まで
    # 落ちる。パックはほぼ満充電なのに 44 ポイントの低下である。従来の
    # 10 秒窓・`battery_drop_fast_pct = 5.0` に対し、これは健全な飛行すべてで
    # 「急に低下」を確定させていた。加えて定常ホバリングだけでも 0.35 pct/s
    # 減り、最悪の 10 秒窓で 8.9 ポイントに達してやはり閾値を超える。
    # `run nominal` が 7.4 秒で着陸した原因がこれである。
    #
    # 電圧ならこの増幅が無い。同じ定常ホバリングは 0.0033V/s なので、下の窓が
    # 見るのは約 0.07V の正直な消費であり、閾値 0.25V に対して十分小さい。
    # 一方、本当に弱っているパック（`battery_drop` の場面は 4.05V → 3.35V を
    # 歩く）はこれを超える。
    # Measured over this window, on the recorded flights themselves rather
    # than from an average rate: the WORST 20 s drop an ordinary hover
    # produces is 0.120 V (fixtures/nominal_hover_states.jsonl), while the
    # LEAST the `battery_drop` scene produces once it is under way is
    # 0.230 V. The threshold sits between those two measured extremes.
    #
    # It is placed nearer the healthy side's worst case than the midpoint,
    # because the cost of the two errors is not symmetric: calling a healthy
    # pack "falling sharply" ends a good flight (which is exactly what
    # happened on 2026-09-19), while being a few seconds late to call a
    # failing one loses nothing -- the LEVEL bands ("running low",
    # "dangerously low") are what actually land the aircraft, and they do
    # not depend on the trend at all.
    #
    # この窓での実測。平均速度からの計算ではなく、記録した飛行そのものから
    # 取った値である: 通常のホバリングが生む 20 秒あたりの**最大**の低下は
    # 0.120V（fixtures/nominal_hover_states.jsonl）、`battery_drop` の場面が
    # 進行してから生む**最小**の低下は 0.230V。閾値はこの実測の両極の間に置く。
    #
    # 中点ではなく健全側の最悪値寄りに置くのは、2 種類の誤りの代償が対称で
    # ないからである。健全なパックを「急に低下」と呼べば良好な飛行を終わらせる
    #（2026-09-19 に実際そうなった）一方、弱ったパックの判定が数秒遅れても失う
    # ものは無い。機体を実際に着陸させるのは**残量**の区分（「残り少ない」
    #「危険」）であり、そちらは傾向に一切依存しない。
    battery_drop_fast_v: float = 0.18
    battery_trend_window_s: float = 20.0

    # How much history to keep BEYOND the trend window. Trimming to exactly
    # the window puts the history's span on the "is the window full?"
    # threshold, where the oldest sample falls in and out each cycle and
    # the answer flips with it -- measured, about twice a second on a pack
    # that was simply draining. With the margin, `Monitor._trend_window`
    # always has a sample comfortably older than the window to measure from.
    # 傾向の窓より、どれだけ長く履歴を残すか。窓ちょうどに刈り込むと、履歴の
    # 長さが「窓が満ちたか」の判定境界に乗り、最古のサンプルが毎周期出入りする
    # たびに答えが反転する。実測では、ただ放電しているだけのパックで毎秒 2 回
    # 反転した。余裕があれば `Monitor._trend_window` は常に、窓より十分に古い
    # サンプルを測定の起点にできる。
    battery_window_margin_s: float = 2.0

    # A gradual fall needs this much too, so that quantisation noise alone
    # (one 0.01 V step either way) is reported as "steady" rather than as a
    # battery that is going down. Set above the 0.120 V worst case an
    # ordinary hover actually produces over this window, for the same reason
    # as the band above: a normal flight should read "steady", not
    # "falling". An earlier value of 0.10 V sat just UNDER that worst case,
    # and a healthy hover reported "falling gradually" for three seconds.
    # 「ゆるやかに低下」にもこれだけの低下を要求する。量子化の揺らぎだけ
    #（0.01V 1 段の上下）で「低下している」と言わず「安定」と言うためである。
    # 値は、通常のホバリングがこの窓で実際に生む最悪値 0.120V より上に置く。
    # 理由は上の区分と同じで、通常の飛行は「低下」ではなく「安定」と読めるべき
    # だからである。以前の 0.10V はその最悪値のすぐ**下**にあり、健全な
    # ホバリングが 3 秒間「ゆるやかに低下」と報告される原因になっていた。
    battery_drop_gradual_v: float = 0.14

    # The load transient at takeoff is not a discharge, so the trend window
    # is not allowed to start inside it. Measured: the reading settles
    # within about a second of the motors reaching hover thrust, so a
    # sample older than this is only kept once a steadier one exists.
    # 離陸時の負荷による電圧降下は放電ではないため、傾向の窓をその中から
    # 始めさせない。実測では、モータがホバリング推力に達してから約 1 秒で
    # 読みは落ち着く。
    battery_settle_s: float = 3.0

    # ToF (ground distance sensor) plausibility [m]. Outside this the
    # reading is reported as unreliable rather than used.
    # ToF（対地距離センサ）の妥当範囲 [m]。外れた値は使わず「当てにならない」と報告する。
    tof_min_m: float = 0.02
    tof_max_m: float = 4.0

    # How stale a sample may be before the link counts as lost [s].
    # サンプルがこれ以上古くなったら通信断とみなす [s]。
    sample_timeout_s: float = 0.5

    # A classification must hold for this long before it REPLACES the one
    # being reported. Every band above has an edge, and a quantity sitting
    # on one crosses it back and forth at the sample rate: measured on the
    # `battery_drop` scene, the battery trend alternated 510 times in 60 s
    # because the window's oldest sample fell in and out of it each cycle.
    #
    # Each of those flips is a new situation signature, and the Arbiter
    # discards any answer whose signature changed while it was in flight
    # (arbiter.py), so flapping alone threw away 7 of 11 answers in the
    # `run nominal` flight on 2026-09-19. Holding a classification briefly
    # costs at most this much delay in reporting a genuine change, which is
    # well inside the 1 Hz at which the Judge is asked anyway.
    #
    # 区分は、この時間続いて初めて、報告中の区分を**置き換える**。上の区分には
    # どれも境目があり、境目上にある量はサンプル周期で行き来する。`battery_drop`
    # の場面での実測では、窓の最古のサンプルが毎周期出入りするため、電池の傾向が
    # 60 秒で 510 回入れ替わった。
    #
    # その 1 回ごとが新しい状況の指紋になり、Arbiter は「応答の往復中に指紋が
    # 変わった答え」を破棄する（arbiter.py）。そのため、ばたつきだけで
    # 2026-09-19 の `run nominal` は 11 件中 7 件の答えを捨てていた。区分を
    # 短く保持する代償は、本物の変化の報告がこの時間だけ遅れることだけであり、
    # どのみち Judge へ問うのは 1Hz なので、その内側に収まる。
    classification_hold_s: float = 1.0


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
class InstructionConfig:
    """How a spoken instruction becomes a sequence of moves (`sf pilot say`).

    Jev names the moves; every number here is applied by code. The model is
    documented not to be a calculator, so the amounts it may choose are
    named bands (small/medium/large) that this table maps to centimetres,
    and any figure the operator actually said is extracted by code instead.

    話した指示を動作の列に変える際の設定（`sf pilot say`）。

    動作に名前を付けるのは Jev だが、ここにある数値を当てるのはすべてコード
    である。モデルは計算機ではないと文書化されているため、モデルが選べる量は
    名前の付いた区分（small/medium/large）だけにし、その区分から cm への対応は
    この表が持つ。操作者が実際に言った数値は、代わりにコードが抜き出す。
    """

    # How many moves one instruction may contain. Six covers "go up, forward,
    # turn, forward, come back, land" -- a longer instruction is better split
    # by the operator than guessed at by the model, and every extra step is a
    # question in the request.
    # 1 つの指示に含められる動作の数。6 あれば「上がって・進んで・回って・
    # 進んで・戻って・降りる」を賄える。それより長い指示は、モデルに推測させる
    # より操作者に分けてもらうほうがよい。1 段増えるごとに質問が 1 問増える。
    max_steps: int = 6

    # Named distance bands [cm]. These are what `small` / `medium` / `large`
    # mean; they sit inside the vehicle's own 10-300 cm move range
    # (api_task.cpp kMoveMinCm / kMoveMaxCm).
    # 名前の付いた距離の区分 [cm]。`small`/`medium`/`large` の意味である。
    # 機体自身が受け付ける移動範囲 10〜300cm（api_task.cpp の kMoveMinCm /
    # kMoveMaxCm）の内側に収めてある。
    distance_small_cm: float = 20.0
    distance_medium_cm: float = 50.0
    distance_large_cm: float = 100.0

    # Named turn bands [deg]. / 名前の付いた旋回角の区分 [度]。
    turn_small_deg: float = 45.0
    turn_medium_deg: float = 90.0
    turn_large_deg: float = 180.0

    # The vehicle's own limits on a single move, so a step that the firmware
    # would refuse (`error out of range`) or silently clamp is caught here
    # instead, where the operator can be told which step was the problem.
    # 1 回の移動に対する機体自身の制限。ファームが拒否する（`error out of
    # range`）か黙ってクランプする手順を、どの手順が問題かを操作者に伝えられる
    # こちら側で捕まえる。
    move_min_cm: float = 10.0
    move_max_cm: float = 300.0

    # A step whose move is named with confidence below this is not flown.
    # Distinct from JudgeConfig.min_confidence: that gate governs a safety
    # action on a flight already under way, while this one governs whether
    # an instruction was understood at all, before anything moves.
    # この確信度に満たない動作の手順は飛ばさない。JudgeConfig.min_confidence
    # とは別物である: あちらは既に飛んでいる機体の安全行動の関門で、こちらは
    # 何かが動く前の「指示を理解できたか」の関門である。
    min_step_confidence: float = 0.6

    # The altitude an instruction starts from when the aircraft is on the
    # ground: what `takeoff` reaches before the first commanded move. The
    # envelope pre-check walks from here.
    # 地上から始まる指示の起点高度。最初の移動の前に `takeoff` が到達する高度で
    # ある。飛行領域の事前検査はここから積算する。
    takeoff_altitude_m: float = 0.5


@dataclass(frozen=True)
class LandingConfig:
    """How long to settle the aircraft before sending `land`.

    Why this exists at all: the firmware **stops holding horizontal
    position the moment a landing begins**, by design.
    `sf_controller_pid/pid_controller.cpp` excludes `VerticalPhase::Landing`
    from `computePositionHold()` so that the descent steers the same way in
    every mode (a POS_HOLD landing handles like STABILIZE). The consequence
    is that whatever horizontal velocity the craft carries INTO the descent
    is carried THROUGH it, unopposed: measured in SILS, a `land` sent
    straight after a move slides 0.45 m before touchdown, the same `land`
    after a 3 s pause slides 0.10 m, and a `land` from a genuinely
    stationary hover slides 0.000 m.

    So the aircraft is brought to rest before the landing is asked for.
    That is this package's work, not the firmware's: the firmware's
    behaviour follows from a design decision about how a descent should
    steer, and changing it is a vehicle-side judgement (see
    docs/plans/jev-autopilot.md §4.3).

    `land` を送る前に機体を静定させる時間の設定。

    これが存在する理由: ファームウェアは**着陸を始めた瞬間に水平の位置保持を
    やめる**。これは設計どおりである。`sf_controller_pid/pid_controller.cpp` は
    `computePositionHold()` から `VerticalPhase::Landing` を除外しており、降下の
    操縦則をモード間で統一するためである（POS_HOLD の着陸も STABILIZE と同じ
    操縦感になる）。その帰結として、降下に**持ち込んだ**水平速度は、妨げられる
    ことなく降下中も**持ち越される**。SILS での実測: 移動の直後に送った `land` は
    接地までに 0.45m 滑り、3 秒おいてからの同じ `land` は 0.10m、本当に静止した
    ホバリングからの `land` は 0.000m である。

    そこで、着陸を求める前に機体を止める。これは本パッケージの仕事であって
    ファームの仕事ではない。ファームの挙動は「降下をどう操縦させるか」という
    設計判断から従うものであり、それを変えるかどうかは機体側の判断事項である
    （docs/plans/jev-autopilot.md §4.3 参照）。
    """

    # The craft counts as at rest below this horizontal speed [m/s]. Same
    # figure P3 settled on for waiting between an instruction's steps, and
    # for the same reason -- it is the speed at which the remaining slide is
    # small compared with the landing accuracy anyone expects.
    # この水平速度 [m/s] を下回れば静止とみなす。P3 が手順の間の待ちに定めたのと
    # 同じ値で、理由も同じ — 残りの滑りが、誰もが期待する着陸精度に比べて
    # 小さくなる速度である。
    settle_speed_mps: float = 0.05

    # The speed must STAY below the threshold for this long. A single slow
    # reading is not rest: an approach crosses zero velocity as it overshoots
    # and turns back, so a one-shot check passes at exactly the moment the
    # craft is about to accelerate the other way (measured in P3).
    # 速度がこの時間だけ下回り続ける必要がある。1 回遅く読めただけでは静止では
    # ない。進入は行き過ぎて戻る際に速度 0 を通過するので、1 回きりの確認は
    # 「これから逆向きに加速する」まさにその瞬間に通ってしまう（P3 で実測）。
    settle_hold_s: float = 1.0

    # Ceiling on the ordinary settling wait [s]. A craft that is still being
    # pushed (a disturbance, a drifting estimate) would otherwise never read
    # as stopped and the landing would never be sent, which is worse than
    # landing with some speed left: the aircraft is in the air either way,
    # and waiting indefinitely spends the battery that makes a landing
    # possible at all.
    # 通常の静定待ちの上限 [s]。押され続けている機体（外乱・推定の流れ）は
    # いつまでも「止まった」と読めず、着陸が永遠に送られなくなる。速度を残して
    # 着陸するより悪い — どちらにせよ機体は空中にあり、待ち続けることは、着陸を
    # 可能にしている当の電池を使うからである。
    settle_max_s: float = 6.0

    # The same ceiling when the reason for landing cannot wait -- a battery
    # in the danger band, a diverged estimate. Short rather than zero: even
    # half a second of `stop` takes the worst of the approach speed off (the
    # 3 s pause above already recovered most of the 0.45 m slide), and a
    # dangerous battery still has this long.
    # 着陸の理由が待てない場合の同じ上限 — 電池の危険域、推定の発散。0 ではなく
    # 短くする。0.5 秒の `stop` でも進入速度の大部分は落ちる（上記の 3 秒の待ちは
    # 0.45m の滑りのほとんどを回収している）し、危険域の電池にもこれだけの余裕は
    # ある。
    urgent_settle_max_s: float = 0.5

    # How long to wait for an outstanding blocking move to answer before
    # overriding it with `stop` [s]. A `land` that interrupts a move leaves
    # the guidance target in place and the craft accelerates towards it
    # after touchdown begins, so the move is ended deliberately rather than
    # left hanging.
    # 実行中のブロックする移動の応答を待つ上限 [s]。これを過ぎたら `stop` で
    # 上書きする。移動の途中に割り込む `land` は誘導目標を残し、機体は降下開始後も
    # そこへ向かって加速する。そこで移動は放置せず、意図して終わらせる。
    move_reply_wait_s: float = 2.0


@dataclass(frozen=True)
class MissionConfig:
    """The limits a mission flies under, which no answer may override
    (`sf pilot mission`).

    Every value here exists because Jev, correctly, cannot see what it
    governs. The model is shown "the battery is running low" and "this leg
    has been redone twice"; it is not shown a percentage, a clock or a
    retry counter, and it is documented not to be a calculator. So the
    decision that follows from a COUNT or a DURATION is taken here, and
    Jev's answer is a proposal that this table can refuse.

    ミッションが従う上限（`sf pilot mission`）。どの答えもこれを覆せない。

    ここの値はいずれも、Jev には（当然ながら）それが支配するものが見えないから
    存在する。モデルに見えているのは「電池が残り少ない」「この区間は 2 回
    やり直した」という語であって、百分率でも時計でも計数器でもない。そもそも
    計算機ではないと文書化されている。したがって**回数**や**経過時間**から従う
    判断はこちらで決め、Jev の答えは、この表が却下しうる提案として扱う。
    """

    # How many times one leg may be redone before the code stops allowing
    # it. Two is enough to absorb a gust that pushed one approach off; a
    # leg that fails three times is failing for a reason repeating it will
    # not fix, and every retry costs battery the mission still needs.
    # 1 つの区間をやり直せる回数の上限。2 回あれば、1 回の進入を押し流した突風は
    # 吸収できる。3 回失敗する区間は、繰り返しても直らない理由で失敗している。
    # やり直しはそのたびに、ミッションがまだ必要とする電池を消費する。
    max_retries_per_leg: int = 2

    # The whole mission's ceiling [s], counted from the first leg. It is not
    # a per-leg timeout -- `say.py`'s StepRunner already bounds one step --
    # but a bound on a route that keeps holding and redoing its way through
    # the battery without ever finishing.
    # ミッション全体の上限 [s]。最初の区間から数える。区間ごとのタイムアウトでは
    # なく（1 手順の上限は既に say.py の StepRunner が持つ）、待機とやり直しを
    # 繰り返して終わらないまま電池を使い切る経路に対する上限である。
    time_limit_s: float = 180.0

    # How close to a leg's intended end point counts as having arrived [m].
    # Chosen to sit just outside the vehicle's own 0.15 m tolerance sphere
    # (api_task.cpp kReachRadiusM): inside that radius the firmware has
    # already declared the move reached, so calling it "stopped short"
    # would contradict the vehicle about its own move. The margin above it
    # covers the settling drift measured in P3.
    # 区間の意図した終点にどれだけ近ければ到達とみなすか [m]。機体自身の許容球
    # 0.15m（api_task.cpp の kReachRadiusM）のすぐ外側に置いた。その内側では
    # ファームが既に「到達した」と宣言しており、それを「手前で止まった」と
    # 呼ぶのは、機体自身の移動について機体と食い違うことになる。上乗せの余裕は
    # P3 で実測した静定中の流れを賄う。
    arrival_tolerance_m: float = 0.25

    # A leg's `go` speed [cm/s] when the route says `return_home`. Kept at
    # the envelope's own horizontal ceiling so a mission cannot fly faster
    # than a hand-written instruction may.
    # 経路が `return_home` と言うときの `go` の速度 [cm/s]。飛行領域自身の水平上限に
    # 合わせ、ミッションが手書きの指示より速く飛べないようにする。
    return_speed_cm_s: float = 50.0


@dataclass(frozen=True)
class SilsConfig:
    """How a `sf pilot run --sils` flight is staged and how scenes drive it.

    These are the numbers the SILS rehearsal needs and the flight itself
    does not, so they sit apart from the thresholds above: changing a scene
    must not be able to change what the Monitor considers a low battery.

    `sf pilot run --sils` の飛行の進め方と、場面の駆動に使う値。

    SILS での予行に必要で、飛行そのものには不要な数値である。場面を変えたことが
    Monitor の「電池残量が少ない」の判定を変えてしまわないよう、上のしきい値とは
    分けてある。
    """

    # Boot calibration must finish before an ARM (or an API `takeoff`) is
    # accepted — scenarios/api_flight.scn holds neutral sticks for 6 s for
    # this reason, and the same wait applies here.
    # 起動校正が終わるまで ARM（API の `takeoff` も）は受理されない。
    # scenarios/api_flight.scn が 6 秒間中立を保つのと同じ理由・同じ待ち時間。
    boot_settle_s: float = 6.0

    # After `takeoff`, the aircraft climbs to its own 0.5 m target and the
    # guidance holds it. Judging before it settles would classify a normal
    # climb as an altitude deviation.
    # `takeoff` の後、機体は自前の 0.5m 目標まで上昇し、誘導がそれを保つ。
    # 落ち着く前に判断させると、正常な上昇を高度の逸脱と区分してしまう。
    takeoff_settle_s: float = 8.0

    # Grace period for the vehicle's own descent after a `land` before the
    # emulator is asked to quit.
    # `land` の後、エミュレータに終了を求めるまで機体自身の降下を待つ時間。
    land_grace_s: float = 5.0

    # `battery_drop`: walk the reported pack voltage from here to here over
    # the flight, via the emulator's `vbatt` stdin verb. The end value sits
    # below MonitorConfig.battery_danger_pct's equivalent voltage so the
    # scene is guaranteed to reach a decision rather than merely approach
    # one; the start value is a healthy pack.
    # `battery_drop`: 報告されるパック電圧を、エミュレータの `vbatt` で飛行中に
    # ここからここまで下げていく。終端は MonitorConfig.battery_danger_pct に
    # 相当する電圧より下に置き、判断に「近づく」だけでなく必ず到達するようにして
    # ある。始端は健全なパックである。
    battery_scene_start_v: float = 4.05
    battery_scene_end_v: float = 3.35

    # `drift`: a steady sideways force [N] pushing the aircraft, and nothing
    # else. It is the Plant's existing wind hook (the *.scn `wind` event), so
    # this scene adds no new fault model.
    #
    # Why not also an under-reading optical flow: an earlier version of this
    # scene set SILS_EMU_FLOW_SCALE as well, on the reasoning that the wind
    # would otherwise be corrected away. That reasoning was never tested, and
    # the knob turns out not to reach the flight at all. `Config::flow_vel_scale`
    # is read only by `Plant::flow()`, which only the plant smoke test calls;
    # the flying path runs virtual_board.cpp -> sils_pmw3901's
    # `set_motion_from_velocity()`, which never sees the multiplier. Measured
    # 2026-09-19 over a 35 s flight at 0.060 N: 359/1388 cycles classified as
    # drifting without the knob versus 361/1389 with it at 0.35 -- the
    # difference is the run-to-run jitter of a real-time emulator, not an
    # effect. Wind alone is therefore the whole scene, and it is enough: the
    # same measurement recorded a peak excursion of 1.04 m at up to 0.635 m/s,
    # with 58 cycles reaching the "drifting fast" band, against 0/1385 cycles
    # for a no-wind control.
    #
    # `drift`: 機体を押す定常の横力 [N]、それだけである。Plant の既存の wind
    # フック（*.scn の `wind` 事象）であり、この場面のために新しい故障モデルは
    # 足していない。
    #
    # フローの過小読みを併用しない理由: 以前の版は「風だけでは位置保持が打ち
    # 消してしまう」という理屈で SILS_EMU_FLOW_SCALE も設定していた。その理屈は
    # 検証されておらず、しかもこのノブは飛行経路に届かない。
    # `Config::flow_vel_scale` を読むのは `Plant::flow()` だけで、それを呼ぶのは
    # plant の smoke 試験だけである。飛行時の経路は virtual_board.cpp →
    # sils_pmw3901 の `set_motion_from_velocity()` であり、この乗数を一切見ない。
    # 2026-09-19 に 0.060N・35 秒の飛行で実測: ノブ無しで 1388 周期中 359 周期が
    # 「流されている」と区分され、0.35 を付けると 1389 周期中 361 周期 — 差は
    # 実時間エミュレータの実行ごとのばらつきであって効果ではない。したがって
    # 風だけがこの場面の全てであり、それで足りる。同じ実測で最大変位 1.04m・
    # 最大速度 0.635m/s に達し、58 周期が「速く流されている」区分に入った
    # （風なしの対照は 1385 周期中 0 周期）。
    # RESOLVED (2026-09-19): an earlier note here recorded that the force,
    # declared in the Plant's NED frame, pushed the craft north in GROUND
    # TRUTH while the firmware's own estimate reported that motion on its
    # EAST axis. The cause was the SILS start attitude: the MuJoCo identity
    # quaternion is level but faces EAST (NED yaw=+90 deg, because MuJoCo's
    # world is ENU), while the firmware boots its estimator at yaw=0. The
    # start attitude is now level-and-NORTH, so the two agree -- re-measured
    # with `wind 0.02 0 0`: truth.csv's pos_x reached +0.147 m and posvel.csv's
    # pos_x reached +0.153 m, with pos_y identically 0 on both.
    #
    # 解決済み（2026-09-19）: 以前ここには、Plant の NED フレームで宣言した力が
    # 「真値」では機体を北へ押すのに、ファーム自身の推定は同じ運動を「東」軸に
    # 出す、と記していた。原因は SILS の初期姿勢だった。MuJoCo の単位
    # クォータニオンは水平だが東向き（NED yaw=+90°。MuJoCo の世界が ENU のため）
    # で、一方ファームは推定器を yaw=0 で起動していた。初期姿勢を「水平かつ北向き」
    # に直したので両者は一致する — `wind 0.02 0 0` で再実測: truth.csv の pos_x は
    # +0.147m、posvel.csv の pos_x は +0.153m に達し、pos_y は双方とも厳密に 0。
    # 0.06 N chosen by measurement, not by feel: at 0.02 N position hold
    # nulls the excursion almost at once (peak ~0.16 m, speeds below the
    # Monitor's "drifting" band, so nothing is ever classified as drift),
    # while at 0.06 N the craft swings out to ~0.9 m at 0.2-0.5 m/s before
    # the controller pulls it back -- a disturbance the controller is
    # visibly fighting, which is the situation worth judging.
    #
    # 0.06N は実測で選んだ（感覚ではない）: 0.02N では位置保持がほぼ即座に
    # 打ち消してしまい（最大約 0.16m、速度は Monitor の「流されている」区分に
    # 届かず、流れとして区分されない）、0.06N では制御が引き戻すまでに
    # 0.2〜0.5m/s で約 0.9m まで振れる — 制御が目に見えて抗っている外乱であり、
    # 判断する価値のある状況である。
    drift_wind_n: float = 0.060

    # `wall_ahead` / `dead_end`: obstacle geometry in NED [m], placed through
    # the emulator's `wall` stdin verb before the flight starts.
    #
    # The wall stands 1.0 m north of the takeoff point because that is inside
    # the forward part's reliable band (Plant tof_front_max_m = 2.0 m) while
    # leaving room to approach it: the craft starts stopping at the distance
    # `Monitor.stop_distance` computes, so a wall closer than that would
    # already be inside the stop band when the flight begins and nothing would
    # be tested. Width 2.0 m (±1.0 m either side) so a small heading error during
    # the approach does not let the beam slip past the end of the segment —
    # that would read as "the wall vanished" rather than as an approach.
    #
    # `wall_ahead` / `dead_end`: 障害物の配置（NED [m]）。飛行開始前にエミュレータの
    # `wall` で置く。
    #
    # 壁を離陸点の北 1.0m に置くのは、そこが前方の信頼帯域（Plant の
    # tof_front_max_m = 2.0m）の内側でありながら、近づく余地を残すからである。機体は
    # `Monitor.stop_distance` が算出する距離で止まり始めるので、それより近い壁は飛行
    # 開始時点で既に停止帯域の中にあり、何も試験されない。幅 2.0m（左右 ±1.0m）にするのは、接近中の
    # わずかな方位の誤差でビームが線分の端から外れないようにするためである ―― 外れれば
    # 「壁が消えた」と読めてしまい、接近としては読めない。
    wall_distance_m: float = 1.0
    wall_half_width_m: float = 1.0

    # `dead_end`: the same wall ahead, plus one side wall, leaving the OTHER
    # side open. The side wall runs from beside the craft out to the far wall
    # so the pocket is closed on three sides. Which side is closed is fixed
    # (east), not random: a scene that differs between runs cannot be the
    # basis of a repeatable measurement.
    # `dead_end`: 同じ正面の壁に加えて片側の壁を置き、「反対側」を開けておく。側壁は
    # 機体の横から奥の壁まで伸び、袋小路を三方で閉じる。閉じる側（東）は固定であって
    # 無作為ではない: 実行ごとに変わる場面は、再現できる測定の土台にならない。
    dead_end_side_m: float = 1.0


@dataclass(frozen=True)
class ForwardConfig:
    """Forward-distance thresholds: the immediate stop rule and the words.
    前方距離のしきい値: 即時停止則と、区分の語。

    Most numbers here are distances in metres from the forward sensor, banded
    as wall_near_m < somewhat_near_m. The Monitor classifies into words with
    these, and the immediate rule fires WITHOUT waiting for Jev.

    The distance the immediate rule fires at is NOT among them: it is computed
    per cycle from the closing speed (`Monitor.stop_distance`), because a fixed
    threshold cannot be right at two different speeds. What is stored here are
    the two terms it is built from, `safety_margin_m` and `coast_per_speed_s`,
    both measured. The history of that change is worth keeping in view: a fixed
    0.5 m threshold let the craft through the wall in 3 of 8 measured approaches
    (§4.9.7), not because the stop was late -- it was issued on the very cycle
    the classification changed -- but because `stop` restores position hold and
    does not brake, so the distance a stop needs depends on the speed it is
    issued at.

    ここの数値の多くは前方センサからの距離 [m] であり、
    wall_near_m < somewhat_near_m の順に並ぶ。Monitor はこれらで語に区分し、即時則は
    Jev を待たずに発火する。

    ただし**即時則が発火する距離はこの中に無い**。接近速度から周期ごとに計算する
    （`Monitor.stop_distance`）。固定のしきい値は、速度が違えば両方で正しくあり得ない
    からである。ここに置くのは、それを組み立てる 2 項 `safety_margin_m` と
    `coast_per_speed_s` であり、どちらも実測値である。この変更の経緯は見えるところに
    残しておく価値がある: 固定の 0.5m というしきい値は、実測した 8 回の接近のうち
    3 回で機体を壁の向こうへ通した（4.9.7 節）。停止が遅かったからではない ―― 区分が
    変わったまさにその周期に送られている ―― `stop` が戻すのは位置保持であって制動では
    なく、したがって停止に要る距離は、それが送られた時点の速度に依存するからである。
    """

    # The floor under the stopping distance: how much room to keep even when
    # the craft is barely moving. The stopping distance itself is
    # `safety_margin_m + coast_per_speed_s * closing_speed` (see below), so
    # this is what the rule reduces to at a standstill.
    #
    # 0.5 m is kept from the old fixed threshold deliberately. It was never
    # wrong as a MINIMUM -- the slowest measured approach (0.129 m/s) coasted
    # only 0.086 m and stopped 0.414 m clear -- it was wrong as the WHOLE rule,
    # because it did not grow with speed. Keeping it as the floor preserves the
    # behaviour that was already adequate at low speed and adds to it above.
    #
    # 停止距離の下限。ほとんど動いていないときでも空けておく余地である。停止距離
    # 自体は `safety_margin_m + coast_per_speed_s * 接近速度` なので（下記）、これは
    # 静止時に規則が帰着する値である。
    #
    # 0.5m を旧来の固定しきい値から意図して引き継ぐ。**下限**としては誤っていなかった
    # ためである —— 実測で最も遅い接近（0.129m/s）の惰走は 0.086m にすぎず、0.414m の
    # 余裕を残して止まっている。誤っていたのは、これを**規則の全体**にしたこと、つまり
    # 速度とともに伸びなかったことである。下限として残せば、低速で既に十分だった挙動は
    # そのままに、その上へ積み増すことになる。
    safety_margin_m: float = 0.5

    # The coast coefficient [s]: how much extra room one metre per second of
    # closing speed needs. Stopping distance = margin + this * closing speed.
    #
    # MEASURED, over the 8 SILS approaches of 2026-09-19 (truth.csv, recorded
    # in docs/plans/jev-autopilot.md §4.9.7). Each run gives a closing speed at
    # the moment the threshold was crossed and the distance travelled after it:
    #
    #     0.129 m/s -> 0.086 m      0.219 m/s -> 0.747 m
    #     0.155 m/s -> 0.390 m      0.236 m/s -> 0.920 m
    #     0.172 m/s -> 0.446 m      0.240 m/s -> 0.961 m
    #     0.181 m/s -> 0.355 m      0.347 m/s -> 0.167 m
    #
    # 4.0 s is the UPPER ENVELOPE of coast/speed over those points, not a
    # least-squares fit. That choice is the whole point, so it is worth saying
    # why: a least-squares line (coast = 0.84*v + 0.33, rms 0.31 m) sits
    # THROUGH the data, so by construction it under-predicts about half the
    # runs -- and an under-predicted stopping distance is a wall strike. The
    # envelope passes at or above all 8 (the binding point is 0.240 m/s ->
    # 0.961 m, giving 0.961/0.240 = 4.00), so every approach that has actually
    # been measured would have been stopped clear of the wall.
    #
    # Why not a*v**2 (a drag-like law, which a coasting airframe suggests):
    # fitted through the same points it is WORSE, under-predicting 6 of the 8
    # against the linear form's 3 (rms 0.41 m vs 0.31 m). The reason is visible
    # in the fixture: the coast is not a free glide decaying smoothly to rest.
    # In wall_approach_states.jsonl the speed falls 0.214 -> 0.132 m/s and then
    # RE-ACCELERATES to 0.185 m/s, because `stop` restores position hold while
    # the blocking `forward` move's own position target is still pulling the
    # craft on. What is being modelled is therefore a control interaction, not
    # aerodynamic drag, and a drag law has no claim on it. The linear envelope
    # is used because it bounds the measurements, not because it explains them.
    #
    # The scatter is wide (coast/speed runs 0.48-4.00 over the 8) for that same
    # reason, which is why the envelope rather than the centre is the only safe
    # reading of this data. Re-measure and revisit this figure if the way a
    # move is ended changes.
    #
    # 惰走係数 [s]。接近速度 1m/s あたり、どれだけ余分に空けるか。
    # 停止距離 = 余裕 + これ × 接近速度。
    #
    # **実測値である。** 2026-09-19 の SILS 接近 8 回（truth.csv。
    # docs/plans/jev-autopilot.md 4.9.7 節に記録）による。各実行は、しきい値を
    # 通過した時点の接近速度と、その後に進んだ距離を与える（上表）。
    #
    # 4.0s は、それらの点に対する coast/速度 の**上側の覆い**であって、最小二乗の
    # あてはめではない。この選択こそが要点なので、理由を述べる: 最小二乗の直線
    #（coast = 0.84v + 0.33、rms 0.31m）はデータの**真ん中**を通るので、構造上
    # ほぼ半数の実行を過小に見積もる —— そして停止距離の過小見積もりとは、壁への
    # 衝突のことである。この覆いは 8 点すべてを下回らない（拘束点は 0.240m/s → 0.961m で、
    # 0.961/0.240 = 4.00）ので、これまでに実測されたどの接近も壁の手前で止まっていた
    # ことになる。
    #
    # a*v² にしない理由（惰走する機体からは、抗力に似た法則が示唆される）: 同じ点に
    # あてはめると、むしろ**悪い**。線形形の 3 件に対し 8 件中 6 件を過小に見積もる
    #（rms 0.41m 対 0.31m）。理由は fixture に見えている。惰走は、滑らかに静止へ
    # 減衰する自由滑走ではない。wall_approach_states.jsonl では速度が 0.214 →
    # 0.132m/s と落ちた後、0.185m/s へ**再加速**する。`stop` が戻すのは位置保持で
    # ある一方、ブロックする `forward` の移動が持つ位置目標が、なお機体を前へ
    # 引いているためである。したがってここで模型化しているのは制御の相互作用で
    # あって空力抗力ではなく、抗力の法則にはこれを説明する資格が無い。線形の覆いを
    # 使うのは、それが実測を**覆う**からであって、説明するからではない。
    #
    # ばらつきが大きいこと（8 点で coast/速度 は 0.48〜4.00）も理由は同じであり、
    # だからこそ中心ではなくこの覆いだけが、このデータの安全な読み方である。移動の
    # 終わらせ方を変えたときは、測り直してこの値を見直すこと。
    coast_per_speed_s: float = 4.0

    # The ceiling on the stopping distance [m]. Without one the rule scales
    # past anything the forward part can actually see: at the envelope's
    # 0.5 m/s ceiling the distance would be 2.5 m, which is beyond the Plant's
    # own reliable band (tof_front_max_m = 2.0 m). A rule that fires on a
    # distance the sensor cannot report is a rule that never fires, so it is
    # clamped here and the SPEED is limited instead (`EnvelopeConfig`).
    # 停止距離の上限 [m]。無ければ、前方の部品に実際に見える範囲を超えて伸びる。
    # 飛行領域の上限 0.5m/s では 2.5m になり、Plant 自身の信頼帯域
    #（tof_front_max_m = 2.0m）の外である。センサが報告できない距離で発火する規則
    # とは、発火しない規則のことなので、ここで頭打ちにし、代わりに**速度**のほうを
    # 制限する（`EnvelopeConfig`）。
    stop_distance_max_m: float = 1.8

    # Hysteresis: once stopped, the reading must come back out past the
    # stopping distance PLUS this before "wall near" is withdrawn. Without a
    # gap the classification chatters at the boundary, and a chattering stop
    # rule is a craft that alternately brakes and accelerates at a wall.
    #
    # Expressed as a margin ON TOP of the stopping distance rather than as an
    # absolute distance, because the stopping distance now moves with speed: a
    # fixed 0.7 m release sat above the old fixed 0.5 m stop, but would sit
    # BELOW the stopping distance at any closing speed past about 0.05 m/s,
    # which inverts the hysteresis -- the craft would release the stop while
    # still inside the band that caused it.
    #
    # ヒステリシス: 一度止まったら、読み値が「停止距離＋この余裕」まで戻らない限り
    #「壁が近い」を取り下げない。差を設けないと区分は境界でばたつき、ばたつく停止則
    # とは、壁の前で制動と加速を交互に繰り返す機体のことである。
    #
    # 絶対距離ではなく停止距離への**上乗せ**で表すのは、停止距離が速度とともに動く
    # ようになったためである。固定の 0.7m は旧来の固定 0.5m の上にはあったが、接近
    # 速度が約 0.05m/s を超えれば停止距離を**下回る**。それはヒステリシスの反転で
    # あり、機体は停止の原因となった帯域の中にいるまま停止を解除してしまう。
    release_margin_m: float = 0.2

    # The word bands above the stop threshold.
    # 停止しきい値より上の、語の帯域。
    wall_near_m: float = 0.8
    somewhat_near_m: float = 1.5

    # How long an invalid forward reading stays "the last thing we saw" before
    # it becomes "unknown". An invalid reading is genuinely ambiguous -- empty
    # space and a surface too close both produce it (§4.8.6) -- so the recent
    # past is the only thing that distinguishes them. Two seconds is about
    # sixty 30Hz samples: long enough that a handful of dropped readings do
    # not erase a wall, short enough that a stale wall does not outlive a turn.
    # 無効な前方の読み値が「最後に見たもの」で居続け、やがて「不明」になるまでの時間。
    # 無効は本当に曖昧である ―― 空間も、近すぎる面も、どちらも無効を生む（§4.8.6）――
    # ので、両者を分けられるのは直前の履歴だけである。2 秒は 30Hz の約 60 サンプルで、
    # 数個の取りこぼしで壁が消えない程度に長く、古い壁が旋回を越えて残らない程度に短い。
    invalid_grace_s: float = 2.0

    # -- backing away when a stop was not enough / 停止で足りないときの後退 --
    #
    # `stop` is not braking, so a craft that crossed the threshold fast keeps
    # closing for a while after it (the whole reason the figures above exist).
    # If it is STILL closing once position hold has had time to take effect,
    # the stop did not do its job and the only move left that increases the
    # distance is to go backwards.
    #
    # `stop` は制動ではないので、速く進入した機体はその後もしばらく詰め続ける
    #（上の数値が存在する理由そのものである）。位置保持が効くだけの時間が経っても
    # なお詰まり続けているなら、停止は仕事をしなかったのであり、距離を増やす手段と
    # して残っているのは後退だけである。

    # How long to let position hold work before judging that it did not. The
    # measured re-acceleration in wall_approach_states.jsonl plays out over
    # about 1.5 s, so a shorter window would call a stop failed while it was
    # still taking effect; a longer one spends the distance it is protecting.
    # 位置保持が効くのを待つ時間。効かなかったと判ずる前に、これだけ待つ。
    # wall_approach_states.jsonl で実測した再加速はおよそ 1.5 秒かけて起きるので、
    # これより短い窓は、まだ効いている最中の停止を「失敗」と呼んでしまう。長ければ、
    # 守ろうとしている当の距離を費やすことになる。
    backoff_grace_s: float = 1.5

    # How much closer it must have got during that window to count as still
    # closing. Above the forward part's own resolution, so sensor noise on a
    # craft that is actually stationary never triggers a retreat.
    # その窓のあいだにどれだけ詰まれば「まだ詰まっている」とみなすか。前方の部品
    # 自身の分解能より大きくし、実際には静止している機体のセンサ雑音が後退を
    # 引き起こさないようにする。
    backoff_closing_m: float = 0.05

    # How far one retreat moves the craft [cm]. One `back`, not a continuous
    # retreat: the craft is next to something it can only see one bearing of,
    # and reversing indefinitely trades a known obstacle ahead for an unknown
    # one behind. A single hop buys room for the next cycle to judge again.
    # 1 回の後退で動く距離 [cm]。連続後退ではなく `back` 1 回である。機体は、
    # 1 方位しか見えない何かの脇にいるのであり、無制限に後退することは、既知の
    # 前方の障害物を未知の後方の障害物と取り替えることである。1 回の後退は、次の
    # 周期が改めて判断するための余地を買う。
    backoff_step_cm: float = 20.0

    # How far the craft drifts FORWARD during a descent, with nothing
    # opposing it [m]. Used to decide how far to back off before landing
    # near a wall (`landing.LandingApproach._retreat_needed_cm`).
    #
    # This is not the stop rule's coast: it is a separate mechanism with a
    # separate cause. The firmware deliberately holds NO horizontal position
    # while descending (LandingConfig explains why), so a descent that
    # begins facing a wall travels towards it for the whole descent.
    #
    # MEASURED over the 13-flight SILS campaign of 2026-09-19: the craft was
    # stopped 0.28-0.43 m clear of the wall at cruise altitude by the stop
    # rule, and the descent alone then carried it to within 0.02-0.27 m,
    # accelerating to 0.26 m/s on the way down. The worst case was 0.72 m of
    # forward travel between the start of the descent and touchdown, and
    # this figure is set just above it.
    #
    # 降下中に機体が、妨げられることなく**前へ**流れる距離 [m]。壁の近くで着陸
    # する際、どれだけ下がるかの判断に使う
    #（`landing.LandingApproach._retreat_needed_cm`）。
    #
    # これは停止則の惰走とは**別物**であり、原因も別である。ファームは降下中、
    # 水平位置を意図して一切保持しない（理由は LandingConfig にある）ので、壁を
    # 向いて始まった降下は、降下のあいだずっとそちらへ進む。
    #
    # **実測値である。** 2026-09-19 の SILS 13 回による: 停止則によって巡航高度では
    # 壁から 0.28〜0.43m 離れて止まっていた機体が、降下だけで 0.02〜0.27m まで詰め、
    # 降下中に 0.26m/s まで加速した。最悪の場合、降下の開始から接地までの前進は
    # 0.72m であり、この値はそのすぐ上に置いてある。
    descent_drift_m: float = 0.75


@dataclass(frozen=True)
class ExploreConfig:
    """Sweeping the forward distance around a turn, to choose a heading.

    **This is off by default and must stay off until the release condition
    below is met.** Every number here is implemented, tested against
    fixtures and reachable, but the manoeuvre it describes cannot be flown:
    in SILS today the aircraft FALLS OUT OF THE AIR when it yaws at all.

    The measurement (docs/plans/jev-autopilot.md §4.9.1): from a 0.5 m
    hover, `cw 90`, `cw 45` and `cw 30` each reach the ground in about 1.3 s
    with a 4.7-5.3 G impact and a disarm, and an `rc` yaw rate as low as
    0.1 rad/s sinks just the same. A level `rc` and a plain hover hold
    altitude to within 2 cm, so this is specific to the yaw axis. The cause
    is the mixer clamping each motor's duty independently and never
    redistributing what it cut off the top, which loses mean lift
    (simulation-policy.md backlog #12); three candidate fixes were measured
    and none was adopted (§4.10).

    **Release condition: a `cw 90` from a 0.5 m hover completes with no
    fall, no impact detection and no disarm.** Climbing first so the ground
    is out of reach does NOT count -- that hides the fall rather than
    fixing it, and this package's rule is not to fly around a defect it can
    see (§4.3). When that condition is met, set `enabled = True` here and
    fly `sf pilot explore --sils --scene dead_end` to completion.

    旋回しながら前方距離を掃き、進む方位を選ぶための設定。

    **既定では無効であり、下記の解除条件が満たされるまで無効のままにする。**
    ここの数値はすべて実装・fixture で試験済みで到達可能だが、記述している
    操作そのものが**飛ばせない**。現状の SILS では、機体はヨー回転しただけで
    落下する。

    実測（docs/plans/jev-autopilot.md §4.9.1）: 0.5m のホバリングから `cw 90`・
    `cw 45`・`cw 30` はいずれも約 1.3 秒で接地し、4.7〜5.3G の衝撃と解除に至る。
    `rc` のヨー速度を 0.1 rad/s まで落としても同じように沈下する。水平の `rc` と
    素のホバリングは高度を 2cm 以内に保つので、これはヨー軸に固有である。原因は、
    ミキサーが各モータの duty を独立に切り詰め、上側で切り落とした分を再配分し
    ないことで、平均揚力が失われる（simulation-policy.md 改修バックログ #12）。
    是正案 3 件は実測済みで、いずれも不採用である（§4.10）。

    **解除条件: 高度 0.5m のホバリングからの `cw 90` が、落下・衝撃検出・解除の
    いずれも起こさずに完了すること。** 先に上昇して地面に届かなくする回避は
    条件に**含めない** —— それは落下を隠すのであって直すことではなく、見えている
    欠陥を迂回して飛ばないのが本パッケージの方針である（§4.3）。条件が満たされた
    ら、ここの `enabled` を True にし、`sf pilot explore --sils --scene dead_end`
    を完走させる。
    """

    # The one switch. Everything else here is inert while this is False.
    # 唯一の切り替え。これが False の間、他の値はすべて働かない。
    enabled: bool = False

    # Why it is off, shown to the operator verbatim when a sweep is asked
    # for. Kept here rather than in the CLI so that whoever flips the switch
    # reads the reason in the same place as the switch.
    # 無効である理由。掃引を求められたとき、そのまま操作者に表示する。切り替えと
    # 同じ場所で理由を読めるよう、CLI ではなくここに置く。
    disabled_reason: str = (
        "ヨー回転による探索は実装済みだが無効にしてある。現状の SILS では、高度 "
        "0.5m から `cw 90`・`cw 45`・`cw 30` のいずれを送っても機体が約 1.3 秒で "
        "接地し、衝撃検出と解除に至る（jev-autopilot.md §4.9.1 の実測）。原因は "
        "ミキサーの独立クランプによる揚力損失で（simulation-policy.md 改修バック"
        "ログ #12）、是正案 3 件はいずれも不採用（§4.10）。解除条件は「高度 0.5m "
        "のホバリングからの `cw 90` が、落下・衝撃検出・解除のいずれも起こさずに"
        "完了すること」であり、高度を上げて地面に届かなくする回避は含めない。"
        "満たされたら config.py の ExploreConfig.enabled を True にすること。"
        " / Exploration by yawing is implemented but disabled: in SILS today "
        "the aircraft reaches the ground about 1.3 s after any yaw command "
        "from a 0.5 m hover. See docs/plans/jev-autopilot.md §4.11."
    )

    # How far the craft turns between readings [deg], and how many readings
    # make one sweep. 45 deg x 8 closes the circle exactly, which is what
    # makes "the direction it started facing" the last bearing rather than a
    # bearing the sweep never visited.
    # 読み取りの間に機体が回る角度 [度] と、1 回の掃引を構成する読み取り数。
    # 45 度 × 8 で円がちょうど閉じる。これにより「出発時に向いていた方位」が、
    # 掃引が訪れなかった方位ではなく最後の方位になる。
    step_deg: float = 45.0
    bearings: int = 8

    # How long to let the craft settle after a turn before believing the
    # forward reading. A turn leaves the airframe rotating and swinging, and
    # a distance read during that is a distance to whatever the beam swept
    # past, not to what lies along the new bearing.
    # 旋回の後、前方の読み値を信じる前に機体を静定させる時間。旋回の直後は機体が
    # まだ回り、振れている。その間に読んだ距離は、新しい方位の先にあるものまでの
    # 距離ではなく、ビームが通り過ぎた何かまでの距離である。
    settle_s: float = 1.5

    # How many readings to take at each bearing, and how long to wait
    # between them. Several rather than one because the forward part
    # reports an invalid reading intermittently (§4.8.6), and one invalid
    # sample must not be able to call an open corridor "cannot measure".
    # 各方位で取る読み取りの数と、その間隔。1 回ではなく複数にするのは、前方の
    # 部品が断続的に無効を返すためであり（§4.8.6）、無効なサンプル 1 つで、開けた
    # 通路を「測定不能」と呼べてしまわないようにするためである。
    samples_per_bearing: int = 5
    sample_interval_s: float = 0.1

    # How many of those readings must be valid before the bearing is
    # classified from them at all. Below this the bearing is "cannot be
    # measured", which is never read as open (monitor.py's FORWARD_UNKNOWN
    # note: an invalid reading means empty space OR a surface too close).
    # その読み取りのうち、方位を区分するのに最低限必要な有効な数。これに満たない
    # 方位は「測定不能」であり、決して「開けている」とは読まない（monitor.py の
    # FORWARD_UNKNOWN の注記 —— 無効は「空間」と「近すぎる面」の両方を意味する）。
    min_valid_samples: int = 2

    # How long a sweep's result stays current [s]. A sweep describes where
    # the walls were when it was taken; after the craft has moved, that is
    # history. The word for it reaches Jev (`surroundings.measured`) rather
    # than the sweep being silently discarded, because "the last look was a
    # while ago" is a reason for caution the model can weigh.
    # 掃引の結果が最新とみなされる時間 [s]。掃引が述べるのは「それを取った時点で
    # 壁がどこにあったか」であり、機体が動いた後ではそれは履歴である。黙って捨てず、
    # その旨の語を Jev へ渡す（`surroundings.measured`）。「最後に見たのは少し前だ」
    # は、モデルが重み付けできる警戒の理由だからである。
    freshness_s: float = 20.0

    # Whether to turn with the blocking `cw`/`ccw` verbs or with an `rc` yaw
    # rate. `cw` is the default because it is the vehicle's own move to a
    # yaw TARGET and it answers when it arrives, so the sweep knows the
    # craft is actually pointing where it thinks. An `rc` rate would have to
    # be integrated on this side, which is dead reckoning over a link with
    # no delivery guarantee.
    #
    # Both fall over today for the same reason (§4.9.1 measured both), so
    # this chooses between two manoeuvres that are equally unflyable; it
    # exists so that whoever lifts the restriction can try the other one
    # without editing the sweep.
    #
    # 旋回に、ブロックする `cw`/`ccw` を使うか、`rc` のヨー速度を使うか。既定を
    # `cw` にするのは、それが機体自身の「ヨー**目標**への移動」であり、到達時に
    # 応答するからである。掃引は、機体が実際に思ったほうを向いていると分かる。
    # `rc` の速度ならこちら側で積分することになり、到達の保証が無いリンクの上での
    # 推測航法になる。
    #
    # 現状はどちらも同じ理由で破綻するので（§4.9.1 は両方を実測）、これは等しく
    # 飛ばせない 2 つの操作のどちらを選ぶかでしかない。制限を解く人が、掃引に手を
    # 触れずにもう一方を試せるようにするために置いてある。
    turn_with_rc: bool = False

    # The yaw rate used when `turn_with_rc` is set [rad/s], and the `rc`
    # unit it is sent in. The vehicle's fourth `rc` channel is -100..100
    # mapping to +/- its own yaw rate ceiling, so the rate is converted here
    # rather than the sweep carrying a stick figure.
    # `turn_with_rc` のときに使うヨー速度 [rad/s] と、それを送る `rc` の単位。
    # 機体の `rc` 第 4 チャンネルは -100〜100 で、機体自身のヨー速度上限に対応する。
    # そこで変換はここで行い、掃引がスティックの数値を持たないようにする。
    rc_yaw_rate_rad_s: float = 0.3
    rc_yaw_rate_max_rad_s: float = 1.0


@dataclass(frozen=True)
class PilotConfig:
    """The whole configuration, passed as one object.
    設定一式。1 つのオブジェクトとして受け渡す。"""

    judge: JudgeConfig = field(default_factory=JudgeConfig)
    envelope: EnvelopeConfig = field(default_factory=EnvelopeConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    arbiter: ArbiterConfig = field(default_factory=ArbiterConfig)
    instruction: InstructionConfig = field(default_factory=InstructionConfig)
    landing: LandingConfig = field(default_factory=LandingConfig)
    mission: MissionConfig = field(default_factory=MissionConfig)
    sils: SilsConfig = field(default_factory=SilsConfig)
    forward: ForwardConfig = field(default_factory=ForwardConfig)
    explore: ExploreConfig = field(default_factory=ExploreConfig)

    monitor_hz: float = MONITOR_HZ
    executor_rc_hz: float = EXECUTOR_RC_HZ
    judge_periodic_hz: float = JUDGE_PERIODIC_HZ


DEFAULT_CONFIG = PilotConfig()
