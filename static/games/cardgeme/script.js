/* ==========================================================================
   くらしバトル - メインプログラム (script.js)
   小学生〜中学生がプログラミングの学習・解読ができる丁寧なコード設計
   ========================================================================== */

// --- 1. カードデータ定義（全45種類） ---
// レア値 ＝ つかう度×1 ＋ ゆうめい度×1.5 ＋ べんり度×2
// 40〜45: ★5(でんせつ) / 32〜39: ★4(レア) / 24〜31: ★3(アンコモン)
// 15〜23: ★2(コモン)   / 4〜14: ★1(ふつう)

const CARD_DATABASE = [
  // ★5 でんせつ
  { id: "reizouko", name: "れいぞうこ", emoji: "🧊", use: 10, fame: 10, conv: 10 },
  { id: "sumaho", name: "スマホ", emoji: "📱", use: 10, fame: 10, conv: 10 },
  { id: "sentakuki", name: "せんたくき", emoji: "🧺", use: 9, fame: 10, conv: 10 },
  { id: "renji", name: "でんしレンジ", emoji: "📻", use: 9, fame: 9, conv: 10 },
  { id: "suidou", name: "すいどう", emoji: "🚰", use: 10, fame: 10, conv: 9 },
  { id: "shingou", name: "しんごうき", emoji: "🚦", use: 8, fame: 10, conv: 9 },

  // ★4 レア
  { id: "keshigomu", name: "けしゴム", emoji: "✏️", use: 8, fame: 10, conv: 7 },
  { id: "shampoo", name: "シャンプー", emoji: "🧴", use: 9, fame: 9, conv: 8 },
  { id: "soujiki", name: "そうじき", emoji: "🧹", use: 7, fame: 9, conv: 9 },
  { id: "haburashi", name: "はブラシ", emoji: "🪥", use: 10, fame: 9, conv: 6 },
  { id: "kasa", name: "かさ", emoji: "☂️", use: 5, fame: 10, conv: 9 },
  { id: "jitensha", name: "じてんしゃ", emoji: "🚲", use: 7, fame: 9, conv: 8 },
  { id: "hasami", name: "ハサミ", emoji: "✂️", use: 6, fame: 9, conv: 8 },
  { id: "suihanki", name: "すいはんき", emoji: "🍚", use: 8, fame: 8, conv: 9 },
  { id: "fudebako", name: "ふでばこ", emoji: "👝", use: 8, fame: 9, conv: 7 },
  { id: "dryer", name: "ドライヤー", emoji: "💨", use: 7, fame: 8, conv: 8 },

  // ★3 アンコモン
  { id: "tsumekiri", name: "つめきり", emoji: "✂️", use: 3, fame: 9, conv: 7 },
  { id: "taijoukei", name: "たいじゅうけい", emoji: "⚖️", use: 4, fame: 8, conv: 6 },
  { id: "houchou", name: "ほうちょう", emoji: "🔪", use: 6, fame: 8, conv: 6 },
  { id: "tape", name: "セロテープ", emoji: "🩹", use: 5, fame: 8, conv: 6 },
  { id: "jougi", name: "じょうぎ", emoji: "📏", use: 5, fame: 8, conv: 5 },
  { id: "stapler", name: "ホッチキス", emoji: "📎", use: 4, fame: 8, conv: 6 },
  { id: "houki", name: "ほうき", emoji: "🧹", use: 5, fame: 7, conv: 6 },
  { id: "suitou", name: "すいとう", emoji: "🍶", use: 7, fame: 7, conv: 5 },
  { id: "tokei", name: "とけい", emoji: "⏰", use: 8, fame: 9, conv: 4 },
  { id: "frypan", name: "フライパン", emoji: "🍳", use: 7, fame: 8, conv: 5 },

  // ★2 コモン
  { id: "soroban", name: "そろばん", emoji: "🧮", use: 1, fame: 7, conv: 4 },
  { id: "mushimegane", name: "むしめがね", emoji: "🔍", use: 2, fame: 7, conv: 4 },
  { id: "uchiwa", name: "うちわ", emoji: "🪭", use: 3, fame: 6, conv: 4 },
  { id: "taionkei", name: "たいおんけい", emoji: "🌡️", use: 2, fame: 7, conv: 5 },
  { id: "choukokutou", name: "ちょうこくとう", emoji: "✒️", use: 1, fame: 6, conv: 4 },
  { id: "pizzacutter", name: "ピザカッター", emoji: "🍕", use: 1, fame: 5, conv: 4 },
  { id: "jouro", name: "じょうろ", emoji: "🪴", use: 2, fame: 6, conv: 4 },
  { id: "kankiri", name: "かんきり", emoji: "🥫", use: 2, fame: 6, conv: 4 },
  { id: "korokoro", name: "コロコロ", emoji: "🧹", use: 4, fame: 6, conv: 4 },
  { id: "pencil", name: "えんぴつけずり", emoji: "✏️", use: 5, fame: 6, conv: 4 },

  // ★1 ふつう
  { id: "magonote", name: "まごのて", emoji: "✋", use: 1, fame: 4, conv: 3 },
  { id: "bunchin", name: "ぶんちん", emoji: "🧱", use: 1, fame: 4, conv: 3 },
  { id: "itodenwa", name: "いとでんわ", emoji: "📞", use: 1, fame: 4, conv: 1 },
  { id: "taketonbo", name: "たけとんぼ", emoji: "🚁", use: 1, fame: 4, conv: 1 },
  { id: "mizudappou", name: "みずでっぽう", emoji: "🔫", use: 1, fame: 5, conv: 2 },
  { id: "sankakujougi", name: "さんかくじょうぎ", emoji: "📐", use: 2, fame: 4, conv: 2 },
  { id: "waribashi", name: "わりばし", emoji: "🥢", use: 3, fame: 4, conv: 2 },
  { id: "sennuki", name: "せんぬき", emoji: "🍾", use: 1, fame: 4, conv: 2 },
  { id: "kaidoku", name: "むしよけスプレー", emoji: "🧴", use: 2, fame: 5, conv: 2 }
];

