// ====== ステージの データ (全10ワールド・長く・飛び移りやすく修正) ======
// . = 空 / # = ブロック / c = コイン / E = 敵 / F = ゴール
const LEVELS = [
  // ワールド 1 (れんしゅう)
  [
    "........................................",
    "........................................",
    "........................................",
    "..................ccc...................",
    "........ccc......#####......ccc.........",
    ".......#####...............#####.....F..",
    ".....................................#..",
    "########################################"
  ],
  // ワールド 2 (敵と 穴)
  [
    ".............................................",
    ".............................................",
    ".............................................",
    "...........ccc...................ccc.........",
    "..........#####........ccc......#####........",
    "......................#####...............F..",
    "......E.........................E.........#..",
    "##########...##########...##########...######"
  ],
  // ワールド 3 (かいだん)
  [
    "..................................................",
    "...................................ccc............",
    ".........................ccc......#####...........",
    "...............ccc......#####.....................",
    "......ccc.....#####.........................ccc...",
    ".....#####.................................#####.F",
    "........E......................................E.#",
    "##########..##########..##########..##########..##"
  ],
  // ワールド 4 (ちょっと 長い穴)
  [
    "..................................................",
    "..................................................",
    "..................................................",
    "..................ccc............ccc..............",
    ".......ccc.......#####..........#####......ccc....",
    "......#####...............................#####.F.",
    "........................E.......................#.",
    "#############..#####..#####..#####..#############."
  ],
  // ワールド 5 (うごく敵を よけろ)
  [
    ".......................................................",
    ".......................................................",
    ".......................................................",
    "....................ccc.......ccc......................",
    ".......ccc.........#####.....#####........ccc..........",
    "......#####..............................#####......F..",
    "...............E.......E...........E................#..",
    "########.....########.....########.....################"
  ],
  // ワールド 6 (高いところ)
  [
    "............................................................",
    ".............................ccc............................",
    "............................#####...........................",
    ".............ccc...........................ccc..............",
    "............#####.....E...................#####.............",
    ".......ccc...........###...........E.....................F..",
    "......#####..............................................#..",
    "##########...#########..#######...#######...############.###"
  ],
  // ワールド 7 (れんぞく ジャンプ)
  [
    "............................................................",
    "............................................................",
    "....................ccc....ccc.....ccc......................",
    "...................#####..#####...#####.....................",
    ".......ccc...................................ccc.........F..",
    "......#####.................................#####........#..",
    ".........................................................#..",
    "#######...#####...#####...#####...########...#######...#####"
  ],
  // ワールド 8 (敵が たくさん)
  [
    ".................................................................",
    ".................................................................",
    ".................................................................",
    "................................ccc..............................",
    "................ccc....ccc.....#####......ccc......ccc...........",
    ".....##........#####..#####..............#####....#####.......F..",
    "..........E..E...............E.......E........E...............#..",
    "###################..################################...#########"
  ],
  // ワールド 9 (アスレチック)
  [
    ".................................................................",
    "............................ccc..................................",
    "...................ccc.....#####.............ccc.................",
    "..........ccc.....#####.....................#####................",
    ".........#####.......................##..........................",
    "..............................................................F..",
    "......E.........................E...................E.........#..",
    "#########.....#########...#########...#########...########..###"
  ],
  // ワールド 10 (さいしゅう けっせん)
  [
    "...........................................................................",
    "...........................................................................",
    ".............................................ccc...........................",
    "......................ccc....ccc............#####........ccc.....ccc.......",
    ".....................#####..#####.......................#####...#####......",
    "........ccc..........................ccc................................F..",
    ".......#####......E.......E.........#####..........E.........E..........#..",
    "########...########...#########...#######...########..########..###########"
  ]
];

const TSIZE = 40; // 1つの ブロックの 大きさ

// ====== セーブデータ ======
let unlockStage = 1;
let myCoins = 0;
let myItems = { life: 0, jump: 0 };

// ====== ゲームの じょうたい ======
let currentStage = 0;
let getCoin = 0; 
let isPlaying = false;
let mapW = 0, mapH = 0;
let camX = 0; // カメラの いち

let keys = { left: false, right: false, up: false };

// 主人公の データ
let p = {
  x: 0, y: 0, w: 24, h: 24,
  vx: 0, vy: 0,
  speed: 4.5,
  jumpPower: -12.5, // ちゃんと 届くように ジャンプ力を 強くしました
  gravity: 0.6,
  onGround: false,
  life: 3, maxLife: 3,
  muteki: 0,
  emoji: "😀"
};

let blocks = [];
let coinsObj = [];
let enemies = [];
let goalObj = null;

// ====== データの よみこみ ======
omame.ready.then(function(data){
  // ここで アカウントごとの データが サーバーから よみこまれます
  unlockStage = data.unlockedStage || 1;
  myCoins = data.coin || 0;
  myItems = data.items || { life: 0, jump: 0 };
  
  updateUI();
  setupEvents();
  requestAnimationFrame(gameLoop);
});

