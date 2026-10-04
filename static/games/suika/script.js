// ====== ゲームの 設定 ======
const CANVAS_W = 360;
const CANVAS_H = 550;
const DROP_LINE_Y = 50;  // フルーツを おとす 高さ
const DEAD_LINE_Y = 100; // ここを こえたら ゲームオーバー

// フルーツの しゅるい（絵文字、半径、色、スコア）
const FRUITS = [
  { emoji: "🍒", r: 12, color: "#ff4d4d", score: 1 },
  { emoji: "🍓", r: 16, color: "#ff3366", score: 3 },
  { emoji: "🍇", r: 24, color: "#9933cc", score: 6 },
  { emoji: "🍊", r: 32, color: "#ff9900", score: 10 },
  { emoji: "🍎", r: 42, color: "#cc0000", score: 15 },
  { emoji: "🍑", r: 54, color: "#ff99cc", score: 21 },
  { emoji: "🍍", r: 68, color: "#ffcc00", score: 28 },
  { emoji: "🍈", r: 84, color: "#99ff66", score: 36 },
  { emoji: "🍉", r: 102, color: "#00cc66", score: 45 }
];

// ====== ゲームの データ ======
let state = 'title'; // 'title' か 'playing' か 'gameover'
let score = 0;
let highScore = 0;
let particles = []; // 落ちている フルーツたち
let effects = [];   // ぶつかった ときの キラキラ
let nextFruitIdx = 0;
let currentFruitX = CANVAS_W / 2;
let dropCooldown = 0; // すぐに 連続で 落とせない ようにする 待ち時間
let gameOverTimer = 0;

// ====== まちうけ（ここから はじまる） ======
omame.ready.then(function(data) {
  // セーブデータから ハイスコアを よみこむ
  highScore = omame.getScore("high") || 0;
  
  updateScoreUI();
  initEvents();
  loop(); // アニメーションを スタート
});

// ゲームを はじめる
function startGame() {
  score = 0;
  particles = [];
  effects = [];
  nextFruitIdx = Math.floor(Math.random() * 4); // さいしょは 0〜3 の どれか
  state = 'playing';
  dropCooldown = 0;
  gameOverTimer = 0;
  
  document.getElementById('title-screen').style.display = 'none';
  document.getElementById('gameover-screen').style.display = 'none';
  document.getElementById('new-record').style.display = 'none';
  updateScoreUI();
}

// フルーツを 新しく つくる
function createParticle(x, y, type) {
  return {
    x: x, y: y,
    vx: 0, vy: 0,
    r: FRUITS[type].r,
    type: type,
    merged: false // 合体して 消える かどうか
  };
}

// PTアイテム「ふっかつ」の しょり
function reviveGame() {
  // フルーツを 高い順（yが 小さい順）に ならべかえる
  particles.sort((a, b) => a.y - b.y);
  // 上に ある おじゃまな フルーツを さいだい 3つ 消す
  particles.splice(0, Math.min(3, particles.length));
  
  state = 'playing';
  gameOverTimer = 0;
  document.getElementById('gameover-screen').style.display = 'none';
}

// ====== 毎フレームの うごき ======
function loop() {
  requestAnimationFrame(loop);
  update();
  draw();
}