// --- 2. グローバル変数 (ゲームの状態を保持) ---
let userState = {
  coin: 50,
  cards: { "keshigomu": 1, "soroban": 1, "magonote": 1 },
  win: 0,
  lose: 0
};

let battleState = {
  cpuDifficulty: "normal",
  playerDeck: [],
  cpuDeck: [],
  playerHand: [],
  cpuHand: [],
  selectedPlayerCard: null,
  round: 1,
  playerScore: 0,
  cpuScore: 0
};

// --- 3. 音声効果（Web Audio API） ---
const AudioCtx = window.AudioContext || window.webkitAudioContext;
let audioCtx = null;

function playSound(type) {
  try {
    if (!audioCtx) audioCtx = new AudioCtx();
    if (audioCtx.state === 'suspended') audioCtx.resume();
    
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.connect(gain);
    gain.connect(audioCtx.destination);
    const now = audioCtx.currentTime;

    if (type === 'click') {
      osc.frequency.setValueAtTime(400, now);
      osc.frequency.exponentialRampToValueAtTime(800, now + 0.05);
      gain.gain.setValueAtTime(0.1, now);
      gain.gain.exponentialRampToValueAtTime(0.01, now + 0.05);
      osc.start(now); osc.stop(now + 0.05);
    } else if (type === 'win') {
      osc.type = 'triangle';
      osc.frequency.setValueAtTime(523, now);
      osc.frequency.setValueAtTime(659, now + 0.1);
      osc.frequency.setValueAtTime(783, now + 0.2);
      gain.gain.setValueAtTime(0.15, now);
      gain.gain.exponentialRampToValueAtTime(0.01, now + 0.4);
      osc.start(now); osc.stop(now + 0.4);
    } else if (type === 'coin') {
      osc.type = 'sine';
      osc.frequency.setValueAtTime(987, now);
      osc.frequency.setValueAtTime(1318, now + 0.08);
      gain.gain.setValueAtTime(0.1, now);
      gain.gain.exponentialRampToValueAtTime(0.01, now + 0.2);
      osc.start(now); osc.stop(now + 0.2);
    }
  } catch(e) {
    // ブラウザの音声自動再生ブロック用対策
  }
}

// --- 4. 初期化（omame.ready） ---
omame.ready.then(function(savedData) {
  if (savedData && typeof savedData === 'object') {
    if (typeof savedData.coin === 'number') userState.coin = savedData.coin;
    if (savedData.cards && typeof savedData.cards === 'object') userState.cards = savedData.cards;
    if (typeof savedData.win === 'number') userState.win = savedData.win;
    if (typeof savedData.lose === 'number') userState.lose = savedData.lose;
  }
  updateHeaderUI();
  goToScreen('screen-home');
});