// セーブする 処理
function saveGame() {
  omame.data.unlockedStage = unlockStage;
  omame.data.coin = myCoins;
  omame.data.items = myItems;
  // これを よぶだけで、あそんでいる アカウントの サーバーに のこります！
  omame.save(); 
}

function showScreen(id) {
  document.querySelectorAll('.screen').forEach(el => el.style.display = 'none');
  document.getElementById(id).style.display = 'flex';
}

function updateUI() {
  document.getElementById('val-coin').innerText = myCoins;
  let list = document.getElementById('stage-list');
  list.innerHTML = "";
  
  for (let i = 0; i < LEVELS.length; i++) {
    let btn = document.createElement('button');
    btn.className = 'btn stage-btn';
    btn.innerText = (i + 1);
    if (i + 1 > unlockStage) {
      btn.disabled = true;
    } else {
      btn.onclick = () => startStage(i);
    }
    list.appendChild(btn);
  }
  
  document.getElementById('val-shop-coin').innerText = myCoins;
}

// ====== ステージを つくる ======
function startStage(idx) {
  currentStage = idx;
  getCoin = 0;
  
  blocks = []; coinsObj = []; enemies = []; goalObj = null;
  let mapData = LEVELS[idx];
  mapH = mapData.length * TSIZE;
  mapW = mapData[0].length * TSIZE;
  
  for (let r = 0; r < mapData.length; r++) {
    for (let c = 0; c < mapData[r].length; c++) {
      let char = mapData[r][c];
      let x = c * TSIZE;
      let y = r * TSIZE;
      
      if (char === '#') blocks.push({x, y, w: TSIZE, h: TSIZE});
      else if (char === 'c') coinsObj.push({x: x+10, y: y+10, w: 20, h: 20, active: true});
      else if (char === 'F') goalObj = {x, y, w: TSIZE, h: TSIZE};
      else if (char === 'E') enemies.push({x, y, w: 24, h: 24, vx: -2, vy: 0, active: true});
    }
  }
  
  p.x = TSIZE; p.y = TSIZE;
  p.vx = 0; p.vy = 0;
  
  p.maxLife = 3 + myItems.life;
  p.life = p.maxLife;
  // アイテムを 買うと さらに ジャンプ力が アップ！
  p.jumpPower = -12.5 - (myItems.jump * 1.5);
  p.muteki = 0;
  
  camX = 0;
  isPlaying = true;
  updateHUD();
  showScreen('screen-game');
}

// ====== メインループ ======
function gameLoop() {
  requestAnimationFrame(gameLoop);
  if (!isPlaying) return;
  update();
  draw();
}

function update() {
  if (keys.left) p.vx = -p.speed;
  else if (keys.right) p.vx = p.speed;
  else p.vx = 0;
  
  if (keys.up && p.onGround) {
    p.vy = p.jumpPower;
    p.onGround = false;
  }
  
  if (p.muteki > 0) p.muteki--;
  p.vy += p.gravity;
  
  p.x += p.vx;
  for (let b of blocks) {
    if (isHit(p, b)) {
      if (p.vx > 0) p.x = b.x - p.w;
      else if (p.vx < 0) p.x = b.x + b.w;
    }
  }
  
  p.y += p.vy;
  p.onGround = false;
  for (let b of blocks) {
    if (isHit(p, b)) {
      if (p.vy > 0) {
        p.y = b.y - p.h;
        p.vy = 0;
        p.onGround = true;
      } else if (p.vy < 0) {
        p.y = b.y + b.h;
        p.vy = 0;
      }
    }
  }
  
  if (p.x < 0) p.x = 0;
  if (p.y > mapH) p.life = 0; 
  
  for (let e of enemies) {
    if (!e.active) continue;
    if (e.y > mapH) { e.active = false; continue; } // 落ちたら 消える
    
    e.vy += p.gravity;
    e.x += e.vx;
    for (let b of blocks) {
      if (isHit(e, b)) {
        if (e.vx > 0) { e.x = b.x - e.w; e.vx *= -1; }
        else if (e.vx < 0) { e.x = b.x + b.w; e.vx *= -1; }
      }
    }
    e.y += e.vy;
    for (let b of blocks) {
      if (isHit(e, b) && e.vy > 0) { e.y = b.y - e.h; e.vy = 0; }
    }
    
    if (isHit(p, e)) {
      if (p.vy > 0 && p.y + p.h < e.y + e.h / 2) {
        e.active = false;
        p.vy = p.jumpPower * 0.7;
      } else if (p.muteki <= 0) {
        p.life--;
        p.muteki = 60;
        p.vy = -5;
        p.vx = (p.x < e.x) ? -5 : 5;
      }
    }
  }
  
  for (let c of coinsObj) {
    if (c.active && isHit(p, c)) {
      c.active = false;
      getCoin++;
    }
  }
  
  if (goalObj && isHit(p, goalObj)) {
    stageClear();
  }
  
  const cvs = document.getElementById('game-canvas');
  camX = p.x - cvs.width / 2;
  if (camX < 0) camX = 0;
  if (camX > mapW - cvs.width) camX = mapW - cvs.width;
  
  updateHUD();
  
  if (p.life <= 0 && isPlaying) {
    isPlaying = false;
    document.getElementById('screen-gameover').style.display = 'flex';
  }
}