// 物理エンジン（計算を する ところ）
function update() {
  if (state !== 'playing') return;

  if (dropCooldown > 0) dropCooldown--;

  // キラキラエフェクトの うごき
  for (let i = effects.length - 1; i >= 0; i--) {
    let e = effects[i];
    e.x += e.vx; e.y += e.vy;
    e.life -= 0.05;
    if (e.life <= 0) effects.splice(i, 1);
  }

  // 計算を こまかく わけて 正確に する（サブステップ）
  const steps = 4;
  const dt = 1 / steps;
  
  for (let s = 0; s < steps; s++) {
    // 1. 重力と いどう
    for (let p of particles) {
      p.vy += 0.8 * dt; // 重力
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      // 摩擦（少しずつ おそく なる）
      p.vx *= 0.98;
      p.vy *= 0.98;
    }

    // 2. かべ との ぶつかり
    for (let p of particles) {
      if (p.x < p.r) { p.x = p.r; p.vx *= -0.3; }
      if (p.x > CANVAS_W - p.r) { p.x = CANVAS_W - p.r; p.vx *= -0.3; }
      if (p.y > CANVAS_H - p.r) { p.y = CANVAS_H - p.r; p.vy *= -0.3; }
    }

    // 3. フルーツ 同士の ぶつかり
    let newParticles = [];
    for (let i = 0; i < particles.length; i++) {
      for (let j = i + 1; j < particles.length; j++) {
        let p1 = particles[i];
        let p2 = particles[j];
        if (p1.merged || p2.merged) continue;

        let dx = p2.x - p1.x;
        let dy = p2.y - p1.y;
        let dist2 = dx*dx + dy*dy;
        let minDist = p1.r + p2.r;

        // ぶつかって いるか？
        if (dist2 < minDist * minDist) {
          let dist = Math.sqrt(dist2);
          
          // 同じ しゅるい なら 合体する
          if (p1.type === p2.type && p1.type < FRUITS.length - 1) {
            p1.merged = true;
            p2.merged = true;
            let nx = p1.x + dx / 2;
            let ny = p1.y + dy / 2;
            let newType = p1.type + 1;
            
            score += FRUITS[newType].score;
            let np = createParticle(nx, ny, newType);
            np.vy = -1; // 少し はねる
            newParticles.push(np);
            
            // キラキラを だす
            for(let k = 0; k < 5; k++) {
              effects.push({
                x: nx, y: ny,
                vx: (Math.random() - 0.5) * 8, vy: (Math.random() - 0.5) * 8,
                r: Math.random() * 3 + 2,
                color: FRUITS[newType].color,
                life: 1.0
              });
            }
            continue; // 合体したら おしだしの 計算は しない
          }

          // ちがう フルーツなら 重ならない ように おしだす
          if (dist === 0) { dx = 1; dy = 0; dist = 1; }
          let overlap = minDist - dist;
          let nx_norm = dx / dist;
          let ny_norm = dy / dist;
          
          let pushX = nx_norm * overlap * 0.5;
          let pushY = ny_norm * overlap * 0.5;
          
          p1.x -= pushX; p1.y -= pushY;
          p2.x += pushX; p2.y += pushY;
          
          // はねかえる スピード
          let rvx = p2.vx - p1.vx;
          let rvy = p2.vy - p1.vy;
          let velAlongNormal = rvx * nx_norm + rvy * ny_norm;
          if (velAlongNormal < 0) {
            let e = 0.1; // はねかえりの 強さ
            let j_imp = -(1 + e) * velAlongNormal / 2;
            p1.vx -= j_imp * nx_norm; p1.vy -= j_imp * ny_norm;
            p2.vx += j_imp * nx_norm; p2.vy += j_imp * ny_norm;
          }
        }
      }
    }
    // 合体した 古いものを 消して、新しいものを 追加
    particles = particles.filter(p => !p.merged);
    particles.push(...newParticles);
  }

  // 4. ゲームオーバーの 判定
  // うえの デッドラインを こえていて、うごきが とまっているか？
  let isDanger = false;
  for (let p of particles) {
    if (p.y - p.r < DEAD_LINE_Y && Math.abs(p.vx) < 1 && Math.abs(p.vy) < 1) {
      isDanger = true; break;
    }
  }
  
  if (isDanger) {
    gameOverTimer++;
    if (gameOverTimer > 60) { // 約1秒 ずっと とまっていたら 負け
      triggerGameOver();
    }
  } else {
    gameOverTimer = 0;
  }
  
  updateScoreUI();
}

function triggerGameOver() {
  state = 'gameover';
  let isHigh = false;
  
  if (score > highScore) {
    isHigh = true;
    highScore = score;
    omame.setScore('high', highScore); // ハイスコアを 保存
    omame.save();
  }
  
  document.getElementById('final-score').innerText = score;
  document.getElementById('high-score').innerText = highScore;
  if (isHigh) {
    document.getElementById('new-record').style.display = 'block';
  }
  document.getElementById('gameover-screen').style.display = 'flex';
}

function updateScoreUI() {
  document.getElementById('score-text').innerText = score;
  document.getElementById('next-text').innerText = FRUITS[nextFruitIdx].emoji;
}

