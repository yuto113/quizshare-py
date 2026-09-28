/* ============================================================
   Omame Group Games ─ ゲームから つかう しくみ
   ============================================================
   <script src="/static/games/omame.js"></script> と 書いて つかう。

     omame.save({stage:5, score:1200})      ほぞんする
     omame.load().then(function(d){ ... })  よみこむ
     omame.items                            もっている もの
     omame.has('kaifuku')                   もっているか
     omame.use('kaifuku')                   1つ つかう
     omame.buy('kaifuku').then(...)         かう
     omame.pt                               いまの PT
   ============================================================ */
(function(){
  var waiting = {};
  var seq = 0;

  function ask(kind, extra){
    return new Promise(function(done){
      var id = ++seq;
      waiting[kind] = waiting[kind] || [];
      waiting[kind].push(done);
      var msg = {omame: kind, id: id};
      if (extra) for (var k in extra) msg[k] = extra[k];
      parent.postMessage(msg, '*');
      // 8びょう たっても 返らなければ あきらめる
      setTimeout(function(){
        var q = waiting[kind] || [];
        var i = q.indexOf(done);
        if (i >= 0){ q.splice(i, 1); done(null); }
      }, 8000);
    });
  }

  window.addEventListener('message', function(e){
    var d = e.data;
    if (!d || !d.omameOk) return;
    var q = waiting[d.omameOk] || [];
    var fn = q.shift();
    if (!fn) return;
    if (d.omameOk === 'load'){
      omame.data = d.data || {};
      omame.items = {};
      (d.items || []).forEach(function(x){ omame.items[x.code] = x.n; });
      fn(omame.data);
    } else if (d.omameOk === 'buy'){
      if (d.result && d.result.ok){
        omame.pt = d.result.pt;
        var c = d.result.item.code;
        omame.items[c] = (omame.items[c] || 0) + 1;
      }
      fn(d.result);
    } else {
      fn(true);
    }
  });

  var omame = {
    data: {},
    items: {},
    pt: 0,

    /* ほぞん */
    save: function(data){
      if (data) omame.data = data;
      return ask('save', {data: omame.data});
    },

    /* よみこみ */
    load: function(){
      return ask('load');
    },

    /* もっているか */
    has: function(code){
      return (omame.items[code] || 0) > 0;
    },

    /* 1つ つかう（へらすのは 見た目だけ。ほんとうに へらしたい ときは
       save で のこす） */
    use: function(code){
      if (!omame.has(code)) return false;
      omame.items[code] = omame.items[code] - 1;
      return true;
    },

    /* かう */
    buy: function(code){
      return ask('buy', {code: code});
    },

    /* かんたんな とくてん ほぞん */
    setScore: function(key, n){
      omame.data = omame.data || {};
      var now = omame.data[key] || 0;
      if (n > now){
        omame.data[key] = n;
        omame.save();
        return true;         // あたらしい きろく
      }
      return false;
    },
    getScore: function(key){
      return (omame.data && omame.data[key]) || 0;
    }
  };

  window.omame = omame;

  /* はじめに じどうで よみこむ */
  omame.ready = omame.load();
})();
