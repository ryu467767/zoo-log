/* 写真の全画面表示（ライトボックス）
 *
 * トップページ（app.js）とマイ写真ギャラリーの両方から使う共通部品。
 * 複数枚あるときは横スクロール（スマホはスワイプ）で移動できる。
 *
 * 使い方:
 *   ZooLightbox.open([{ url: "/uploads/xxx.jpg", caption: "園名" }, ...], 0);
 */
(function () {
  var root = null;      // 全画面表示の入れ物
  var track = null;     // 横スクロールする部分
  var countEl = null;
  var captionEl = null;
  var prevBtn = null;
  var nextBtn = null;
  var items = [];
  var index = 0;
  var lastFocus = null;

  function build() {
    if (root) return;

    root = document.createElement("div");
    root.className = "lb";
    root.hidden = true;
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.setAttribute("aria-label", "写真の全画面表示");

    var bar = document.createElement("div");
    bar.className = "lb__bar";

    countEl = document.createElement("span");
    countEl.className = "lb__count";

    var close = document.createElement("button");
    close.type = "button";
    close.className = "lb__close";
    close.textContent = "×";
    close.setAttribute("aria-label", "閉じる");
    close.addEventListener("click", hide);

    bar.appendChild(countEl);
    bar.appendChild(close);

    track = document.createElement("div");
    track.className = "lb__track";
    track.addEventListener("scroll", onScroll, { passive: true });

    prevBtn = document.createElement("button");
    prevBtn.type = "button";
    prevBtn.className = "lb__nav lb__nav--prev";
    prevBtn.innerHTML = "&#8249;";
    prevBtn.setAttribute("aria-label", "前の写真");
    prevBtn.addEventListener("click", function () { go(index - 1); });

    nextBtn = document.createElement("button");
    nextBtn.type = "button";
    nextBtn.className = "lb__nav lb__nav--next";
    nextBtn.innerHTML = "&#8250;";
    nextBtn.setAttribute("aria-label", "次の写真");
    nextBtn.addEventListener("click", function () { go(index + 1); });

    captionEl = document.createElement("div");
    captionEl.className = "lb__caption";

    root.appendChild(bar);
    root.appendChild(track);
    root.appendChild(prevBtn);
    root.appendChild(nextBtn);
    root.appendChild(captionEl);
    document.body.appendChild(root);

    document.addEventListener("keydown", onKey);
  }

  function onKey(e) {
    if (!root || root.hidden) return;
    if (e.key === "Escape") { hide(); return; }
    if (e.key === "ArrowLeft") { go(index - 1); e.preventDefault(); }
    if (e.key === "ArrowRight") { go(index + 1); e.preventDefault(); }
  }

  // スクロール位置から今何枚目かを割り出す
  var scrollTimer = null;
  function onScroll() {
    if (scrollTimer) clearTimeout(scrollTimer);
    scrollTimer = setTimeout(function () {
      var w = track.clientWidth || 1;
      var i = Math.round(track.scrollLeft / w);
      if (i !== index) { index = i; paint(); }
    }, 60);
  }

  function paint() {
    if (index < 0) index = 0;
    if (index > items.length - 1) index = items.length - 1;
    countEl.textContent = items.length > 1 ? (index + 1) + " / " + items.length : "";
    captionEl.textContent = (items[index] && items[index].caption) || "";
    var many = items.length > 1;
    prevBtn.hidden = !many || index === 0;
    nextBtn.hidden = !many || index === items.length - 1;
  }

  function go(i) {
    if (i < 0 || i > items.length - 1) return;
    index = i;
    track.scrollTo({ left: i * track.clientWidth, behavior: "smooth" });
    paint();
  }

  function hide() {
    if (!root || root.hidden) return;
    root.hidden = true;
    document.body.classList.remove("lb-open");
    track.innerHTML = "";
    items = [];
    if (lastFocus && lastFocus.focus) { try { lastFocus.focus(); } catch (e) {} }
    lastFocus = null;
  }

  function open(list, start) {
    if (!list || !list.length) return;
    build();

    items = list;
    index = Math.min(Math.max(start || 0, 0), items.length - 1);
    lastFocus = document.activeElement;

    track.innerHTML = "";
    items.forEach(function (it) {
      var slide = document.createElement("div");
      slide.className = "lb__slide";

      var img = document.createElement("img");
      img.className = "lb__img";
      img.src = it.url;
      img.alt = it.caption || "";
      slide.appendChild(img);

      // 写真の外側（余白）を押したら閉じる
      slide.addEventListener("click", function (e) {
        if (e.target === slide) hide();
      });

      track.appendChild(slide);
    });

    root.hidden = false;
    document.body.classList.add("lb-open");

    // 表示してから位置を合わせる（hidden のままだと幅が 0 で計算できない）
    requestAnimationFrame(function () {
      track.scrollLeft = index * track.clientWidth;
      paint();
    });
  }

  window.ZooLightbox = { open: open, close: hide };
})();