// ====== 画面を かく ところ ======
function draw() {
  const canvas = document.getElementById('game-canvas');
  const ctx = canvas.getContext('2d');
  
  // 画面を １回 まっさらに する
  ctx.clearRect(0, 0, CANVAS_W, CANVAS_H);

  // デッドライン（赤い点線）を ひく
  ctx.beginPath();
  ctx.moveTo(0, DEAD_LINE_Y);
  ctx.lineTo(CANVAS_W, DEAD_LINE_Y);
  ctx.setLineDash([5, 5]);
  if (gameOverTimer > 0) {
    ctx.strokeStyle = gameOverTimer % 20 < 10 ? '#ff0000' : '#ff9999'; // ピンチのときは 点滅
  } else {
    ctx.strokeStyle = '#cccccc';
  }
  ctx.lineWidth = 2;
  ctx.stroke();
  ctx.setLineDash([]);

  // 落とす まえの フルーツと、ガイドライン
  if (state === 'playing' && dropCooldown === 0) {
    ctx.beginPath();
    ctx.moveTo(currentFruitX, DROP_LINE_Y);
    ctx.lineTo(currentFruitX, CANVAS_H);
    ctx.strokeStyle = 'rgba(0,0,0,0.1)';
    ctx.lineWidth = 2;
    ctx.stroke();
    
    drawFruit(ctx, currentFruitX, DROP_LINE_Y, nextFruitIdx);
  }

  // 落ちている フルーツたちを かく
  for (let p of particles) {
    drawFruit(ctx, p.x, p.y, p.type);
  }

  // キラキラエフェクトを かく
  for (let e of effects) {
    ctx.globalAlpha = Math.max(0, e.life);
    ctx.beginPath();
    ctx.arc(e.x, e.y, e.r, 0, Math.PI * 2);
    ctx.fillStyle = e.color;
    ctx.fill();
    ctx.globalAlpha = 1.0;
  }
}

function drawFruit(ctx, x, y, type) {
  let f = FRUITS[type];
  
  // まるい 背景
  ctx.beginPath();
  ctx.arc(x, y, f.r, 0, Math.PI * 2);
  ctx.fillStyle = f.color;
  ctx.fill();
  
  // 少し 立体的に みせる 光沢
  let grad = ctx.createRadialGradient(x - f.r*0.3, y - f.r*0.3, f.r*0.1, x, y, f.r);
  grad.addColorStop(0, 'rgba(255,255,255,0.4)');
  grad.addColorStop(1, 'rgba(0,0,0,0.1)');
  ctx.fillStyle = grad;
  ctx.fill();

  // 絵文字を まん中に かく
  ctx.font = `${f.r * 1.2}px sans-serif`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  // OSによって 絵文字の 高さが 少し ずれるので + f.r*0.1 で 調整
  ctx.fillText(f.emoji, x, y + f.r * 0.1);
}

// ====== 操作（クリックや タッチ） ======
function initEvents() {
  const canvas = document.getElementById('game-canvas');
  
  // 画面の どこを さわったか 計算する（スマホの 拡大縮小に 合わせる）
  function getTouchX(e) {
    const rect = canvas.getBoundingClientRect();
    let clientX = e.touches && e.touches.length > 0 ? e.touches[0].clientX : e.clientX;
    return (clientX - rect.left) * (CANVAS_W / rect.width);
  }

  // 指を うごかした とき
  function handleMove(x) {
    if (state !== 'playing' || dropCooldown > 0) return;
    let r = FRUITS[nextFruitIdx].r;
    currentFruitX = Math.max(r, Math.min(CANVAS_W - r, x));
  }

  // 指を はなした とき（落とす）
  function handleDrop() {
    if (state !== 'playing' || dropCooldown > 0) return;
    
    let p = createParticle(currentFruitX, DROP_LINE_Y, nextFruitIdx);
    particles.push(p);
    
    nextFruitIdx = Math.floor(Math.random() * 4); // 0〜3（チェリーからミカンまで）
    dropCooldown = 45; // 次が 落ちるまで 少し まつ（約0.7秒）
  }

  // スマホの タッチ操作
  canvas.addEventListener('touchstart', e => {
    e.preventDefault();
    handleMove(getTouchX(e));
  }, {passive: false});
  canvas.addEventListener('touchmove', e => {
    e.preventDefault();
    handleMove(getTouchX(e));
  }, {passive: false});
  canvas.addEventListener('touchend', e => {
    e.preventDefault();
    handleDrop();
  }, {passive: false});

  // パソコンの マウス操作
  let isMouseDn = false;
  canvas.addEventListener('mousedown', e => {
    isMouseDn = true; handleMove(getTouchX(e));
  });
  canvas.addEventListener('mousemove', e => {
    if (isMouseDn) handleMove(getTouchX(e));
  });
  canvas.addEventListener('mouseup', e => {
    if (isMouseDn) { isMouseDn = false; handleDrop(); }
  });
  canvas.addEventListener('mouseleave', e => {
    if (isMouseDn) { isMouseDn = false; handleDrop(); }
  });
  
  // ボタンの 処理
  document.getElementById('start-btn').addEventListener('click', startGame);
  document.getElementById('restart-btn').addEventListener('click', startGame);
  
  // アイテム「ふっかつ」を 買う
  document.getElementById('revive-btn').addEventListener('click', () => {
    // PTをつかって 購入。code "revive" は 運営画面で 設定されたもの。
    omame.buy("revive").then(function(r) {
      if (r.ok) {
        reviveGame();
      } else {
        alert(r.error || "PTが たりません。");
      }
    });
  });
}