function draw() {
  const cvs = document.getElementById('game-canvas');
  const ctx = cvs.getContext('2d');
  
  ctx.clearRect(0, 0, cvs.width, cvs.height);
  ctx.save();
  ctx.translate(-camX, 0); 
  
  for (let b of blocks) {
    ctx.fillStyle = '#8B4513';
    ctx.fillRect(b.x, b.y, b.w, b.h);
    ctx.fillStyle = '#228B22';
    ctx.fillRect(b.x, b.y, b.w, 6);
  }
  
  if (goalObj) {
    ctx.font = "30px sans-serif";
    ctx.fillText("🚩", goalObj.x, goalObj.y + 30);
  }
  
  ctx.font = "20px sans-serif";
  for (let c of coinsObj) {
    if (c.active) ctx.fillText("🪙", c.x, c.y + 16);
  }
  
  ctx.font = "24px sans-serif";
  for (let e of enemies) {
    if (e.active) ctx.fillText("👾", e.x, e.y + 20);
  }
  
  if (p.muteki % 10 < 5) {
    ctx.font = "24px sans-serif";
    ctx.fillText(p.emoji, p.x, p.y + 20);
  }
  
  ctx.restore();
}

function updateHUD() {
  document.getElementById('hud-life').innerText = p.life;
  document.getElementById('hud-coin').innerText = getCoin;
}

function stageClear() {
  isPlaying = false;
  myCoins += getCoin;
  if (currentStage + 1 >= unlockStage) {
    unlockStage = currentStage + 2;
  }
  saveGame();
  
  document.getElementById('clear-coin').innerText = getCoin;
  document.getElementById('screen-clear').style.display = 'flex';
}

function isHit(a, b) {
  return (a.x < b.x + b.w && a.x + a.w > b.x &&
          a.y < b.y + b.h && a.y + a.h > b.y);
}

// ====== 操作の じゅんび ======
function setupEvents() {
  document.getElementById('btn-start').onclick = () => { updateUI(); showScreen('screen-map'); };
  document.getElementById('btn-go-shop').onclick = () => { updateUI(); showScreen('screen-shop'); };
  document.getElementById('btn-shop-back').onclick = () => { updateUI(); showScreen('screen-map'); };
  
  document.getElementById('btn-buy-life').onclick = () => {
    if (myCoins >= 50) {
      myCoins -= 50; myItems.life++; saveGame(); updateUI();
      alert("❤️ ハートの さいだい数が ふえた！");
    } else alert("コインが たりないよ！");
  };
  document.getElementById('btn-buy-jump').onclick = () => {
    if (myCoins >= 50) {
      myCoins -= 50; myItems.jump++; saveGame(); updateUI();
      alert("👟 ジャンプ力が アップした！");
    } else alert("コインが たりないよ！");
  };
  
  document.getElementById('btn-back-map1').onclick = () => { updateUI(); showScreen('screen-map'); };
  document.getElementById('btn-back-map2').onclick = () => { updateUI(); showScreen('screen-map'); };
  document.getElementById('btn-retry').onclick = () => {
    document.getElementById('screen-gameover').style.display = 'none';
    startStage(currentStage);
  };
  document.getElementById('btn-next-stage').onclick = () => {
    document.getElementById('screen-clear').style.display = 'none';
    if (currentStage + 1 < LEVELS.length) startStage(currentStage + 1);
    else { alert("🎉 ぜんぶ クリア！ おめでとう！"); updateUI(); showScreen('screen-map'); }
  };
  
  window.addEventListener('keydown', e => {
    if (e.code === 'ArrowLeft') keys.left = true;
    if (e.code === 'ArrowRight') keys.right = true;
    if (e.code === 'Space' || e.code === 'ArrowUp') keys.up = true;
  });
  window.addEventListener('keyup', e => {
    if (e.code === 'ArrowLeft') keys.left = false;
    if (e.code === 'ArrowRight') keys.right = false;
    if (e.code === 'Space' || e.code === 'ArrowUp') keys.up = false;
  });
  
  function bindPad(id, keyName) {
    const el = document.getElementById(id);
    const press = (e) => { e.preventDefault(); keys[keyName] = true; };
    const release = (e) => { e.preventDefault(); keys[keyName] = false; };
    
    el.addEventListener('mousedown', press);
    el.addEventListener('mouseup', release);
    el.addEventListener('mouseleave', release);
    el.addEventListener('touchstart', press, {passive: false});
    el.addEventListener('touchend', release, {passive: false});
    el.addEventListener('touchcancel', release, {passive: false});
  }
  
  bindPad('pad-btn-left', 'left');
  bindPad('pad-btn-right', 'right');
  bindPad('pad-btn-jump', 'up');
}