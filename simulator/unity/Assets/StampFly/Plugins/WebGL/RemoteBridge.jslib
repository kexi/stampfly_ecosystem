/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Kouhei Ito
 *
 * Part of StampFly Ecosystem (Unity simulator — the page's side of sf unity).
 * https://github.com/M5Fly-kanazawa/stampfly_ecosystem
 *
 * The browser half of the contract in docs/commands/sf-unity.md §8: ask
 * /api/hello once for this run's run_id, long-poll /api/cmd/next for commands,
 * answer on /api/cmd/result, and batch log lines to /api/log. A page whose
 * /api/hello goes unanswered is not served by `sf unity serve` (the public
 * site), and everything here stays quiet except window.stampfly.
 *
 * docs/commands/sf-unity.md §8 の約束のブラウザ側。/api/hello を 1 回呼んで
 * この実行の run_id を受け取り、/api/cmd/next で命令を待ち、/api/cmd/result に
 * 返し、ログの行をまとめて /api/log へ送る。/api/hello に応えが無いページは
 * `sf unity serve` が配信していない（公開サイト）ので、window.stampfly 以外は
 * 何もしない。
 *
 * Everything lives inside the $SfuRemote object because only the object and the
 * functions are emitted into the player; a plain top-level `var` in a .jslib is
 * dropped (simulator/unity/README.md).
 * 全てを $SfuRemote オブジェクトの中に置く。プレイヤーへ出るのはオブジェクトと
 * 関数だけで、.jslib の素の大域 `var` は落とされるためである
 * （simulator/unity/README.md）。
 */

var SfuRemoteLibrary = {

  $SfuRemote: {
    // How the page reached this run's identity.
    // このページがこの実行の素性をどう得たか。
    MODE_UNKNOWN: 0,   // /api/hello has not answered yet / まだ応えが無い
    MODE_LOCAL: 1,     // served by sf unity serve / sf unity serve が配信
    MODE_PUBLIC: 2,    // no local server; the page issued its own run_id / 自前

    mode: 0,
    runId: '',
    serverVersion: '',

    // The C# object SendMessage delivers a command to, and the method name.
    // Set by SfuRemoteStart so the names live in one place.
    // SendMessage が命令を届ける C# の対象と、そのメソッド名。名前を 1 か所に
    // まとめるため SfuRemoteStart が設定する。
    target: '',
    commandMethod: '',

    // Lines waiting to be sent, and the ones a failed request must keep so a
    // retry does not lose them.
    // 送信待ちの行と、失敗した要求が保持して再送で失わないようにする行。
    queue: [],
    sending: false,
    flushTimer: null,
    dropped: 0,

    // The lines window.stampfly.logs() returns, newest last. Kept as the raw
    // JSON text of each line so no re-serialization can change what was logged.
    // window.stampfly.logs() が返す行。新しいものが後ろ。記録したものが直列化で
    // 変わらないよう、各行の JSON の文字列のまま持つ。
    ring: [],
    RING_LIMIT: 2000,

    // The last per-tick trace C# handed over, as JSON Lines. Held so a page
    // whose POST failed, or one with no server at all, can still give it up
    // through window.stampfly.trace() / .downloadTrace().
    // C# が直近に渡した刻みごとのトレース（JSON Lines）。送信に失敗したページや
    // サーバの無いページからも window.stampfly.trace() ／ .downloadTrace() で
    // 取り出せるよう保持する。
    lastTrace: '',

    // Commands the page raised itself (window.stampfly.command) wait here for
    // C# to answer them; the server's commands are answered over HTTP instead.
    // ページ自身が起こした命令（window.stampfly.command）は、C# の答えをここで
    // 待つ。サーバの命令は HTTP で返す。
    localPending: {},

    // How many lines, or how long, before a batch goes out. §8 asks for tens of
    // lines or a few hundred milliseconds -- never one request per line.
    // 何行、あるいはどれだけの時間でまとめて送るか。§8 は数十行または数百ミリ秒
    // と定める。1 行 1 要求にはしない。
    BATCH_LINES: 40,
    BATCH_MS: 300,

    // The most lines the queue holds before the oldest are thrown away. A page
    // that has lost its server must not grow without bound; the loss itself is
    // reported as one `log.dropped` line.
    // 待ち行列が保持する行数の上限。サーバを失ったページが際限なく膨らんでは
    // ならない。失ったこと自体は `log.dropped` の 1 行で報告する。
    QUEUE_LIMIT: 4000,

    // What /api/cmd/next is asked to wait, and how long before a failed poll is
    // retried. §8 caps the wait at 60 s and counts a page connected for 70 s
    // after its last poll, so 25 s leaves ample room to re-poll in time.
    // /api/cmd/next に頼む待ち時間と、失敗した待ち受けを繰り返すまでの間隔。
    // §8 は待ちの上限を 60 秒、直近の待ち受けから 70 秒を接続中としているので、
    // 25 秒なら待ち直しは十分間に合う。
    POLL_WAIT_S: 25,
    POLL_RETRY_MS: 2000,

    polling: false,

    /**
     * Send one JSON request to the local server. Returns a Promise of the
     * decoded body, or null when the request did not land -- callers decide
     * whether that means "no server" or "try again".
     * ローカルサーバへ JSON の要求を 1 つ送る。復号した本文の Promise を返す。
     * 届かなかったときは null で、それが「サーバが無い」なのか「もう一度」なの
     * かは呼び出し側が決める。
     */
    request: function (path, body) {
      var options = { method: body === undefined ? 'GET' : 'POST' };
      if (body !== undefined) {
        options.headers = { 'Content-Type': 'application/json' };
        options.body = JSON.stringify(body);
      }

      return fetch(path, options).then(function (response) {
        return response.json();
      }).catch(function () {
        return null;
      });
    },

    /**
     * Put a line in the queue and send the batch when it is full. Dropping the
     * oldest keeps the newest lines, which are the ones that explain what the
     * page is doing now.
     * 行を待ち行列へ入れ、いっぱいになったらまとめて送る。古いものから捨てる
     * ことで新しい行を残す。いまページが何をしているかを語るのはそちらである。
     */
    enqueue: function (line) {
      SfuRemote.queue.push(line);

      var overflowed = SfuRemote.queue.length > SfuRemote.QUEUE_LIMIT;
      if (overflowed) {
        var excess = SfuRemote.queue.length - SfuRemote.QUEUE_LIMIT;
        SfuRemote.queue.splice(0, excess);
        SfuRemote.dropped += excess;
      }

      if (SfuRemote.queue.length >= SfuRemote.BATCH_LINES) {
        SfuRemote.flush();
        return;
      }

      SfuRemote.scheduleFlush();
    },

    /** Send whatever is queued after BATCH_MS. / BATCH_MS 後に待ち行列を送る。 */
    scheduleFlush: function () {
      if (SfuRemote.flushTimer !== null) { return; }
      SfuRemote.flushTimer = setTimeout(function () {
        SfuRemote.flushTimer = null;
        SfuRemote.flush();
      }, SfuRemote.BATCH_MS);
    },

    /**
     * Post the queued lines. On failure the batch goes back to the front of the
     * queue so a retry sends it again; a line is lost only to the queue limit,
     * never to one failed request.
     * 待ち行列の行を送る。失敗したらそのまとまりを待ち行列の先頭へ戻し、再送で
     * もう一度送る。行が失われるのは待ち行列の上限によってだけで、1 回の失敗に
     * よってではない。
     */
    flush: function () {
      var isIdle = SfuRemote.mode !== SfuRemote.MODE_LOCAL
                || SfuRemote.sending
                || SfuRemote.queue.length === 0;
      if (isIdle) { return; }

      var batch = SfuRemote.queue;
      SfuRemote.queue = [];
      SfuRemote.sending = true;

      SfuRemote.request('/api/log', { lines: batch }).then(function (answer) {
        SfuRemote.sending = false;
        if (answer && answer.ok) {
          if (SfuRemote.queue.length > 0) { SfuRemote.scheduleFlush(); }
          return;
        }

        SfuRemote.queue = batch.concat(SfuRemote.queue);
        SfuRemote.scheduleFlush();
      });
    },

    /**
     * Send what is left when the tab goes away. sendBeacon is the only request
     * a closing document is guaranteed to finish, and it takes a Blob rather
     * than headers, so the type is set on the Blob itself.
     * タブが閉じるときに残りを送る。閉じていく文書が終えられると保証された要求は
     * sendBeacon だけである。ヘッダではなく Blob を受けるので、種別は Blob 自身に
     * 設定する。
     */
    beacon: function () {
      var nothingToSend = SfuRemote.mode !== SfuRemote.MODE_LOCAL
                       || SfuRemote.queue.length === 0;
      if (nothingToSend) { return; }

      var body = new Blob([JSON.stringify({ lines: SfuRemote.queue })],
                          { type: 'application/json' });
      var sent = navigator.sendBeacon('/api/log', body);
      if (sent) { SfuRemote.queue = []; }
    },

    /**
     * Wait for the next command and hand it to C#. Re-polls immediately after
     * every answer, including the empty one, so the server never sees the gap
     * that would mark this page as gone.
     * 次の命令を待ち、C# へ渡す。空の答えを含め、返ってきたら直ちに待ち直す。
     * サーバがこのページを去ったと見なす間隔を作らないためである。
     */
    poll: function () {
      if (SfuRemote.mode !== SfuRemote.MODE_LOCAL) { return; }

      SfuRemote.polling = true;
      SfuRemote.request('/api/cmd/next?wait=' + SfuRemote.POLL_WAIT_S)
        .then(function (answer) {
          if (answer && answer.command) {
            SfuRemote.deliver(answer.cmd_id, answer.command, answer.args || {});
          }

          var failed = answer === null;
          setTimeout(SfuRemote.poll, failed ? SfuRemote.POLL_RETRY_MS : 0);
        });
    },

    /**
     * Give one command to C#. The payload is one JSON string because
     * SendMessage carries a single argument; C# answers through
     * SfuRemoteAnswer, which is why the cmd_id travels with it.
     * 命令を 1 つ C# へ渡す。SendMessage は引数を 1 つしか運べないので、
     * 中身は JSON の文字列 1 本にする。C# は SfuRemoteAnswer で返すため、
     * cmd_id を一緒に運ぶ。
     */
    deliver: function (cmdId, command, args) {
      var payload = JSON.stringify({ cmd_id: cmdId, command: command, args: args });
      if (typeof SendMessage === 'function' && SfuRemote.target) {
        SendMessage(SfuRemote.target, SfuRemote.commandMethod, payload);
        return;
      }

      SfuRemote.answer(cmdId, false, null, 'the page is not ready for commands');
    },

    /**
     * Return one command's result: to the server when it came from there, and
     * to the waiting Promise when window.stampfly.command raised it.
     * 命令 1 つの結果を返す。サーバから来たものはサーバへ、
     * window.stampfly.command が起こしたものは待っている Promise へ。
     */
    answer: function (cmdId, ok, data, error) {
      var waiting = SfuRemote.localPending[cmdId];
      if (waiting) {
        delete SfuRemote.localPending[cmdId];
        if (ok) { waiting.resolve(data); } else { waiting.reject(new Error(error)); }
        return;
      }

      if (SfuRemote.mode !== SfuRemote.MODE_LOCAL) { return; }

      var body = { cmd_id: cmdId, ok: ok };
      if (ok) { body.data = data; } else { body.error = error; }
      SfuRemote.request('/api/cmd/result', body);
    },

    /**
     * The page's own entry point, the same one `sf unity cmd` reaches through
     * the server. Returns a Promise so a check in the browser can await the
     * result rather than poll for it.
     * ページ自身の入口。`sf unity cmd` がサーバ経由で届くのと同じところへ入る。
     * ブラウザでの確認が結果を待てるよう Promise を返す。
     */
    command: function (request) {
      var parsed = typeof request === 'string' ? JSON.parse(request) : (request || {});
      var cmdId = parsed.cmd_id || SfuRemote.newCommandId();

      return new Promise(function (resolve, reject) {
        SfuRemote.localPending[cmdId] = { resolve: resolve, reject: reject };
        SfuRemote.deliver(cmdId, parsed.command, parsed.args || {});
      });
    },

    /**
     * A command id in the shape lib/sfcli/utils/jsonl_log.py checks for:
     * `c` + YYYYMMDDTHHMMSSZ + `-` + 6 hex digits. The page issues one only for
     * a command it raised itself; the server's ids come back unchanged.
     * lib/sfcli/utils/jsonl_log.py が検査する形の cmd_id。
     * `c` + YYYYMMDDTHHMMSSZ + `-` + 16 進 6 桁。ページが発行するのは自分で
     * 起こした命令の分だけで、サーバの識別子はそのまま返す。
     */
    newCommandId: function () {
      return 'c' + SfuRemote.stamp() + '-' + SfuRemote.randomHex(6);
    },

    /** The same shape without the `c`, 8 hex digits. / `c` 無しで 16 進 8 桁。 */
    newRunId: function () {
      return SfuRemote.stamp() + '-' + SfuRemote.randomHex(8);
    },

    /** YYYYMMDDTHHMMSSZ in UTC. / UTC の YYYYMMDDTHHMMSSZ。 */
    stamp: function () {
      return new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z');
    },

    randomHex: function (digits) {
      var text = '';
      for (var index = 0; index < digits; index++) {
        text += '0123456789abcdef'[Math.floor(Math.random() * 16)];
      }
      return text;
    },

    /**
     * Turn the URL's arguments into the commands they stand for. This is the
     * plan's third route into the page and the only one the public site has, so
     * `?world=gate_course` must reach exactly the handler `sf unity cmd
     * world.load --arg name=gate_course` reaches.
     * URL の引数を、それが表す命令に直す。これは計画のもう 1 つの経路であり、
     * 公開サイトが持つ唯一の経路でもある。`?world=gate_course` は
     * `sf unity cmd world.load --arg name=gate_course` と同じ処理へ届かなければ
     * ならない。
     */
    commandsFromUrl: function () {
      var parameters = new URLSearchParams(window.location.search);
      var commands = [];

      var world = parameters.get('world');
      if (world) {
        commands.push({ command: 'world.load', args: { name: world } });
      }

      var level = parameters.get('log');
      if (level) {
        commands.push({ command: 'log.level', args: { level: level } });
      }

      return commands;
    },

    /**
     * Publish the page's own entry points. They exist whether or not a server
     * answered, because the public page has nothing else.
     * ページ自身の入口を公開する。サーバが応えたかによらず存在する。公開ページ
     * には他に何も無いためである。
     */
    publish: function () {
      window.stampfly = {
        command: SfuRemote.command,
        logs: function () { return SfuRemote.readLogs(); },
        download: function () { return SfuRemote.downloadLogs(); },
        trace: function () { return SfuRemote.lastTrace; },
        downloadTrace: function () { return SfuRemote.downloadTrace(); },
        get runId() { return SfuRemote.runId; },
        get mode() {
          return SfuRemote.mode === SfuRemote.MODE_LOCAL ? 'local' : 'public';
        },
      };
    },

    /**
     * The lines this page has produced, as one JSON Lines document.
     *
     * C# keeps its own ring buffer of the same lines. This one exists because
     * window.stampfly.logs() must answer from JavaScript without a round trip
     * into the player, and because the public page -- which sends nothing --
     * still needs somewhere for its lines to accumulate.
     *
     * このページが出した行を 1 つの JSON Lines として返す。
     *
     * C# も同じ行の輪状バッファを持つ。ここにも持つのは、
     * window.stampfly.logs() がプレイヤーへ往復せずに JavaScript から答える
     * 必要があるためと、何も送らない公開ページにも行の溜まる場所が要るため。
     */
    readLogs: function () {
      return SfuRemote.ring.join('\n');
    },

    /**
     * Keep a line for window.stampfly.logs(), dropping the oldest once the ring
     * is full. Independent of the send queue: a line is kept whether or not a
     * server took it.
     * window.stampfly.logs() のために行を保持し、いっぱいになったら最も古いもの
     * を捨てる。送信の待ち行列とは別で、サーバが受け取ったかによらず保持する。
     */
    remember: function (line) {
      SfuRemote.ring.push(line);
      if (SfuRemote.ring.length > SfuRemote.RING_LIMIT) {
        SfuRemote.ring.shift();
      }
    },

    /**
     * Hand the viewer the kept lines as a file. The public page has no server
     * to post to, so this is how its log leaves the browser.
     * 保持している行をファイルとして渡す。公開ページには送る先のサーバが無く、
     * ログがブラウザの外へ出る道はこれである。
     */
    downloadLogs: function () {
      var text = SfuRemote.readLogs();
      var url = URL.createObjectURL(new Blob([text], { type: 'application/x-ndjson' }));
      var link = document.createElement('a');
      link.href = url;
      link.download = (SfuRemote.runId || 'stampfly') + '.jsonl';
      link.click();
      URL.revokeObjectURL(url);
      return text.length;
    },

    /**
     * Hand the viewer the last trace as a file, for a page whose POST could not
     * land. Named apart from the log so the two never overwrite each other in
     * the download folder.
     * 直近のトレースをファイルとして渡す。送信が届かなかったページのため。
     * ログとは別の名前にしてあり、保存先で互いを上書きしない。
     */
    downloadTrace: function () {
      var text = SfuRemote.lastTrace || '';
      var url = URL.createObjectURL(new Blob([text], { type: 'application/x-ndjson' }));
      var link = document.createElement('a');
      link.href = url;
      link.download = (SfuRemote.runId || 'stampfly') + '.trace.jsonl';
      link.click();
      URL.revokeObjectURL(url);
      return text.length;
    },
  },

  /**
   * Start the bridge: publish window.stampfly, ask /api/hello once, and begin
   * polling when it answers. `targetPointer` and `methodPointer` name the C#
   * object SendMessage delivers commands to.
   * 橋を始める。window.stampfly を公開し、/api/hello を 1 回尋ね、応えがあれば
   * 待ち受けを始める。`targetPointer` と `methodPointer` は、SendMessage が命令を
   * 届ける C# の対象を指す。
   */
  SfuRemoteStart__deps: ['$SfuRemote'],
  SfuRemoteStart: function (targetPointer, methodPointer) {
    SfuRemote.target = UTF8ToString(targetPointer);
    SfuRemote.commandMethod = UTF8ToString(methodPointer);
    SfuRemote.publish();

    window.addEventListener('pagehide', SfuRemote.beacon);
    window.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'hidden') { SfuRemote.flush(); }
    });

    SfuRemote.request('/api/hello').then(function (answer) {
      var served = answer !== null && answer.ok === true && !!answer.run_id;
      if (!served) {
        // No local server: this is the public page. It keeps its own run_id so
        // its lines are still a coherent run in the ring buffer.
        // ローカルサーバが無い。これは公開ページである。輪状バッファの中で
        // 一続きの実行として読めるよう、自分の run_id を持つ。
        SfuRemote.mode = SfuRemote.MODE_PUBLIC;
        SfuRemote.runId = SfuRemote.newRunId();
        return;
      }

      SfuRemote.mode = SfuRemote.MODE_LOCAL;
      SfuRemote.runId = answer.run_id;
      SfuRemote.serverVersion = answer.server_version || '';
      SfuRemote.poll();
      SfuRemote.flush();
    });
  },

  /**
   * How far the handshake got, for C# to read once per frame until it settles:
   * 0 unknown, 1 served locally, 2 public.
   * 握手がどこまで進んだか。落ち着くまで C# が毎フレーム読む。
   * 0 未確定、1 ローカル配信、2 公開。
   */
  SfuRemoteMode__deps: ['$SfuRemote'],
  SfuRemoteMode: function () {
    return SfuRemote.mode;
  },

  /** This run's id, as a string C# must free. / この実行の id。C# 側で解放する。 */
  SfuRemoteRunId__deps: ['$SfuRemote'],
  SfuRemoteRunId: function () {
    var text = SfuRemote.runId || '';
    var size = lengthBytesUTF8(text) + 1;
    var pointer = _malloc(size);
    stringToUTF8(text, pointer, size);
    return pointer;
  },

  /** The commands the URL asks for, as a JSON array. / URL が求める命令の JSON 配列。 */
  SfuRemoteUrlCommands__deps: ['$SfuRemote'],
  SfuRemoteUrlCommands: function () {
    var text = JSON.stringify(SfuRemote.commandsFromUrl());
    var size = lengthBytesUTF8(text) + 1;
    var pointer = _malloc(size);
    stringToUTF8(text, pointer, size);
    return pointer;
  },

  /**
   * Take one finished JSON line: keep it for window.stampfly.logs(), and queue
   * it for /api/log when a local server is listening.
   * 出来上がった JSON の行を 1 つ受け取る。window.stampfly.logs() のために保持し、
   * ローカルサーバが聞いていれば /api/log 行きの待ち行列へ積む。
   */
  SfuRemoteLog__deps: ['$SfuRemote'],
  SfuRemoteLog: function (linePointer) {
    var line = UTF8ToString(linePointer);
    SfuRemote.remember(line);

    if (SfuRemote.mode !== SfuRemote.MODE_LOCAL) { return; }
    SfuRemote.enqueue(JSON.parse(line));
  },

  /**
   * How many lines the queue has thrown away, cleared by the read so C# reports
   * each loss once.
   * 待ち行列が捨てた行の数。読むと 0 に戻し、C# が各損失を 1 回だけ報告する
   * ようにする。
   */
  SfuRemoteTakeDropped__deps: ['$SfuRemote'],
  SfuRemoteTakeDropped: function () {
    var dropped = SfuRemote.dropped;
    SfuRemote.dropped = 0;
    return dropped;
  },

  /** Answer one command. / 命令 1 つに答える。 */
  SfuRemoteAnswer__deps: ['$SfuRemote'],
  SfuRemoteAnswer: function (cmdIdPointer, ok, dataPointer, errorPointer) {
    var cmdId = UTF8ToString(cmdIdPointer);
    var succeeded = ok !== 0;
    var data = succeeded ? JSON.parse(UTF8ToString(dataPointer) || 'null') : null;
    SfuRemote.answer(cmdId, succeeded, data, UTF8ToString(errorPointer));
  },

  /**
   * Post a whole per-tick trace to /api/trace as JSON Lines. Sent as text/plain
   * rather than through SfuRemote.request, which would JSON-encode the body a
   * second time and double the size of something already measured in megabytes.
   *
   * It also keeps the trace on window.stampfly so a page with no local server
   * -- or one whose POST failed -- can still hand it over by hand:
   * `window.stampfly.trace()` returns the text and `.downloadTrace()` saves it.
   *
   * 刻みごとのトレース全体を JSON Lines として /api/trace へ送る。
   * SfuRemote.request を通さず text/plain で送るのは、あちらが本文をもう一度
   * JSON に符号化し、既にメガバイト単位のものを倍にしてしまうためである。
   *
   * トレースは window.stampfly にも残す。ローカルサーバの無いページや、送信に
   * 失敗したページからでも、人が手で取り出せるようにするためである。
   * `window.stampfly.trace()` が本文を返し、`.downloadTrace()` が保存する。
   */
  SfuRemoteTrace__deps: ['$SfuRemote'],
  SfuRemoteTrace: function (bodyPointer) {
    var body = UTF8ToString(bodyPointer);
    SfuRemote.lastTrace = body;

    if (SfuRemote.mode !== SfuRemote.MODE_LOCAL) { return 0; }

    fetch('/api/trace', {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain' },
      body: body,
    }).catch(function () {
      // The trace is still on window.stampfly, so a failed post loses nothing
      // that cannot be fetched by hand.
      // トレースは window.stampfly に残っているので、送信に失敗しても手で
      // 取り出せないものは何も失われない。
    });
    return 1;
  },
};

autoAddDeps(SfuRemoteLibrary, '$SfuRemote');
mergeInto(LibraryManager.library, SfuRemoteLibrary);
