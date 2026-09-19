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
    # ある。包絡の事前検査はここから積算する。
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
    # 経路が `return_home` と言うときの `go` の速度 [cm/s]。包絡自身の水平上限に
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

    monitor_hz: float = MONITOR_HZ
    executor_rc_hz: float = EXECUTOR_RC_HZ
    judge_periodic_hz: float = JUDGE_PERIODIC_HZ


DEFAULT_CONFIG = PilotConfig()