// セーブ処理
function saveAllData() {
  omame.save({
    coin: userState.coin,
    cards: userState.cards,
    win: userState.win,
    lose: userState.lose
  });
  omame.setScore("high_win", userState.win);
}

// ヘッダーUI更新
function updateHeaderUI() {
  document.getElementById("coin-count").textContent = userState.coin;
  document.getElementById("card-count").textContent = Object.keys(userState.cards).length + "/45";
  document.getElementById("win-count").textContent = userState.win;
  document.getElementById("lose-count").textContent = userState.lose;
}

// レアリティ判定関数
function getRarity(card) {
  const rv = card.use * 1 + card.fame * 1.5 + card.conv * 2;
  if (rv >= 40) return { rank: 5, name: "でんせつ", stars: "★★★★★", badgeClass: "rarity-5", rv };
  if (rv >= 32) return { rank: 4, name: "レア", stars: "★★★★", badgeClass: "rarity-4", rv };
  if (rv >= 24) return { rank: 3, name: "アンコモン", stars: "★★★", badgeClass: "rarity-3", rv };
  if (rv >= 15) return { rank: 2, name: "コモン", stars: "★★", badgeClass: "rarity-2", rv };
  return { rank: 1, name: "ふつう", stars: "★", badgeClass: "rarity-1", rv };
}

// カードDOM生成
function createCardElement(card, isOwned = true) {
  const rarity = getRarity(card);
  const div = document.createElement("div");
  div.className = `card-item ${rarity.badgeClass} ${isOwned ? '' : 'locked'}`;
  div.dataset.id = card.id;

  const total = card.use + card.fame + card.conv;

  div.innerHTML = `
    <div class="card-stars">${rarity.stars}</div>
    <div class="card-emoji">${card.emoji}</div>
    <div class="card-name">${card.name}</div>
    <div class="card-stats-mini">
      <div class="stat-line"><span>つかう</span><b>${card.use}</b></div>
      <div class="stat-line"><span>ゆうめい</span><b>${card.fame}</b></div>
      <div class="stat-line"><span>べんり</span><b>${card.conv}</b></div>
    </div>
  `;
  return div;
}

// 画面遷移
function goToScreen(screenId) {
  playSound('click');
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  const target = document.getElementById(screenId);
  if (target) target.classList.add('active');

  // まえの がめんの のこりを けす
  // （これが ないと、もういちど ひらいた ときに 古い ものが 見える）
  if (screenId === 'screen-pack') resetPack();
  if (screenId === 'screen-cpu-select') resetBattle();
}

/* パックの がめんを まっさらに する */
function resetPack() {
  const visual = document.getElementById('pack-visual');
  const revealArea = document.getElementById('pack-reveal-area');
  if (visual) visual.style.display = '';
  if (revealArea) revealArea.innerHTML = '';
}

/* バトルの のこりを ぜんぶ けす */
function resetBattle() {
  battleState.playerDeck = [];
  battleState.cpuDeck = [];
  battleState.playerHand = [];
  battleState.cpuHand = [];
  battleState.selectedPlayerCard = null;
  battleState.round = 1;
  battleState.playerScore = 0;
  battleState.cpuScore = 0;

  // 画面の のこりを けす
  const modal = document.getElementById('battle-finish-modal');
  if (modal) modal.classList.add('hidden');
  const rw = document.getElementById('finish-rewards');
  if (rw) rw.innerHTML = '';
}

// --- 5. バトル処理 ---
function goToCpuSelect() {
  goToScreen('screen-cpu-select');
}

function selectCpu(difficulty) {
  battleState.cpuDifficulty = difficulty;
  setupDeckSelectScreen();
  goToScreen('screen-deck-select');
}

function setupDeckSelectScreen() {
  const pool = document.getElementById("deck-pool");
  pool.innerHTML = "";
  battleState.playerDeck = [];
  updateDeckSelectUI();

  Object.keys(userState.cards).forEach(cardId => {
    const card = CARD_DATABASE.find(c => c.id === cardId);
    if (!card) return;
    const elem = createCardElement(card, true);
    elem.onclick = () => toggleSelectCardInDeck(card, elem);
    pool.appendChild(elem);
  });
}

function toggleSelectCardInDeck(card, elem) {
  const idx = battleState.playerDeck.findIndex(c => c.id === card.id);
  if (idx >= 0) {
    battleState.playerDeck.splice(idx, 1);
    elem.classList.remove("selected");
  } else {
    if (battleState.playerDeck.length < 3) {
      battleState.playerDeck.push(card);
      elem.classList.add("selected");
    }
  }
  updateDeckSelectUI();
}

