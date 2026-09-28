/* ============================================
   スネーク
   へびを うごかして エサを たべる。
   かべか じぶんの からだに ぶつかると おわり。
   ============================================ */

var cv  = document.getElementById('cv');
var ctx = cv.getContext('2d');

var MASU = 20;              // よこ・たて いくつに わけるか
var CELL = cv.width / MASU; // 1マスの 大きさ

var snake  = [];            // へびの からだ。さきが あたま
var dir    = {x: 1, y: 0};  // いま すすんで いる むき
var nextDir = {x: 1, y: 0}; // つぎに まがる むき
var food   = {x: 0, y: 0};  // エサの ばしょ
var score  = 0;
var best   = 0;
var timer  = null;
var speed  = 130;           // 何ミリびょうごとに すすむか
var playing = false;

/* ---- Omame から まえの きろくを よみこむ ---- */
omame.ready.then(function (data) {
  best = omame.getScore('high');
  document.getElementById('best').textContent = best;

  // はやさの このみも のこして おく
  if (data && data.speed) {
    speed = data.speed;
    markSpeed();
  }
});

/* ---- ゲームを はじめる ---- */
function start() {
  // へびを まんなかに 3つ ならべる
  snake = [
    {x: 8, y: 10},
    {x: 7, y: 10},
    {x: 6, y: 10}
  ];
  dir     = {x: 1, y: 0};
  nextDir = {x: 1, y: 0};
  score   = 0;
  playing = true;

  putFood();
  updateNum();

  document.getElementById('cover').classList.add('hide');

  if (timer) clearInterval(timer);
  timer = setInterval(step, speed);
}

/* ---- エサを おく（へびに かさならない ばしょに） ---- */
function putFood() {
  while (true) {
    var x = Math.floor(Math.random() * MASU);
    var y = Math.floor(Math.random() * MASU);

    // へびの 上では ないか しらべる
    var kasanaru = snake.some(function (s) {
      return s.x === x && s.y === y;
    });

    if (!kasanaru) {
      food = {x: x, y: y};
      return;
    }
  }
}

/* ---- 1コマ すすめる ---- */
function step() {
  dir = nextDir;   // まがる むきを ここで きめる

  // あたまの つぎの ばしょ
  var head = {
    x: snake[0].x + dir.x,
    y: snake[0].y + dir.y
  };

  // かべに ぶつかった？
  if (head.x < 0 || head.y < 0 || head.x >= MASU || head.y >= MASU) {
    gameOver();
    return;
  }

  // じぶんの からだに ぶつかった？
  var butsukatta = snake.some(function (s) {
    return s.x === head.x && s.y === head.y;
  });
  if (butsukatta) {
    gameOver();
    return;
  }

  snake.unshift(head);   // あたまを たす

  // エサを たべた？
  if (head.x === food.x && head.y === food.y) {
    score += 10;
    putFood();
    updateNum();
    // たべた ときは しっぽを けさない → ながく なる
  } else {
    snake.pop();         // たべて いないので しっぽを けす
  }

  draw();
}

/* ---- 画面に かく ---- */
function draw() {
  // したじ
  ctx.fillStyle = '#0c1a12';
  ctx.fillRect(0, 0, cv.width, cv.height);

  // うすい ます目
  ctx.strokeStyle = 'rgba(255,255,255,.04)';
  ctx.lineWidth = 1;
  for (var i = 1; i < MASU; i++) {
    ctx.beginPath();
    ctx.moveTo(i * CELL, 0);
    ctx.lineTo(i * CELL, cv.height);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(0, i * CELL);
    ctx.lineTo(cv.width, i * CELL);
    ctx.stroke();
  }

  // エサ（あかい まる）
  ctx.fillStyle = '#ff5a5a';
  ctx.beginPath();
  ctx.arc(
    food.x * CELL + CELL / 2,
    food.y * CELL + CELL / 2,
    CELL / 2 - 2,
    0, Math.PI * 2
  );
  ctx.fill();

  // へび
  snake.forEach(function (s, i) {
    // あたまは あかるく、しっぽに いくほど くらく
    var akarusa = 1 - (i / snake.length) * 0.5;
    ctx.fillStyle = (i === 0)
      ? '#6ef29a'
      : 'rgba(52, 196, 111, ' + akarusa + ')';

    var pad = 1;
    ctx.beginPath();
    ctx.roundRect(
      s.x * CELL + pad,
      s.y * CELL + pad,
      CELL - pad * 2,
      CELL - pad * 2,
      4
    );
    ctx.fill();

    // あたまには 目を つける
    if (i === 0) {
      ctx.fillStyle = '#0c1a12';
      var cx = s.x * CELL + CELL / 2;
      var cy = s.y * CELL + CELL / 2;
      var d  = CELL / 5;
      ctx.beginPath();
      ctx.arc(cx - d * dir.y - d * 0.4 * dir.x,
              cy - d * dir.x - d * 0.4 * dir.y, 1.8, 0, Math.PI * 2);
      ctx.arc(cx + d * dir.y - d * 0.4 * dir.x,
              cy + d * dir.x - d * 0.4 * dir.y, 1.8, 0, Math.PI * 2);
      ctx.fill();
    }
  });
}