function autoSelectDeck() {
  const ownedList = Object.keys(userState.cards).map(id => CARD_DATABASE.find(c => c.id === id)).filter(Boolean);
  // スコア順（合計値の高い順）に3枚選択
  ownedList.sort((a, b) => (b.use + b.fame + b.conv) - (a.use + a.fame + a.conv));
  battleState.playerDeck = ownedList.slice(0, 3);
  
  document.querySelectorAll("#deck-pool .card-item").forEach(elem => {
    if (battleState.playerDeck.some(c => c.id === elem.dataset.id)) {
      elem.classList.add("selected");
    } else {
      elem.classList.remove("selected");
    }
  });
  updateDeckSelectUI();
}

function updateDeckSelectUI() {
  document.getElementById("deck-selected-count").textContent = battleState.playerDeck.length;
  document.getElementById("btn-start-battle").disabled = (battleState.playerDeck.length !== 3);
}

function startBattle() {
  // CPUの手札を決定 (ランダムに3枚)
  const shuffled = [...CARD_DATABASE].sort(() => Math.random() - 0.5);
  battleState.cpuDeck = shuffled.slice(0, 3);
  
  battleState.playerHand = [...battleState.playerDeck];
  battleState.cpuHand = [...battleState.cpuDeck];
  battleState.round = 1;
  battleState.playerScore = 0;
  battleState.cpuScore = 0;
  battleState.selectedPlayerCard = null;

  renderBattleRound();
  goToScreen('screen-battle');
}

function renderBattleRound() {
  document.getElementById("round-indicator").textContent = `ラウンド ${battleState.round} / 3`;
  document.getElementById("player-score").textContent = battleState.playerScore;
  document.getElementById("cpu-score").textContent = battleState.cpuScore;

  // プレイヤーの手札表示
  const handContainer = document.getElementById("player-hand");
  handContainer.innerHTML = "";
  battleState.selectedPlayerCard = null;

  battleState.playerHand.forEach(card => {
    const elem = createCardElement(card, true);
    elem.onclick = () => {
      document.querySelectorAll("#player-hand .card-item").forEach(e => e.classList.remove("selected"));
      elem.classList.add("selected");
      battleState.selectedPlayerCard = card;
      playSound('click');
    };
    handContainer.appendChild(elem);
  });
}

function chooseStat(statType) {
  if (!battleState.selectedPlayerCard) {
    alert("まずは あなたの 手札から 1枚 えらんでね！");
    return;
  }

  // CPUのカード選択思考（難易度別）
  let cpuCard = null;
  if (battleState.cpuDifficulty === 'easy') {
    // よわい：ランダム
    const idx = Math.floor(Math.random() * battleState.cpuHand.length);
    cpuCard = battleState.cpuHand.splice(idx, 1)[0];
  } else if (battleState.cpuDifficulty === 'normal') {
    // ふつう：一番ステータス合計が高いカード
    battleState.cpuHand.sort((a, b) => (b.use + b.fame + b.conv) - (a.use + a.fame + a.conv));
    cpuCard = battleState.cpuHand.shift();
  } else {
    // つよい：選択されたステータスが一番高いカードで対抗
    const getVal = (c) => statType === 'total' ? (c.use + c.fame + c.conv) : c[statType];
    battleState.cpuHand.sort((a, b) => getVal(b) - getVal(a));
    cpuCard = battleState.cpuHand.shift();
  }

  // プレイヤーの手札から使用したカードを除外
  const pIdx = battleState.playerHand.findIndex(c => c.id === battleState.selectedPlayerCard.id);
  battleState.playerHand.splice(pIdx, 1);

  // 勝敗計算
  const pCard = battleState.selectedPlayerCard;
  let pVal = (statType === 'total') ? (pCard.use + pCard.fame + pCard.conv) : pCard[statType];
  let cVal = (statType === 'total') ? (cpuCard.use + cpuCard.fame + cpuCard.conv) : cpuCard[statType];

  let resultText = "";
  if (pVal > cVal) {
    battleState.playerScore++;
    resultText = "あなたの 勝ち！ 🎉";
    playSound('win');
  } else if (pVal < cVal) {
    battleState.cpuScore++;
    resultText = "CPUの 勝ち！ 😢";
  } else {
    resultText = "引き分け！ 🤝";
  }

  // ラウンド結果モーダル表示
  document.getElementById("round-result-title").textContent = resultText;
  document.getElementById("compare-player-card").innerHTML = "";
  document.getElementById("compare-player-card").appendChild(createCardElement(pCard, true));
  document.getElementById("compare-cpu-card").innerHTML = "";
  document.getElementById("compare-cpu-card").appendChild(createCardElement(cpuCard, true));

  const statNames = { use: "つかう度", fame: "ゆうめい度", conv: "べんり度", total: "合計" };
  document.getElementById("round-result-desc").textContent = `${statNames[statType]} 勝負: あなた ${pVal} vs CPU ${cVal}`;

  document.getElementById("round-result-modal").classList.remove("hidden");
}