/* ---- てんすうを 画面に 出す ---- */
function updateNum() {
  document.getElementById('score').textContent = score;
  document.getElementById('len').textContent   = snake.length;
}

/* ---- おわり ---- */
function gameOver() {
  playing = false;
  clearInterval(timer);
  timer = null;

  var cover  = document.getElementById('cover');
  var cscore = document.getElementById('cscore');

  // Omame に さいこう記録を のこす
  var atarashii = omame.setScore('high', score);

  if (atarashii) {
    best = score;
    document.getElementById('best').textContent = best;
    cscore.innerHTML = '🎉 あたらしい きろく！<br>' + score + 'てん';
  } else {
    cscore.innerHTML = score + 'てん<br>'
      + '<span style="font-size:12.5px;color:#7aa98c">'
      + 'さいこうは ' + best + 'てん</span>';
  }

  document.getElementById('ctitle').textContent = 'おしまい';
  document.getElementById('cmsg').textContent   = 'もういちど やってみよう';
  document.getElementById('startBtn').textContent = 'もういちど';
  cover.classList.remove('hide');
}

/* ---- むきを かえる ---- */
function turn(x, y) {
  if (!playing) return;
  // まっぎゃくには まがれない（じぶんに ぶつかるので）
  if (dir.x === -x && dir.y === -y) return;
  nextDir = {x: x, y: y};
}

/* ---- キーボード ---- */
document.addEventListener('keydown', function (e) {
  var k = e.key.toLowerCase();

  if (k === 'arrowup'    || k === 'w') turn(0, -1);
  if (k === 'arrowdown'  || k === 's') turn(0,  1);
  if (k === 'arrowleft'  || k === 'a') turn(-1, 0);
  if (k === 'arrowright' || k === 'd') turn(1,  0);

  // やじるしキーで 画面が 上下に うごかない ように
  if (k.indexOf('arrow') === 0) e.preventDefault();

  // スペースで はじめる
  if (k === ' ' && !playing) start();
});

/* ---- スマホ：画面を なぞる ---- */
var tx = 0, ty = 0;

var stage = document.getElementById('stage');

stage.addEventListener('touchstart', function (e) {
  tx = e.touches[0].clientX;
  ty = e.touches[0].clientY;
}, {passive: true});

stage.addEventListener('touchend', function (e) {
  var dx = e.changedTouches[0].clientX - tx;
  var dy = e.changedTouches[0].clientY - ty;

  // うごきが 小さすぎる ときは むし
  if (Math.abs(dx) < 20 && Math.abs(dy) < 20) return;

  // よこと たて、どちらに 大きく うごいたか
  if (Math.abs(dx) > Math.abs(dy)) {
    turn(dx > 0 ? 1 : -1, 0);
  } else {
    turn(0, dy > 0 ? 1 : -1);
  }
}, {passive: true});

/* ---- スマホ：ボタン ---- */
document.querySelectorAll('.pb').forEach(function (b) {
  var go = function (e) {
    e.preventDefault();
    var d = b.dataset.dir;
    if (d === 'up')    turn(0, -1);
    if (d === 'down')  turn(0,  1);
    if (d === 'left')  turn(-1, 0);
    if (d === 'right') turn(1,  0);
  };
  b.addEventListener('touchstart', go, {passive: false});
  b.addEventListener('mousedown', go);
});

/* ---- はやさを えらぶ ---- */
function markSpeed() {
  document.querySelectorAll('.sp').forEach(function (b) {
    b.classList.toggle('on', Number(b.dataset.sp) === speed);
  });
}

document.querySelectorAll('.sp').forEach(function (b) {
  b.addEventListener('click', function () {
    speed = Number(b.dataset.sp);
    markSpeed();

    // このみを Omame に のこす
    omame.data = omame.data || {};
    omame.data.speed = speed;
    omame.save();

    // あそんで いる さいちゅうなら、はやさを すぐ かえる
    if (playing) {
      clearInterval(timer);
      timer = setInterval(step, speed);
    }
  });
});

/* ---- はじめる ボタン ---- */
document.getElementById('startBtn').addEventListener('click', start);

/* ---- さいしょの 画面を かいて おく ---- */
snake = [{x: 8, y: 10}, {x: 7, y: 10}, {x: 6, y: 10}];
food  = {x: 14, y: 10};
draw();