function nextRound() {
  document.getElementById("round-result-modal").classList.add("hidden");
  if (battleState.round < 3) {
    battleState.round++;
    renderBattleRound();
  } else {
    finishBattle();
  }
}

function finishBattle() {
  const isWin = battleState.playerScore > battleState.cpuScore;
  const finishTitle = document.getElementById("finish-title");
  const rewardBox = document.getElementById("finish-rewards");

  if (isWin) {
    userState.win++;
    userState.coin += 10; // 勝利報酬 10コイン
    // カードゲット報酬 (ランダム1枚)
    const rewardCard = CARD_DATABASE[Math.floor(Math.random() * CARD_DATABASE.length)];
    userState.cards[rewardCard.id] = (userState.cards[rewardCard.id] || 0) + 1;

    finishTitle.textContent = "勝利！ 🏆";
    rewardBox.innerHTML = `
      <p>おめでとう！ 10コイン と 新しいカードを ゲット！</p>
      <div style="display:flex; justify-content:center; margin-top:8px;">
        ${createCardElement(rewardCard, true).outerHTML}
      </div>
    `;
    playSound('win');
  } else {
    userState.lose++;
    userState.coin += 3; // 参加賞 3コイン
    finishTitle.textContent = "ざんねん... 負け";
    rewardBox.innerHTML = `<p>さんか賞として 3コイン ゲット！ つぎは 勝てるよ！</p>`;
  }

  saveAllData();
  updateHeaderUI();
  renderZukan();          // もらった カードを ずかんに 出す
  document.getElementById("battle-finish-modal").classList.remove("hidden");
}

// --- 6. パック購入処理 ---
function buyPack() {
  if (userState.coin < 20) {
    alert("コインが たりません！（1パック 20コイン）");
    return;
  }

  userState.coin -= 20;
  playSound('coin');

  // ランダムに3枚抽選
  const drawnCards = [];
  for (let i = 0; i < 3; i++) {
    const card = CARD_DATABASE[Math.floor(Math.random() * CARD_DATABASE.length)];
    drawnCards.push(card);
    userState.cards[card.id] = (userState.cards[card.id] || 0) + 1;
  }

  saveAllData();
  updateHeaderUI();

  // カード表示（まえに 出した ものを けしてから）
  const visual = document.getElementById("pack-visual");
  const revealArea = document.getElementById("pack-reveal-area");
  visual.style.display = "none";
  revealArea.innerHTML = "";
  renderZukan();          // ずかんも すぐ あたらしく する

  drawnCards.forEach(card => {
    const elem = createCardElement(card, true);
    revealArea.appendChild(elem);
  });
}

// --- 7. ずかん表示＆フィルタ ---
function renderZukan(filter = 'all') {
  const grid = document.getElementById("zukan-grid");
  grid.innerHTML = "";

  CARD_DATABASE.forEach(card => {
    const isOwned = !!userState.cards[card.id];
    const rarity = getRarity(card);

    if (filter === 'owned' && !isOwned) return;
    if (filter === 'rare' && rarity.rank < 4) return;

    const elem = createCardElement(card, isOwned);
    grid.appendChild(elem);
  });
}

function filterZukan(type, btn) {
  document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  renderZukan(type);
}

// --- 8. PT両替処理 ---
function exchangePT() {
  omame.buy("pt_coin100").then(function(r) {
    if (r && r.ok) {
      userState.coin += 100;
      saveAllData();
      updateHeaderUI();
      playSound('coin');
      alert("100 コイン に りょうがえ しました！ 🪙");
    } else {
      alert((r && r.error) ? r.error : "PTが たりないか、キャンセルされました。");
    }
  });
}