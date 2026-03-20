const $ = (id) => document.getElementById(id);

let state = {
  items: [],
  filter: "all",
  pref: "",
  sort: "pref_plain",
  lastMapKey: "",
  regionOpen: {},
};
state.loggedIn = false;
state.markerById = {};
state.showClosed = false;
state.filterMemo = false;
state.filterPhoto = false;
const selectedAnimals = new Set();
const PREF_ORDER = [
  "北海道",
  "青森県","岩手県","宮城県","秋田県","山形県","福島県",
  "茨城県","栃木県","群馬県","埼玉県","千葉県","東京都","神奈川県",
  "新潟県","富山県","石川県","福井県","山梨県","長野県",
  "岐阜県","静岡県","愛知県","三重県",
  "滋賀県","京都府","大阪府","兵庫県","奈良県","和歌山県",
  "鳥取県","島根県","岡山県","広島県","山口県",
  "徳島県","香川県","愛媛県","高知県",
  "福岡県","佐賀県","長崎県","熊本県","大分県","宮崎県","鹿児島県",
  "沖縄県"
];

const REGION_ORDER = ["北海道","東北","関東","中部","近畿","中国","四国","九州・沖縄"];

const REGION_BY_PREF = {
  "北海道": "北海道",
  "青森県":"東北","岩手県":"東北","宮城県":"東北","秋田県":"東北","山形県":"東北","福島県":"東北",
  "茨城県":"関東","栃木県":"関東","群馬県":"関東","埼玉県":"関東","千葉県":"関東","東京都":"関東","神奈川県":"関東",
  "新潟県":"中部","富山県":"中部","石川県":"中部","福井県":"中部","山梨県":"中部","長野県":"中部","岐阜県":"中部","静岡県":"中部","愛知県":"中部",
  "三重県":"近畿","滋賀県":"近畿","京都府":"近畿","大阪府":"近畿","兵庫県":"近畿","奈良県":"近畿","和歌山県":"近畿",
  "鳥取県":"中国","島根県":"中国","岡山県":"中国","広島県":"中国","山口県":"中国",
  "徳島県":"四国","香川県":"四国","愛媛県":"四国","高知県":"四国",
  "福岡県":"九州・沖縄","佐賀県":"九州・沖縄","長崎県":"九州・沖縄","熊本県":"九州・沖縄","大分県":"九州・沖縄","宮崎県":"九州・沖縄","鹿児島県":"九州・沖縄","沖縄県":"九州・沖縄",
};

function regionOf(pref) {
  return REGION_BY_PREF[pref] || "その他";
}



let map;
let markersLayer;




async function apiGet(path) {
  const res = await fetch(path, { credentials: "same-origin" });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

async function apiPut(path, body) {
  const res = await fetch(path, {
    method: "PUT",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": state.csrfToken || "",
    },
    body: JSON.stringify(body),
  });

  const text = await res.text(); // 先に本文を読む

  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${text || "(empty)"}`);
  }

  // 空レスポ(204など)でも落ちないように
  if (!text) return null;

  // JSONじゃない場合もあるのでtry
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}



function match(item, q) {
  if (!q) return true;
  const t = (item.name + " " + item.prefecture + " " + item.city + " " + item.location_raw).toLowerCase();
  return t.includes(q.toLowerCase());
}

function passesFilter(item) {
  // 閉園した動物園は showClosed が true の時だけ表示
  if (!state.showClosed && item.is_closed) return false;
  // エリアフィルタ（常時有効）
  if (state.pref && item.prefecture !== state.pref) return false;
  if (state.filter === "all") return true;
  if (state.filter === "visited") return item.visited;
  if (state.filter === "want_to_go") return item.want_to_go;
  if (state.filter === "unvisited") return !item.visited;
  if (state.filter === "star") return item.mola_star === 1;
  if (state.filterMemo && !(item.note && item.note.trim())) return false;
  if (state.filterPhoto && !item.has_photos) return false;
  return true;
}

function passesAnimalFilter(item) {
  if (selectedAnimals.size > 0 && item.is_closed) return false;
  for (const animal of selectedAnimals) {
    if (!item[animal]) return false; // AND: 1つでもFalseならNG
  }
  return true;
}


function setPrefOptions(items) {
  const sel = $("pref-filter");
  if (!sel) return; // HTML側に無い場合は何もしない

  const current = sel.value || state.pref || "";
  const prefs = Array.from(
    new Set(items.map((x) => x.prefecture).filter(Boolean))
  ).sort((a, b) => {
    const ai = PREF_ORDER.indexOf(a);
    const bi = PREF_ORDER.indexOf(b);
    if (ai === -1 && bi === -1) return a.localeCompare(b, "ja");
    if (ai === -1) return 1;
    if (bi === -1) return -1;
    return ai - bi;
  });

  // option を作り直し
  sel.innerHTML = `<option value="">都道府県（すべて）</option>`;
  for (const p of prefs) {
    const opt = document.createElement("option");
    opt.value = p;
    opt.textContent = p;
    sel.appendChild(opt);
  }

  // できるだけ選択状態を維持
  sel.value = prefs.includes(current) ? current : "";
  state.pref = sel.value;
}

function showConfirm(title, body) {
  return new Promise((resolve) => {
    const overlay = document.getElementById("confirmModal");
    document.getElementById("confirmTitle").textContent = title;
    document.getElementById("confirmBody").textContent = body;
    overlay.style.display = "flex";

    function cleanup(result) {
      overlay.style.display = "none";
      document.getElementById("confirmOk").onclick = null;
      document.getElementById("confirmCancel").onclick = null;
      resolve(result);
    }

    document.getElementById("confirmOk").onclick = () => cleanup(true);
    document.getElementById("confirmCancel").onclick = () => cleanup(false);
  });
}

function renderCard(it) {
  const card = document.createElement("div");
  card.id = "card-" + it.id;
  card.className = "card" + (it.is_closed ? " closed" : "") + (it.visited ? " is-visited" : "");

  // ★スタンプ風バッジ（訪問済み）
  if (it.visited) {
    const stamp = document.createElement("div");
    stamp.className = "stamp";
    stamp.textContent = "VISITED";
    card.appendChild(stamp);
  }

  let title;

  if (it.url) {
    title = document.createElement("a");
    title.href = it.url;
    title.target = "_blank";
    title.rel = "noopener noreferrer";
    title.className = "title link-title";
    title.textContent = it.name;
  } else {
    title = document.createElement("div");
    title.className = "title";
    title.textContent = it.name;
  }

  card.appendChild(title);

  // 閉園バッジ
  if (it.is_closed) {
    const badge = document.createElement("span");
    badge.className = "closed-badge";
    badge.textContent = "閉園" + (it.closed_at ? " " + it.closed_at : "");
    title.appendChild(badge);
  }

  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = `${it.prefecture}${it.city ? " / " + it.city : ""} ${it.location_raw ? " / " + it.location_raw : ""}`;
  card.appendChild(meta);

  // 動物アイコン行
  const ANIMAL_ICONS = [
    { key: "has_elephant",   icon: "🐘", label: "ゾウ" },
    { key: "has_giraffe",    icon: "🦒", label: "キリン" },
    { key: "has_lion",       icon: "🦁", label: "ライオン" },
    { key: "has_tiger",      icon: "🐯", label: "トラ" },
    { key: "has_panda",      icon: "🐼", label: "パンダ" },
    { key: "has_gorilla",    icon: "🦍", label: "ゴリラ" },
    { key: "has_hippo",      icon: "🦛", label: "カバ" },
    { key: "has_koala",      icon: "🐨", label: "コアラ" },
    { key: "has_polar_bear", icon: "🐻‍❄️", label: "ホッキョクグマ" },
    { key: "has_red_panda",  icon: "🦊", label: "レッサーパンダ" },
  ];
  const animalIcons = ANIMAL_ICONS.filter(a => it[a.key]);
  if (animalIcons.length > 0) {
    const animalRow = document.createElement("div");
    animalRow.className = "card-animals";
    animalIcons.forEach(a => {
      const span = document.createElement("span");
      span.className = "card-animal-tag";
      span.textContent = a.icon + "\u00a0" + a.label;
      animalRow.appendChild(span);
    });
    card.appendChild(animalRow);
  }

  // セパレーター（情報エリア / アクションエリアの区切り）
  const sep = document.createElement("hr");
  sep.className = "card-sep";
  card.appendChild(sep);

  const row = document.createElement("div");
  row.className = "row2";

  const btn = document.createElement("button");
  btn.className = it.visited ? "btn visited" : "btn";
  btn.textContent = it.visited ? "訪問済✅（解除）" : "訪問済にする";

  // 訪問日入力・訪問回数表示（クロージャで参照するため先に宣言）
  const dateRow = document.createElement("div");
  const dateInput = document.createElement("input");
  let countDisplay = null; // ログイン中のみ後でセット

  if (!state.loggedIn) {
    btn.disabled = true;
    // ★追加：連打ガード（1秒以内の再クリックは捨てる）
const now = Date.now();
if (btn.dataset.lastClick && now - Number(btn.dataset.lastClick) < 1000) {
  btn.disabled = false;
  return;
}
btn.dataset.lastClick = String(now);
    btn.title = "ログインすると押せます";
  } else {
    btn.onclick = async () => {
      // ★最初に無効化（連打・二重タップ防止）
      if (btn.disabled) return;
      btn.disabled = true;

      // 解除時は確認ポップアップ
      if (it.visited && !(await showConfirm("訪問済を解除しますか？", "訪問日や訪問回数がリセットされます"))) {
        btn.disabled = false;
        return;
      }

      const newVisited = !it.visited;

      // ① 先にUIを即変更
      it.visited = newVisited;
      card.classList.toggle("is-visited", newVisited);
      btn.className = newVisited ? "btn visited" : "btn";
      btn.textContent = newVisited ? "訪問済✅（解除）" : "訪問済にする";

      // ② VISITEDバッジ（stamp）も即同期
      let stampEl = card.querySelector(".stamp");
      if (newVisited) {
        if (!stampEl) {
          stampEl = document.createElement("div");
          stampEl.className = "stamp";
          stampEl.textContent = "VISITED";
          card.appendChild(stampEl);
        }
      } else {
        if (stampEl) stampEl.remove();
      }

      // ③ 訪問日行の表示切替（楽観的）
      dateRow.style.display = newVisited ? "" : "none";
      if (newVisited && !dateInput.value) {
        const d = new Date();
        dateInput.value = d.getFullYear() + "-"
          + String(d.getMonth() + 1).padStart(2, "0") + "-"
          + String(d.getDate()).padStart(2, "0");
      }

      // ④ 訪問回数の楽観的更新（訪問済みにした時は最低1）
      const oldCount = it.visit_count || 0;
      if (newVisited && oldCount === 0) {
        it.visit_count = 1;
        if (countDisplay) countDisplay.textContent = "1";
      } else if (!newVisited) {
        it.visit_count = 0;
        if (countDisplay) countDisplay.textContent = "0";
      }

      try {
        const res = await apiPut(`/api/zoos/${it.id}/visited`, { visited: newVisited });
        if (res && res.visited_at) {
          it.visited_at = res.visited_at;
          dateInput.value = res.visited_at.slice(0, 10);
        } else if (!newVisited) {
          it.visited_at = null;
          dateInput.value = "";
        }
        if (res && res.visit_count !== undefined) {
          it.visit_count = res.visit_count;
          if (countDisplay) countDisplay.textContent = String(res.visit_count);
        }
        // バッジをリアルタイム更新
        updateBadgesFromState();
      } catch (e) {
        // 失敗したら元に戻す
        it.visited = !newVisited;
        card.classList.toggle("is-visited", !newVisited);
        btn.className = !newVisited ? "btn visited" : "btn";
        btn.textContent = !newVisited ? "訪問済✅（解除）" : "訪問済にする";

        let stampEl2 = card.querySelector(".stamp");
        if (!newVisited) {
          if (!stampEl2) {
            stampEl2 = document.createElement("div");
            stampEl2.className = "stamp";
            stampEl2.textContent = "VISITED";
            card.appendChild(stampEl2);
          }
        } else {
          if (stampEl2) stampEl2.remove();
        }

        dateRow.style.display = !newVisited ? "" : "none";
        // 訪問回数も元に戻す
        it.visit_count = oldCount;
        if (countDisplay) countDisplay.textContent = String(oldCount);
        alert("APIエラー: " + e.message);
      } finally {
        btn.disabled = false;
      }
    };
  }

  row.appendChild(btn);

  // 「行きたい」ボタン（ログイン中のみ、閉園は除外）
  if (state.loggedIn && !it.is_closed) {
    const wantBtn = document.createElement("button");
    wantBtn.type = "button";
    wantBtn.className = it.want_to_go ? "btn-want active" : "btn-want";
    wantBtn.textContent = it.want_to_go ? "行きたい★" : "行きたい☆";
    wantBtn.onclick = async () => {
      if (wantBtn.disabled) return;
      wantBtn.disabled = true;
      const newVal = !it.want_to_go;
      it.want_to_go = newVal;
      wantBtn.className = newVal ? "btn-want active" : "btn-want";
      wantBtn.textContent = newVal ? "行きたい★" : "行きたい☆";
      refreshMarker(it); // 地図マーカーを即時更新
      try {
        await apiPut(`/api/zoos/${it.id}/want_to_go`, { want_to_go: newVal });
      } catch (e) {
        it.want_to_go = !newVal;
        wantBtn.className = !newVal ? "btn-want active" : "btn-want";
        wantBtn.textContent = !newVal ? "行きたい★" : "行きたい☆";
        alert("APIエラー: " + e.message);
      } finally {
        wantBtn.disabled = false;
      }
    };
    row.appendChild(wantBtn);
  }

  card.appendChild(row);

  // 訪問日行のセットアップ
  dateRow.className = "visit-date-row";
  dateRow.style.display = it.visited ? "" : "none";

  const dateLbl = document.createElement("span");
  dateLbl.className = "visit-date-label";
  dateLbl.textContent = "訪問日：";

  dateInput.type = "date";
  dateInput.className = "visit-date-input";
  dateInput.disabled = !state.loggedIn;
  if (it.visited_at) {
    dateInput.value = it.visited_at.slice(0, 10);
  }
  dateInput.onchange = async () => {
    if (!state.loggedIn) return;
    try {
      const res = await apiPut(`/api/zoos/${it.id}/visited_at`, { visited_at: dateInput.value || null });
      if (res) it.visited_at = res.visited_at;
    } catch (e) {
      alert("日付の保存に失敗: " + e.message);
    }
  };

  dateRow.appendChild(dateLbl);
  dateRow.appendChild(dateInput);
  card.appendChild(dateRow);

  // 訪問回数行（ログイン中のみ）
  if (state.loggedIn) {
    const countRow = document.createElement("div");
    countRow.className = "visit-count-row";

    const countLbl = document.createElement("span");
    countLbl.className = "visit-count-label";
    countLbl.textContent = "訪問回数：";

    const minusBtn = document.createElement("button");
    minusBtn.type = "button";
    minusBtn.className = "count-btn";
    minusBtn.textContent = "−";

    countDisplay = document.createElement("span");
    countDisplay.className = "count-display";
    countDisplay.textContent = String(it.visit_count || 0);

    const plusBtn = document.createElement("button");
    plusBtn.type = "button";
    plusBtn.className = "count-btn";
    plusBtn.textContent = "＋";

    async function saveCount(newCount) {
      if (newCount < 0) return;
      const old = it.visit_count || 0;
      it.visit_count = newCount;
      countDisplay.textContent = String(newCount);
      try {
        await apiPut(`/api/zoos/${it.id}/visit_count`, { visit_count: newCount });
      } catch (e) {
        it.visit_count = old;
        countDisplay.textContent = String(old);
        alert("保存に失敗: " + e.message);
      }
    }

    minusBtn.onclick = () => saveCount((it.visit_count || 0) - 1);
    plusBtn.onclick = () => saveCount((it.visit_count || 0) + 1);

    countRow.appendChild(countLbl);
    countRow.appendChild(minusBtn);
    countRow.appendChild(countDisplay);
    countRow.appendChild(plusBtn);
    card.appendChild(countRow);

    // 訪問履歴（複数日付）セクション
    const datesRow = document.createElement("div");
    datesRow.className = "visit-years-row";

    let currentDates = Array.isArray(it.visit_dates) ? [...it.visit_dates] : [];

    async function saveDates(dates) {
      try {
        const res = await apiPut(`/api/zoos/${it.id}/visit_dates`, { visit_dates: dates });
        if (res && res.visit_dates) {
          it.visit_dates = res.visit_dates;
          currentDates = [...res.visit_dates];
        }
      } catch (e) {
        alert("保存に失敗: " + e.message);
      }
    }

    function fmtDate(iso) {
      const [y, m, d] = iso.split("-");
      return `${y}/${parseInt(m)}/${parseInt(d)}`;
    }

    function renderDates() {
      datesRow.innerHTML = "";
      const lbl = document.createElement("span");
      lbl.className = "visit-years-label";
      lbl.textContent = "訪問履歴：";
      datesRow.appendChild(lbl);

      const chipsWrap = document.createElement("span");
      chipsWrap.className = "visit-years-chips";
      currentDates.forEach(d => {
        const chip = document.createElement("span");
        chip.className = "visit-year-chip";
        chip.textContent = fmtDate(d);
        const del = document.createElement("button");
        del.type = "button";
        del.className = "visit-year-del";
        del.textContent = "×";
        del.title = d + " を削除";
        del.onclick = async () => {
          currentDates = currentDates.filter(x => x !== d);
          renderDates();
          await saveDates(currentDates);
        };
        chip.appendChild(del);
        chipsWrap.appendChild(chip);
      });
      datesRow.appendChild(chipsWrap);

      const addBtn = document.createElement("button");
      addBtn.type = "button";
      addBtn.className = "visit-year-add";
      addBtn.textContent = "＋";
      addBtn.title = "訪問日を追加";
      addBtn.onclick = () => {
        const today = new Date().toISOString().slice(0, 10);
        const input = document.createElement("input");
        input.type = "date";
        input.className = "visit-year-input";
        input.value = today;
        addBtn.replaceWith(input);
        input.focus();
        async function confirmDate() {
          const val = input.value;
          if (val && !currentDates.includes(val)) {
            currentDates = [...currentDates, val].sort();
            await saveDates(currentDates);
          }
          renderDates();
        }
        input.onblur = confirmDate;
        input.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); input.blur(); } if (e.key === "Escape") renderDates(); };
      };
      datesRow.appendChild(addBtn);
    }

    renderDates();
    card.appendChild(datesRow);
  }

  const note = document.createElement("textarea");
  note.className = "note";
  note.placeholder = "メモ（例：混雑、動物の様子、感想）";
  note.value = it.note || "";
  note.disabled = !state.loggedIn;
if (!state.loggedIn) note.placeholder = "ログインするとメモできます";
  note.onchange = async () => {
    if (!state.loggedIn) return;
    try {
      await apiPut(`/api/zoos/${it.id}/note`, { note: note.value });
    } catch (e) {
      alert("APIエラー: " + e.message);
    }
  };
  card.appendChild(note);
    // ===== Photos UI (logged in only) =====
    const photosWrap = document.createElement("div");
    photosWrap.className = "photos";

    const thumbs = document.createElement("div");
    thumbs.className = "thumbs";
    photosWrap.appendChild(thumbs);

    async function refreshPhotos() {
      thumbs.innerHTML = "";
      if (!state.loggedIn) {
        const msg = document.createElement("div");
        msg.className = "photos-guest";
        msg.textContent = "ログインすると写真を追加できます";
        thumbs.appendChild(msg);
        return;
      }

      try {
        const list = await apiGet(`/api/zoos/${it.id}/photos`);
        for (const p of list) {
          const item = document.createElement("div");
          item.className = "thumb-item";

          const img = document.createElement("img");
          img.className = "thumb";
          img.src = p.url;
          img.loading = "lazy";
          item.appendChild(img);

          const del = document.createElement("button");
          del.type = "button";
          del.className = "thumb-del";
          del.textContent = "×";
          del.title = "削除";
          del.onclick = async (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (!confirm("この写真を削除しますか？")) return;

            try {
              const res = await fetch(`/api/zoos/${it.id}/photos/${p.id}`, {
                method: "DELETE",
                credentials: "same-origin",
                headers: { "X-CSRF-Token": state.csrfToken },
              });
              if (!res.ok) throw new Error(await res.text());
              await refreshPhotos();
            } catch (err) {
              alert("削除に失敗: " + err.message);
            }
          };
          item.appendChild(del);

          thumbs.appendChild(item);
        }
      } catch (e) {
        console.warn("photos fetch failed:", e);
      }
    }

  // アップロード input（非表示）
  const up = document.createElement("input");
  up.type = "file";
  up.accept = "image/*";
  up.className = "photo-input-hidden";
  up.disabled = !state.loggedIn;

  // 見た目用ボタン
  const pickBtn = document.createElement("button");
  pickBtn.type = "button";
  pickBtn.className = "photo-btn";
  pickBtn.textContent = state.loggedIn ? "写真を追加" : "ログインして写真を追加";

  // 未ログインでも押せるようにする（押したらログインへ）
  pickBtn.disabled = false;

  pickBtn.onclick = () => {
    if (!state.loggedIn) {
      location.href = "/login";
      return;
    }

    // 429対策：写真一覧は必要になった時だけ読む（初回だけ）
    if (!photosWrap.dataset.loaded) {
      photosWrap.dataset.loaded = "1";
      refreshPhotos();
    }

    up.click();
  };

  up.onchange = async () => {
    if (!state.loggedIn) return;
    const file = up.files && up.files[0];
    if (!file) return;

    const fd = new FormData();
    fd.append("file", file);

    try {
      const res = await fetch(`/api/zoos/${it.id}/photos`, {
        method: "POST",
        body: fd,
        credentials: "same-origin",
        headers: { "X-CSRF-Token": state.csrfToken },
      });
      if (!res.ok) throw new Error(await res.text());
      up.value = "";
      await refreshPhotos();
    } catch (e) {
      alert("写真アップロード失敗: " + e.message);
    }
  };

  photosWrap.appendChild(pickBtn);
  photosWrap.appendChild(up);
    card.appendChild(photosWrap);


  return card;
}



function render() {
  const q = $("q").value.trim();
  const list = $("list");
  list.innerHTML = "";

  let items = state.items.filter((x) => match(x, q)).filter(passesFilter).filter(passesAnimalFilter);

  const ja = (a, b) => (a ?? "").toString().localeCompare((b ?? "").toString(), "ja");

  if (state.sort === "name") {
    items.sort((a, b) => ja(a.name, b.name));

  } else if (state.sort === "pref_plain") {
    // 地域分けしない「都道府県順」（デフォルト）
    items.sort((a, b) => {
      const ai = PREF_ORDER.indexOf(a.prefecture);
      const bi = PREF_ORDER.indexOf(b.prefecture);

      if (ai === -1 && bi === -1) return ja(a.name, b.name);
      if (ai === -1) return 1;
      if (bi === -1) return -1;

      if (ai !== bi) return ai - bi;
      return ja(a.name, b.name);
    });

  } else if (state.sort === "pref") {
    // 地域別
    items.sort((a, b) => {
      const ra = regionOf(a.prefecture);
      const rb = regionOf(b.prefecture);
      return ra.localeCompare(rb) || ja(a.name, b.name);
    });

  } else if (state.sort === "visit_count") {
    // 訪問回数順（未訪問は非表示）
    items = items.filter(x => x.visited);
    items.sort((a, b) => (b.visit_count || 0) - (a.visit_count || 0) || ja(a.name, b.name));

  } else if (state.sort === "visited_at") {
    // 訪問日順（新しい順）、未訪問は末尾
    items.sort((a, b) => {
      if (!a.visited_at && !b.visited_at) return ja(a.name, b.name);
      if (!a.visited_at) return 1;
      if (!b.visited_at) return -1;
      return b.visited_at.localeCompare(a.visited_at);
    });
  }

  // --- 描画（prefなら地域セクション、nameならフラット）
  if (state.sort === "pref") {
    const groups = new Map(); // region -> items[]
    for (const it of items) {
      const r = regionOf(it.prefecture);
      if (!groups.has(r)) groups.set(r, []);
      groups.get(r).push(it);
    }

    for (const r of REGION_ORDER) {
      const arr = groups.get(r);
      if (!arr || arr.length === 0) continue;

      if (state.regionOpen[r] === undefined) state.regionOpen[r] = false;

      const header = document.createElement("button");
      header.className = "regionHeadingBtn";
      header.type = "button";
      const done = arr.filter((x) => x.visited).length;
      header.textContent = `${state.regionOpen[r] ? "▼" : "▶"} ${r}（${done}/${arr.length}）`;
      header.onclick = () => {
        state.regionOpen[r] = !state.regionOpen[r];
        render();
      };
      list.appendChild(header);

      const body = document.createElement("div");
      body.className = "regionBody";
      body.style.display = state.regionOpen[r] ? "" : "none";
      list.appendChild(body);

      for (const it of arr) {
        body.appendChild(renderCard(it));
      }
    }
  } else {
    for (const it of items) {
      list.appendChild(renderCard(it));
    }
  }

  // --- 地図更新（ここは必ず通す）
  const mapKey = `${state.filter}|${state.pref}|${q}`;
  const shouldFit = state.lastMapKey !== mapKey;
  state.lastMapKey = mapKey;

  updateMap(items, { fit: shouldFit });
}

// ===== 達成バッジ =====
const BADGES = [
  { id: "v10",  label: "10園達成",  icon: "🥉", cat: "visit",
    check: (items) => items.filter(x => !x.is_closed && x.visited).length >= 10 },
  { id: "v30",  label: "30園達成",  icon: "🥈", cat: "visit",
    check: (items) => items.filter(x => !x.is_closed && x.visited).length >= 30 },
  { id: "v50",  label: "50園達成",  icon: "🥇", cat: "visit",
    check: (items) => items.filter(x => !x.is_closed && x.visited).length >= 50 },
  { id: "v100", label: "100園達成", icon: "🏆", cat: "visit",
    check: (items) => items.filter(x => !x.is_closed && x.visited).length >= 100 },
  { id: "all",  label: "全国制覇",  icon: "👑", cat: "visit",
    check: (items) => { const o = items.filter(x => !x.is_closed); return o.length > 0 && o.every(x => x.visited); } },
  ...REGION_ORDER.map(r => ({
    id: "r_" + r, label: r + "制覇", icon: "🗾", cat: "region",
    check: (items) => {
      const rr = items.filter(x => !x.is_closed && regionOf(x.prefecture) === r);
      return rr.length > 0 && rr.every(x => x.visited);
    }
  })),
  // ゾウ
  { id: "a_elephant_3",   label: "ゾウ好き",         icon: "🐘", cat: "animal",
    check: (items) => items.filter(x => !x.is_closed && x.visited && x.has_elephant).length >= 3 },
  { id: "a_elephant_5",   label: "ゾウ大好き",       icon: "🐘", cat: "animal",
    check: (items) => items.filter(x => !x.is_closed && x.visited && x.has_elephant).length >= 5 },
  { id: "a_elephant_10",  label: "ゾウマスター",     icon: "🐘", cat: "animal",
    check: (items) => items.filter(x => !x.is_closed && x.visited && x.has_elephant).length >= 10 },
  { id: "a_elephant_all", label: "ゾウ制覇",         icon: "🐘", cat: "animal",
    check: (items) => { const w = items.filter(x => !x.is_closed && x.has_elephant); return w.length > 0 && w.every(x => x.visited); } },
  // キリン
  { id: "a_giraffe_3",   label: "キリン好き",        icon: "🦒", cat: "animal",
    check: (items) => items.filter(x => !x.is_closed && x.visited && x.has_giraffe).length >= 3 },
  { id: "a_giraffe_5",   label: "キリン大好き",      icon: "🦒", cat: "animal",
    check: (items) => items.filter(x => !x.is_closed && x.visited && x.has_giraffe).length >= 5 },
  { id: "a_giraffe_10",  label: "キリンマスター",    icon: "🦒", cat: "animal",
    check: (items) => items.filter(x => !x.is_closed && x.visited && x.has_giraffe).length >= 10 },
  { id: "a_giraffe_all", label: "キリン制覇",        icon: "🦒", cat: "animal",
    check: (items) => { const w = items.filter(x => !x.is_closed && x.has_giraffe); return w.length > 0 && w.every(x => x.visited); } },
  // ライオン
  { id: "a_lion_3",   label: "ライオン好き",         icon: "🦁", cat: "animal",
    check: (items) => items.filter(x => !x.is_closed && x.visited && x.has_lion).length >= 3 },
  { id: "a_lion_5",   label: "ライオン大好き",       icon: "🦁", cat: "animal",
    check: (items) => items.filter(x => !x.is_closed && x.visited && x.has_lion).length >= 5 },
  { id: "a_lion_10",  label: "ライオンマスター",     icon: "🦁", cat: "animal",
    check: (items) => items.filter(x => !x.is_closed && x.visited && x.has_lion).length >= 10 },
  { id: "a_lion_all", label: "ライオン制覇",         icon: "🦁", cat: "animal",
    check: (items) => { const w = items.filter(x => !x.is_closed && x.has_lion); return w.length > 0 && w.every(x => x.visited); } },
  // トラ
  { id: "a_tiger_3",   label: "トラ好き",            icon: "🐯", cat: "animal",
    check: (items) => items.filter(x => !x.is_closed && x.visited && x.has_tiger).length >= 3 },
  { id: "a_tiger_5",   label: "トラ大好き",          icon: "🐯", cat: "animal",
    check: (items) => items.filter(x => !x.is_closed && x.visited && x.has_tiger).length >= 5 },
  { id: "a_tiger_10",  label: "トラマスター",        icon: "🐯", cat: "animal",
    check: (items) => items.filter(x => !x.is_closed && x.visited && x.has_tiger).length >= 10 },
  { id: "a_tiger_all", label: "トラ制覇",            icon: "🐯", cat: "animal",
    check: (items) => { const w = items.filter(x => !x.is_closed && x.has_tiger); return w.length > 0 && w.every(x => x.visited); } },
  // パンダ
  { id: "a_panda_3",   label: "パンダ好き",          icon: "🐼", cat: "animal",
    check: (items) => items.filter(x => !x.is_closed && x.visited && x.has_panda).length >= 3 },
  { id: "a_panda_5",   label: "パンダ大好き",        icon: "🐼", cat: "animal",
    check: (items) => items.filter(x => !x.is_closed && x.visited && x.has_panda).length >= 5 },
  { id: "a_panda_all", label: "パンダ制覇",          icon: "🐼", cat: "animal",
    check: (items) => { const w = items.filter(x => !x.is_closed && x.has_panda); return w.length > 0 && w.every(x => x.visited); } },
  // ゴリラ
  { id: "a_gorilla_3",   label: "ゴリラ好き",        icon: "🦍", cat: "animal",
    check: (items) => items.filter(x => !x.is_closed && x.visited && x.has_gorilla).length >= 3 },
  { id: "a_gorilla_5",   label: "ゴリラ大好き",      icon: "🦍", cat: "animal",
    check: (items) => items.filter(x => !x.is_closed && x.visited && x.has_gorilla).length >= 5 },
  { id: "a_gorilla_all", label: "ゴリラ制覇",        icon: "🦍", cat: "animal",
    check: (items) => { const w = items.filter(x => !x.is_closed && x.has_gorilla); return w.length > 0 && w.every(x => x.visited); } },
  // カバ
  { id: "a_hippo_3",   label: "カバ好き",            icon: "🦛", cat: "animal",
    check: (items) => items.filter(x => !x.is_closed && x.visited && x.has_hippo).length >= 3 },
  { id: "a_hippo_5",   label: "カバ大好き",          icon: "🦛", cat: "animal",
    check: (items) => items.filter(x => !x.is_closed && x.visited && x.has_hippo).length >= 5 },
  { id: "a_hippo_all", label: "カバ制覇",            icon: "🦛", cat: "animal",
    check: (items) => { const w = items.filter(x => !x.is_closed && x.has_hippo); return w.length > 0 && w.every(x => x.visited); } },
  // コアラ
  { id: "a_koala_3",   label: "コアラ好き",          icon: "🐨", cat: "animal",
    check: (items) => items.filter(x => !x.is_closed && x.visited && x.has_koala).length >= 3 },
  { id: "a_koala_5",   label: "コアラ大好き",        icon: "🐨", cat: "animal",
    check: (items) => items.filter(x => !x.is_closed && x.visited && x.has_koala).length >= 5 },
  { id: "a_koala_all", label: "コアラ制覇",          icon: "🐨", cat: "animal",
    check: (items) => { const w = items.filter(x => !x.is_closed && x.has_koala); return w.length > 0 && w.every(x => x.visited); } },
  // ホッキョクグマ
  { id: "a_polar_bear_3",   label: "ホッキョクグマ好き",    icon: "🐻‍❄️", cat: "animal",
    check: (items) => items.filter(x => !x.is_closed && x.visited && x.has_polar_bear).length >= 3 },
  { id: "a_polar_bear_5",   label: "ホッキョクグマ大好き",  icon: "🐻‍❄️", cat: "animal",
    check: (items) => items.filter(x => !x.is_closed && x.visited && x.has_polar_bear).length >= 5 },
  { id: "a_polar_bear_all", label: "ホッキョクグマ制覇",    icon: "🐻‍❄️", cat: "animal",
    check: (items) => { const w = items.filter(x => !x.is_closed && x.has_polar_bear); return w.length > 0 && w.every(x => x.visited); } },
  // レッサーパンダ
  { id: "a_red_panda_3",   label: "レッサーパンダ好き",    icon: "🦊", cat: "animal",
    check: (items) => items.filter(x => !x.is_closed && x.visited && x.has_red_panda).length >= 3 },
  { id: "a_red_panda_5",   label: "レッサーパンダ大好き",  icon: "🦊", cat: "animal",
    check: (items) => items.filter(x => !x.is_closed && x.visited && x.has_red_panda).length >= 5 },
  { id: "a_red_panda_10",  label: "レッサーパンダマスター",icon: "🦊", cat: "animal",
    check: (items) => items.filter(x => !x.is_closed && x.visited && x.has_red_panda).length >= 10 },
  { id: "a_red_panda_all", label: "レッサーパンダ制覇",    icon: "🦊", cat: "animal",
    check: (items) => { const w = items.filter(x => !x.is_closed && x.has_red_panda); return w.length > 0 && w.every(x => x.visited); } },
];

function renderBadges(items) {
  const el = document.getElementById("badgeContainer");
  if (!el || !state.loggedIn) return;
  el.innerHTML = "";

  // 動物バッジ：種別ごとに最高達成ティアのみ表示
  const earnedAnimalIds = new Set(
    BADGES.filter(b => b.cat === "animal" && b.check(items)).map(b => b.id)
  );
  const animalTierSuffixes = ["_3", "_5", "_10", "_all"];
  const animalKeys = [...new Set(
    BADGES.filter(b => b.cat === "animal").map(b => b.id.replace(/_(?:3|5|10|all)$/, ""))
  )];
  const showAnimalIds = new Set();
  for (const key of animalKeys) {
    let highest = null;
    for (const sfx of animalTierSuffixes) {
      if (earnedAnimalIds.has(key + sfx)) highest = key + sfx;
    }
    if (highest) showAnimalIds.add(highest);
  }

  // 達成済みバッジのみ表示（動物バッジは最高ティアのみ）
  for (const b of BADGES) {
    if (!b.check(items)) continue;
    if (b.cat === "animal" && !showAnimalIds.has(b.id)) continue;
    const div = document.createElement("div");
    let cls = "badge earned";
    if (b.cat === "region") cls += " region-badge";
    if (b.cat === "animal") cls += " animal-badge";
    div.className = cls;
    div.title = b.label;
    div.textContent = b.icon + " " + b.label;
    el.appendChild(div);
  }

  // 都道府県制覇バッジ（達成済みのみ）
  const prefs = [...new Set(items.filter(x => !x.is_closed).map(x => x.prefecture))];
  const completedPrefs = prefs.filter(p => {
    const inPref = items.filter(x => x.prefecture === p && !x.is_closed);
    return inPref.length > 0 && inPref.every(x => x.visited);
  });
  for (const p of completedPrefs) {
    const div = document.createElement("div");
    div.className = "badge earned pref-badge";
    div.textContent = "📍 " + p + "制覇";
    el.appendChild(div);
  }
}

// 訪問ボタン押下後にバッジをリアルタイム更新する
function updateBadgesFromState() {
  if (!state.loggedIn || !state.items.length) return;
  renderBadges(state.items);
}

// ===== SNSシェア =====
function generateShareImage(visited, total, items) {
  const canvas = document.createElement("canvas");
  canvas.width = 800;
  canvas.height = 450;
  const ctx = canvas.getContext("2d");

  // 背景グラデーション（緑）
  const grad = ctx.createLinearGradient(0, 0, 800, 450);
  grad.addColorStop(0, "#1b4332");
  grad.addColorStop(1, "#2d6a4f");
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, 800, 450);

  ctx.beginPath();
  ctx.arc(680, 70, 120, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(255,255,255,0.05)";
  ctx.fill();

  // タイトル
  ctx.fillStyle = "white";
  ctx.font = "bold 25px sans-serif";
  ctx.textAlign = "center";
  ctx.fillText("🐘 全国動物園スタンプラリー", 400, 70);

  // 区切り線
  ctx.beginPath();
  ctx.moveTo(150, 95);
  ctx.lineTo(650, 95);
  ctx.strokeStyle = "rgba(255,255,255,0.3)";
  ctx.lineWidth = 1;
  ctx.stroke();

  // 訪問数（大きい数字）
  ctx.fillStyle = "white";
  ctx.font = "bold 105px sans-serif";
  ctx.fillText(`${visited}園`, 400, 232);

  // サブテキスト
  ctx.font = "bold 27px sans-serif";
  ctx.fillStyle = "rgba(255,255,255,0.85)";
  ctx.fillText(`訪問達成！（全${total}園中）`, 400, 284);

  // 達成率
  const pct = total > 0 ? Math.round(visited / total * 100) : 0;
  ctx.font = "bold 24px sans-serif";
  ctx.fillStyle = "#ffe066";
  ctx.fillText(`達成率 ${pct}%`, 400, 322);

  // ===== 達成バッジを描画 =====
  const earnedBadges = [];
  if (items && items.length) {
    // 動物バッジは最高ティアのみ
    const animalTierSuffixes = ["_3", "_5", "_10", "_all"];
    const animalKeys = [...new Set(
      BADGES.filter(b => b.cat === "animal").map(b => b.id.replace(/_(?:3|5|10|all)$/, ""))
    )];
    const showAnimalIds = new Set();
    for (const key of animalKeys) {
      let highest = null;
      for (const sfx of animalTierSuffixes) {
        const found = BADGES.find(bb => bb.id === key + sfx);
        if (found && found.check(items)) highest = key + sfx;
      }
      if (highest) showAnimalIds.add(highest);
    }
    for (const b of BADGES) {
      if (!b.check(items)) continue;
      if (b.cat === "animal" && !showAnimalIds.has(b.id)) continue;
      earnedBadges.push(b);
    }
  }

  if (earnedBadges.length > 0) {
    ctx.font = "15px sans-serif";
    const maxShow = Math.min(earnedBadges.length, 8);
    const perRow = Math.min(4, maxShow);
    const pillH = 26;
    const pillPad = 12;
    const gap = 8;

    for (let rowStart = 0; rowStart < maxShow; rowStart += perRow) {
      const row = earnedBadges.slice(rowStart, rowStart + perRow);
      const labels = row.map(b => b.icon + " " + b.label);
      const pillWidths = labels.map(t => ctx.measureText(t).width + pillPad * 2);
      const totalW = pillWidths.reduce((a, c) => a + c, 0) + gap * (row.length - 1);
      let x = 400 - totalW / 2;
      const rowY = 355 + Math.floor(rowStart / perRow) * 34;

      labels.forEach((label, i) => {
        const pw = pillWidths[i];
        // 丸角背景
        const r = 13;
        ctx.fillStyle = "rgba(255,255,255,0.18)";
        ctx.beginPath();
        ctx.moveTo(x + r, rowY);
        ctx.lineTo(x + pw - r, rowY);
        ctx.quadraticCurveTo(x + pw, rowY, x + pw, rowY + r);
        ctx.lineTo(x + pw, rowY + pillH - r);
        ctx.quadraticCurveTo(x + pw, rowY + pillH, x + pw - r, rowY + pillH);
        ctx.lineTo(x + r, rowY + pillH);
        ctx.quadraticCurveTo(x, rowY + pillH, x, rowY + pillH - r);
        ctx.lineTo(x, rowY + r);
        ctx.quadraticCurveTo(x, rowY, x + r, rowY);
        ctx.fill();
        // テキスト
        ctx.fillStyle = "rgba(255,255,255,0.9)";
        ctx.textAlign = "left";
        ctx.fillText(label, x + pillPad, rowY + pillH - 7);
        x += pw + gap;
      });
    }
  }

  // URL
  ctx.textAlign = "center";
  ctx.font = "14px sans-serif";
  ctx.fillStyle = "rgba(255,255,255,0.4)";
  ctx.fillText("zoo-log.onrender.com", 400, 440);

  return canvas.toDataURL("image/png");
}

function handleShare() {
  const statsText = document.getElementById("statsText");
  const text = statsText ? statsText.textContent : "0 / 0";
  const parts = text.split("/").map(s => parseInt(s.trim()) || 0);
  const visited = parts[0] || 0;
  const total = parts[1] || 0;

  const dataUrl = generateShareImage(visited, total, state.items);

  // プレビュー画像をセットしてモーダルを開く
  const previewImg = document.getElementById("sharePreviewImg");
  const shareModal = document.getElementById("shareModal");
  if (previewImg) previewImg.src = dataUrl;
  if (shareModal) shareModal.style.display = "flex";

  // 「Xにポストする」ボタン
  const shareApiBtn = document.getElementById("shareApiBtn");
  if (shareApiBtn) {
    shareApiBtn.onclick = async () => {
      const tweetText = `${visited}園訪問達成！（全${total}園中 ${Math.round(visited/Math.max(total,1)*100)}%）\n#全国動物園スタンプラリー\nhttps://zoo-log.onrender.com/`;
      // スマホ: 画像付きWeb Share → その後Xを開く
      const res = await fetch(dataUrl);
      const blob = await res.blob();
      const file = new File([blob], "zoo_stamp.png", { type: "image/png" });
      if (navigator.share && navigator.canShare && navigator.canShare({ files: [file] })) {
        try {
          await navigator.share({ files: [file], title: "全国動物園スタンプラリー", text: tweetText });
          return;
        } catch (e) {
          if (e.name === "AbortError") return;
        }
      }
      // デスクトップ or フォールバック: 画像を保存 + X Web Intent を開く
      const a = document.createElement("a");
      a.href = dataUrl;
      a.download = "zoo_stamp.png";
      a.click();
      const xUrl = "https://x.com/intent/tweet?text=" + encodeURIComponent(tweetText);
      window.open(xUrl, "_blank", "noopener");
    };
  }

  // 「画像を保存」ボタン
  const shareDownloadBtn = document.getElementById("shareDownloadBtn");
  if (shareDownloadBtn) {
    shareDownloadBtn.onclick = () => {
      const a = document.createElement("a");
      a.href = dataUrl;
      a.download = "zoo_stamp.png";
      a.click();
    };
  }
}

async function load() {
  const items = await apiGet(state.loggedIn ? "/api/zoos" : "/api/public/zoos");

  let stats = { visited: 0, total: items.filter(x => !x.is_closed).length };

  if (state.loggedIn) {
    try {
      stats = await apiGet("/api/stats");
    } catch (e) {
      console.warn("stats取得失敗:", e);
    }
  }

  state.items = items;
  setPrefOptions(items);
  initMap();

  const visited = Number(stats.visited || 0);
  const total = Math.max(1, Number(stats.total || 0));
  const pct = Math.round((visited / total) * 100);

  const statsTextEl = document.getElementById("statsText");
  const statsPctEl = document.getElementById("statsPct");
  const barEl = document.getElementById("progressBar");

  if (statsTextEl && barEl) {
    statsTextEl.textContent = `${visited} / ${total}`;
    if (statsPctEl) statsPctEl.textContent = `${pct}%`;
    barEl.style.width = `${pct}%`;
  }

  // stats dashboard の表示制御（ログイン時のみ表示）
  const dashboardEl = document.getElementById('statsDashboard');
  if (dashboardEl) dashboardEl.style.display = state.loggedIn ? '' : 'none';

  // シェアボタン・ギャラリーボタン（ログイン時のみ表示）
  const shareBtnEl = document.getElementById("shareBtn");
  if (shareBtnEl) shareBtnEl.style.display = state.loggedIn ? "" : "none";
  const galleryBtnEl = document.getElementById("galleryBtn");
  if (galleryBtnEl) galleryBtnEl.style.display = state.loggedIn ? "" : "none";

  // 達成バッジ
  renderBadges(items);

  render();
}

state.csrfToken = "";

async function initCsrf() {
  try {
    const res = await fetch("/api/csrf", { credentials: "same-origin" });
    const j = await res.json();
    state.csrfToken = j.token || "";
  } catch (e) {
    console.warn("csrf init failed", e);
  }
}

async function apiMe() {
  try {
    const res = await fetch("/api/me", { credentials: "same-origin" });
    if (!res.ok) return { logged_in: false };
    return await res.json();
  } catch {
    return { logged_in: false };
  }
}

function setLoginStatus(me) {
  const statusEl = document.getElementById("loginStatus");
  const loginBtn = document.getElementById("googleLogin");
  const logoutBtn = document.getElementById("logoutBtn");

  if (!statusEl || !loginBtn || !logoutBtn) return;

  // ★CTAも毎回ここで同期
  updateLoginCta(me);

  if (!me || !me.logged_in) {
    statusEl.textContent = "";
    loginBtn.style.display = "";
    logoutBtn.style.display = "none";
    return;
  }

  statusEl.textContent = `${me.name || me.email || me.user_id} でログイン中`;
  loginBtn.style.display = "none";
  logoutBtn.style.display = "";
}

function updateLoginCta(me) {
  const cta = document.getElementById("loginCta");
  const btn = document.getElementById("loginCtaBtn");
  if (!cta || !btn) return;

  const loggedIn = !!(me && me.logged_in);

  cta.style.display = loggedIn ? "none" : "";

  // アプリ紹介（未ログイン時のみ表示）
  const intro = document.getElementById("appIntro");
  if (intro) intro.style.display = loggedIn ? "none" : "";

  // ログアウト時はダッシュボードも非表示（load()でも制御するが念のため）
  if (!loggedIn) {
    const dashboard = document.getElementById('statsDashboard');
    if (dashboard) dashboard.style.display = 'none';
  }

  btn.onclick = () => {
    location.href = "/login";
  };
}


function wireUI() {

  const qEl = $("q");
if (qEl) {
  qEl.oninput = render;
  qEl.onkeydown = (e) => { if (e.key === "Enter") render(); };
}

const loginBtn = document.getElementById("googleLogin");
if (loginBtn) loginBtn.onclick = () => { location.href = "/login"; };

const logoutBtn = document.getElementById("logoutBtn");
if (logoutBtn) logoutBtn.onclick = async () => {
  try {
    await fetch("/logout", { credentials: "same-origin" });
  } finally {
    location.reload();
  }
};


  const closedToggle = document.getElementById("showClosedToggle");
  if (closedToggle) {
    closedToggle.checked = state.showClosed;
    closedToggle.onchange = () => {
      state.showClosed = closedToggle.checked;
      render();
    };
  }

  // エリア検索ボタン
  const areaSearchBtn = document.getElementById("areaSearchBtn");
  if (areaSearchBtn) areaSearchBtn.addEventListener("click", openAreaModal);

  // 動物から探すボタン → 動物モーダル
  const animalSearchBtn = document.getElementById("animalSearchBtn");
  if (animalSearchBtn) animalSearchBtn.addEventListener("click", openAnimalModal);

  // 絞り込みボタン
  const filterBtnEl = document.getElementById("filterBtn");
  if (filterBtnEl) filterBtnEl.addEventListener("click", openFilterModal);

  initSuggestions();

  // ===== ハンバーガードロワー =====
  const menuBtn = document.getElementById('menuBtn');
  const drawer = document.getElementById('drawer');
  const drawerOverlay = document.getElementById('drawerOverlay');
  const drawerClose = document.getElementById('drawerClose');

  function openDrawer() {
    if (!drawer) return;
    drawer.classList.add('is-open');
    if (drawerOverlay) drawerOverlay.classList.add('is-open');
    drawer.removeAttribute('aria-hidden');
  }
  function closeDrawer() {
    if (!drawer) return;
    drawer.classList.remove('is-open');
    if (drawerOverlay) drawerOverlay.classList.remove('is-open');
    drawer.setAttribute('aria-hidden', 'true');
  }

  if (menuBtn) menuBtn.addEventListener('click', openDrawer);
  if (drawerClose) drawerClose.addEventListener('click', closeDrawer);
  if (drawerOverlay) drawerOverlay.addEventListener('click', closeDrawer);

  // ボトムシートの閉じる処理
  const sheetCloseBtn = document.getElementById('sheetClose');
  const mapSheetOverlay = document.getElementById('mapSheetOverlay');
  if (sheetCloseBtn) sheetCloseBtn.addEventListener('click', closeMapSheet);
  if (mapSheetOverlay) mapSheetOverlay.addEventListener('click', closeMapSheet);

  // シェア
  const shareBtnEl = document.getElementById("shareBtn");
  if (shareBtnEl) shareBtnEl.addEventListener("click", handleShare);

  // シェアモーダルのクローズ
  const shareModal = document.getElementById("shareModal");
  const shareModalClose = document.getElementById("shareModalClose");
  if (shareModalClose) shareModalClose.addEventListener("click", () => { if (shareModal) shareModal.style.display = "none"; });
  if (shareModal) shareModal.addEventListener("click", (e) => { if (e.target === shareModal) shareModal.style.display = "none"; });
}

wireUI();

// ===== エリアモーダル =====
function openAreaModal() {
  const modal = document.getElementById('areaModal');
  const content = document.getElementById('areaModalContent');
  if (!modal || !content) return;

  // リージョンごとに都道府県ボタンを生成
  content.innerHTML = '';
  // PREF_ORDER から地域ごとのpref一覧を構築
  const regionPrefs = {};
  for (const region of REGION_ORDER) regionPrefs[region] = [];
  for (const pref of PREF_ORDER) {
    const region = REGION_BY_PREF[pref] || 'その他';
    if (!regionPrefs[region]) regionPrefs[region] = [];
    regionPrefs[region].push(pref);
  }

  for (const region of REGION_ORDER) {
    const prefs = regionPrefs[region];
    if (!prefs || !prefs.length) continue;
    const sec = document.createElement('div');
    sec.className = 'area-region';
    const title = document.createElement('p');
    title.className = 'area-region-title';
    title.textContent = region;
    sec.appendChild(title);
    const grid = document.createElement('div');
    grid.className = 'area-pref-grid';
    for (const pref of prefs) {
      const count = state.items.filter(x => x.prefecture === pref && (state.showClosed || !x.is_closed)).length;
      if (count === 0) continue;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'area-pref-btn' + (state.pref === pref ? ' active' : '');
      btn.innerHTML = pref + ' <span class="area-pref-count">(' + count + ')</span>';
      btn.onclick = () => {
        state.pref = pref;
        modal.style.display = 'none';
        updateFilterBadge();
        render();
      };
      grid.appendChild(btn);
    }
    sec.appendChild(grid);
    content.appendChild(sec);
  }

  modal.style.display = '';

  document.getElementById('areaModalClear').onclick = () => {
    state.pref = '';
    modal.style.display = 'none';
    updateFilterBadge();
    render();
  };
  document.getElementById('areaModalClose').onclick = () => { modal.style.display = 'none'; };
  modal.addEventListener('click', function handler(e) {
    if (e.target === modal) { modal.style.display = 'none'; modal.removeEventListener('click', handler); }
  });
}

// ===== 絞り込みモーダル =====
function openFilterModal() {
  const modal = document.getElementById('filterModal');
  if (!modal) return;

  // 現在のstateをモーダルに反映
  modal.querySelectorAll('input[name="visitFilter"]').forEach(r => {
    r.checked = r.value === (state.filter || 'all');
  });
  const memoCheck = document.getElementById('filterMemoCheck');
  const photoCheck = document.getElementById('filterPhotoCheck');
  if (memoCheck) memoCheck.checked = state.filterMemo;
  if (photoCheck) photoCheck.checked = state.filterPhoto;

  modal.style.display = '';

  document.getElementById('filterApplyBtn').onclick = () => {
    const checked = modal.querySelector('input[name="visitFilter"]:checked');
    state.filter = checked ? checked.value : 'all';
    state.filterMemo  = !!(memoCheck && memoCheck.checked);
    state.filterPhoto = !!(photoCheck && photoCheck.checked);
    modal.style.display = 'none';
    updateFilterBadge();
    render();
  };

  document.getElementById('filterResetBtn').onclick = () => {
    state.filter = 'all';
    state.filterMemo = false;
    state.filterPhoto = false;
    modal.style.display = 'none';
    updateFilterBadge();
    render();
  };

  document.getElementById('filterModalClose').onclick = () => { modal.style.display = 'none'; };
  modal.addEventListener('click', function handler(e) {
    if (e.target === modal) { modal.style.display = 'none'; modal.removeEventListener('click', handler); }
  });
}

// ===== 動物モーダル =====
function countWithPendingAnimals(pendingSet) {
  const q = ($('q') ? $('q').value.trim() : '').toLowerCase();
  return state.items.filter(item => {
    if (q && !match(item, q)) return false;
    if (!passesFilter(item)) return false;
    if (pendingSet.size > 0 && item.is_closed) return false;
    for (const a of pendingSet) { if (!item[a]) return false; }
    return true;
  }).length;
}

function openAnimalModal() {
  const modal = document.getElementById('animalModal');
  if (!modal) return;

  // 現在のselectedAnimalsをモーダルに反映
  modal.querySelectorAll('input[name="animalModalFilter"]').forEach(c => {
    c.checked = selectedAnimals.has(c.value);
  });

  const countEl = document.getElementById('animalCountNum');

  function updateCount() {
    const pending = new Set(
      [...modal.querySelectorAll('input[name="animalModalFilter"]:checked')].map(c => c.value)
    );
    if (countEl) {
      countEl.textContent = countWithPendingAnimals(pending);
      countEl.classList.remove('pop');
      void countEl.offsetWidth; // reflow
      countEl.classList.add('pop');
      countEl.addEventListener('animationend', () => countEl.classList.remove('pop'), { once: true });
    }
  }

  modal.querySelectorAll('input[name="animalModalFilter"]').forEach(c => {
    c.onchange = updateCount;
  });

  updateCount();
  modal.style.display = '';

  document.getElementById('animalApplyBtn').onclick = () => {
    selectedAnimals.clear();
    modal.querySelectorAll('input[name="animalModalFilter"]:checked').forEach(c => {
      selectedAnimals.add(c.value);
    });
    modal.style.display = 'none';
    updateFilterBadge();
    render();
  };

  document.getElementById('animalResetBtn').onclick = () => {
    selectedAnimals.clear();
    modal.querySelectorAll('input[name="animalModalFilter"]').forEach(c => { c.checked = false; });
    updateCount();
  };

  document.getElementById('animalModalClose').onclick = () => { modal.style.display = 'none'; };
  modal.addEventListener('click', function handler(e) {
    if (e.target === modal) { modal.style.display = 'none'; modal.removeEventListener('click', handler); }
  });
}

function updateFilterBadge() {
  // エリアボタン
  const areaBtn = document.getElementById('areaSearchBtn');
  if (areaBtn) {
    areaBtn.classList.toggle('has-filter', !!state.pref);
    areaBtn.textContent = state.pref ? ('📍 ' + state.pref) : '📍 エリアから探す';
  }
  // 動物ボタン
  const animalBtn = document.getElementById('animalSearchBtn');
  if (animalBtn) {
    animalBtn.classList.toggle('has-filter', selectedAnimals.size > 0);
    animalBtn.textContent = selectedAnimals.size > 0
      ? ('🐘 動物 (' + selectedAnimals.size + '件選択)')
      : '🐘 動物から探す';
  }
  // 絞り込みボタンラベル
  const filterLabelEl = document.getElementById('filterBtnLabel');
  const filterBtnEl = document.getElementById('filterBtn');
  const extraCount = (state.filterMemo ? 1 : 0) + (state.filterPhoto ? 1 : 0);
  const FILTER_LABELS = { all: 'すべて', visited: '訪問済', want_to_go: '行きたい', unvisited: '未訪問' };
  let label = FILTER_LABELS[state.filter] || 'すべて';
  if (extraCount > 0) label += ' +' + extraCount;
  if (filterLabelEl) filterLabelEl.textContent = label;
  const hasFilter = state.filter !== 'all' || state.filterMemo || state.filterPhoto;
  if (filterBtnEl) filterBtnEl.classList.toggle('has-filter', hasFilter);
}

function initSuggestions() {
  const qEl = $('q');
  const list = $('suggestList');
  if (!qEl || !list) return;

  qEl.addEventListener('input', () => {
    const q = qEl.value.trim();
    if (!q || !state.items.length) {
      list.style.display = 'none';
      return;
    }
    const lq = q.toLowerCase();
    const matches = state.items
      .filter(item => item.name.toLowerCase().includes(lq))
      .slice(0, 8);
    if (!matches.length) {
      list.style.display = 'none';
      return;
    }
    list.innerHTML = '';
    for (const item of matches) {
      const li = document.createElement('li');
      li.textContent = item.name;
      li.addEventListener('click', () => {
        qEl.value = item.name;
        list.style.display = 'none';
        render();
      });
      list.appendChild(li);
    }
    list.style.display = '';
  });

  // 外側クリックで候補を閉じる
  document.addEventListener('click', (e) => {
    if (!qEl.contains(e.target) && !list.contains(e.target)) {
      list.style.display = 'none';
    }
  });
}

(async () => {
  // まずCSRFトークン取得（ログイン前でもOK）
  await initCsrf();

  const me = await apiMe();
  state.loggedIn = !!(me && me.logged_in);
  setLoginStatus(me);

  load().catch((e) => alert("APIエラー: " + e.message));
})();


function initMap() {
  if (map) return;

  map = L.map("map", { zoomControl: true }).setView([36.2048, 138.2529], 5);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);

  markersLayer = L.layerGroup().addTo(map);
}


function refreshMarker(it) {
  const marker = state.markerById[it.id];
  if (!marker) return;
  let markerHtml;
  if (it.is_closed) {
    markerHtml = '<div class="marker closed"></div>';
  } else if (it.visited) {
    markerHtml = '<div class="marker visited"></div>';
  } else if (it.want_to_go) {
    markerHtml = '<div class="marker want-to-go"></div>';
  } else {
    markerHtml = '<div class="marker unvisited"></div>';
  }
  marker.setIcon(L.divIcon({ className: '', html: markerHtml, iconSize: [16, 16] }));
}

function updateMap(items, opts = { fit: true }) {
  if (!map) return;
  markersLayer.clearLayers();
  state.markerById = {};

  const pts = [];
  for (const it of items) {
    const lat = Number(it.lat ?? it.latitude);
    const lng = Number(it.lng ?? it.longitude);
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) continue;

    pts.push([lat, lng]);

    const label = it.name;
    let badge, markerHtml;
    if (it.is_closed) {
      badge = "🚫";
      markerHtml = '<div class="marker closed"></div>';
    } else if (it.visited) {
      badge = "✅";
      markerHtml = '<div class="marker visited"></div>';
    } else if (it.want_to_go) {
      badge = "⭐";
      markerHtml = '<div class="marker want-to-go"></div>';
    } else {
      badge = "⬜";
      markerHtml = '<div class="marker unvisited"></div>';
    }
    const icon = L.divIcon({
      className: "",
      html: markerHtml,
      iconSize: [16, 16],
    });

    const marker = L.marker([lat, lng], { icon }).addTo(markersLayer);

    state.markerById[it.id] = marker;

    marker.on('click', () => openMapSheet(it));
  }

  if (opts.fit && pts.length) {
    map.fitBounds(pts, { padding: [24, 24] });
  }
}

// ===== 地図ボトムシート =====
let _sheetItem = null;
let _sheetChanged = false;

function openMapSheet(it) {
  _sheetItem = it;
  const overlay = document.getElementById('mapSheetOverlay');
  const sheet   = document.getElementById('mapSheet');
  const nameEl  = document.getElementById('sheetName');
  const locEl   = document.getElementById('sheetLoc');
  if (!sheet || !overlay) return;

  if (it.url) {
    nameEl.innerHTML = `<a href="${it.url}" target="_blank" rel="noopener noreferrer" class="sheet-name-link">${it.name || ''}</a>`;
  } else {
    nameEl.textContent = it.name || '';
  }
  locEl.textContent  = [it.prefecture, it.city].filter(Boolean).join(' / ');

  renderSheetActions(it);

  overlay.style.display = '';
  sheet.style.display = '';
  // force reflow
  sheet.getBoundingClientRect();
  overlay.classList.add('is-open');
  sheet.classList.add('is-open');
}

function closeMapSheet() {
  const overlay = document.getElementById('mapSheetOverlay');
  const sheet   = document.getElementById('mapSheet');
  if (overlay) overlay.classList.remove('is-open');
  if (sheet)   sheet.classList.remove('is-open');
  setTimeout(() => {
    if (overlay) overlay.style.display = 'none';
    if (sheet)   sheet.style.display   = 'none';
    _sheetItem = null;
    if (_sheetChanged) {
      _sheetChanged = false;
      render();
    }
  }, 300);
}

function renderSheetActions(it) {
  const el = document.getElementById('sheetActions');
  if (!el) return;
  el.innerHTML = '';

  if (!state.loggedIn) {
    const msg = document.createElement('p');
    msg.className = 'sheet-login-msg';
    msg.textContent = 'ログインすると記録できます';
    el.appendChild(msg);

    const btn = document.createElement('button');
    btn.className = 'sheet-login-btn';
    btn.textContent = 'Googleでログイン';
    btn.onclick = () => { location.href = '/login'; };
    el.appendChild(btn);

    el.appendChild(makeSheetCardLink(it));
    return;
  }

  if (it.is_closed) {
    const msg = document.createElement('p');
    msg.className = 'sheet-closed-msg';
    msg.textContent = '🚫 この動物園は閉園しています';
    el.appendChild(msg);
    el.appendChild(makeSheetCardLink(it));
    return;
  }

  // 訪問済ボタン
  const visitBtn = document.createElement('button');
  visitBtn.type = 'button';
  visitBtn.className = 'sheet-visit-btn' + (it.visited ? ' visited' : '');
  visitBtn.textContent = it.visited ? '✅ 訪問済（解除）' : '訪問済にする';
  visitBtn.onclick = () => sheetToggleVisited(it, el);
  el.appendChild(visitBtn);

  // 行きたいボタン
  const wantBtn = document.createElement('button');
  wantBtn.type = 'button';
  wantBtn.className = 'sheet-want-btn' + (it.want_to_go ? ' active' : '');
  wantBtn.textContent = it.want_to_go ? '★ 行きたい（解除）' : '☆ 行きたい';
  wantBtn.onclick = () => sheetToggleWantToGo(it, el);
  el.appendChild(wantBtn);

  if (it.visited) {
    // 訪問回数行
    const countRow = document.createElement('div');
    countRow.className = 'sheet-count-row';
    const cLabel = document.createElement('span');
    cLabel.className = 'sheet-count-label';
    cLabel.textContent = '訪問回数';
    const minusBtn = document.createElement('button');
    minusBtn.className = 'sheet-count-btn'; minusBtn.textContent = '−';
    const numEl = document.createElement('span');
    numEl.className = 'sheet-count-num';
    numEl.textContent = String(it.visit_count || 0);
    const plusBtn = document.createElement('button');
    plusBtn.className = 'sheet-count-btn'; plusBtn.textContent = '+';

    async function saveSheetCount(n) {
      const newN = Math.max(0, n);
      const old = it.visit_count || 0;
      it.visit_count = newN;
      numEl.textContent = String(newN);
      _sheetChanged = true;
      try {
        await apiPut(`/api/zoos/${it.id}/visit_count`, { visit_count: newN });
      } catch(e) {
        it.visit_count = old;
        numEl.textContent = String(old);
        alert('APIエラー: ' + e.message);
      }
    }
    minusBtn.onclick = () => saveSheetCount((it.visit_count || 0) - 1);
    plusBtn.onclick  = () => saveSheetCount((it.visit_count || 0) + 1);
    countRow.append(cLabel, minusBtn, numEl, plusBtn);
    el.appendChild(countRow);

    // 訪問日行
    const dateRow = document.createElement('div');
    dateRow.className = 'sheet-date-row';
    const dLabel = document.createElement('label');
    dLabel.textContent = '訪問日：';
    const dateInput = document.createElement('input');
    dateInput.type = 'date';
    if (it.visited_at) dateInput.value = it.visited_at.slice(0, 10);
    dateInput.onchange = async () => {
      try {
        const res = await apiPut(`/api/zoos/${it.id}/visited_at`, { visited_at: dateInput.value || null });
        if (res) it.visited_at = res.visited_at;
        _sheetChanged = true;
      } catch(e) {
        alert('APIエラー: ' + e.message);
      }
    };
    dateRow.append(dLabel, dateInput);
    el.appendChild(dateRow);
  }

  // ===== 写真セクション =====
  const photosWrap = document.createElement('div');
  photosWrap.className = 'sheet-photos-wrap';

  const photoHeaderRow = document.createElement('div');
  photoHeaderRow.className = 'sheet-photos-header';

  const photoLabel = document.createElement('span');
  photoLabel.className = 'sheet-photos-label';
  photoLabel.textContent = '📷 写真';

  const addBtn = document.createElement('button');
  addBtn.type = 'button';
  addBtn.className = 'sheet-photo-add-btn';
  addBtn.textContent = '+ 追加';

  photoHeaderRow.append(photoLabel, addBtn);

  const thumbsDiv = document.createElement('div');
  thumbsDiv.className = 'sheet-thumbs';

  const fileInput = document.createElement('input');
  fileInput.type = 'file';
  fileInput.accept = 'image/*';
  fileInput.style.display = 'none';

  async function refreshSheetPhotos() {
    thumbsDiv.innerHTML = '<span style="font-size:12px;color:#aaa">読み込み中…</span>';
    try {
      const list = await apiGet(`/api/zoos/${it.id}/photos`);
      thumbsDiv.innerHTML = '';
      for (const p of list) {
        const item = document.createElement('div');
        item.className = 'sheet-thumb-item';
        const img = document.createElement('img');
        img.className = 'sheet-thumb';
        img.src = p.url;
        img.loading = 'lazy';
        item.appendChild(img);
        const del = document.createElement('button');
        del.type = 'button';
        del.className = 'sheet-thumb-del';
        del.textContent = '×';
        del.onclick = async (e) => {
          e.preventDefault();
          e.stopPropagation();
          if (!confirm('この写真を削除しますか？')) return;
          try {
            const res = await fetch(`/api/zoos/${it.id}/photos/${p.id}`, {
              method: 'DELETE',
              credentials: 'same-origin',
              headers: { 'X-CSRF-Token': state.csrfToken },
            });
            if (!res.ok) throw new Error(await res.text());
            it.has_photos = thumbsDiv.querySelectorAll('.sheet-thumb-item').length > 0;
            _sheetChanged = true;
            await refreshSheetPhotos();
          } catch (err) {
            alert('削除に失敗: ' + err.message);
          }
        };
        item.appendChild(del);
        thumbsDiv.appendChild(item);
      }
    } catch(e) {
      thumbsDiv.innerHTML = '';
      console.warn('sheet photos fetch failed:', e);
    }
  }

  addBtn.onclick = () => { fileInput.click(); };

  fileInput.onchange = async () => {
    const file = fileInput.files && fileInput.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append('file', file);
    addBtn.disabled = true;
    addBtn.textContent = '送信中…';
    try {
      const res = await fetch(`/api/zoos/${it.id}/photos`, {
        method: 'POST',
        body: fd,
        credentials: 'same-origin',
        headers: { 'X-CSRF-Token': state.csrfToken },
      });
      if (!res.ok) throw new Error(await res.text());
      fileInput.value = '';
      it.has_photos = true;
      _sheetChanged = true;
      await refreshSheetPhotos();
    } catch(e) {
      alert('写真アップロード失敗: ' + e.message);
    } finally {
      addBtn.disabled = false;
      addBtn.textContent = '+ 追加';
    }
  };

  photosWrap.appendChild(photoHeaderRow);
  photosWrap.appendChild(thumbsDiv);
  photosWrap.appendChild(fileInput);
  el.appendChild(photosWrap);
  refreshSheetPhotos();

  el.appendChild(makeSheetCardLink(it));
}

function makeSheetCardLink(it) {
  const a = document.createElement('a');
  a.className = 'sheet-card-link';
  a.href = '#card-' + it.id;
  a.textContent = 'リストのカードを見る →';
  a.onclick = (e) => {
    e.preventDefault();
    closeMapSheet();
    setTimeout(() => {
      const card = document.getElementById('card-' + it.id);
      if (card) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 320);
  };
  return a;
}

async function sheetToggleVisited(it, actionsEl) {
  const newVisited = !it.visited;

  if (!newVisited) {
    // 解除確認モーダル（既存の confirmModal を流用）
    const ok = await new Promise(resolve => {
      const modal = document.getElementById('confirmModal');
      const title = document.getElementById('confirmTitle');
      const body  = document.getElementById('confirmBody');
      const okBtn = document.getElementById('confirmOk');
      const cancelBtn = document.getElementById('confirmCancel');
      if (!modal) { resolve(true); return; }
      title.textContent = '訪問済を解除しますか？';
      body.textContent  = `「${it.name}」の訪問済と訪問回数・日付がリセットされます。`;
      modal.style.display = '';
      const cleanup = (result) => {
        modal.style.display = 'none';
        okBtn.onclick = null; cancelBtn.onclick = null;
        resolve(result);
      };
      okBtn.onclick    = () => cleanup(true);
      cancelBtn.onclick = () => cleanup(false);
    });
    if (!ok) return;
  }

  it.visited = newVisited;
  if (newVisited && (it.visit_count || 0) === 0) it.visit_count = 1;
  if (!newVisited) { it.visit_count = 0; it.visited_at = null; }
  _sheetChanged = true;
  refreshMarker(it);
  renderSheetActions(it);

  try {
    const res = await apiPut(`/api/zoos/${it.id}/visited`, { visited: newVisited });
    if (res && res.visited_at) it.visited_at = res.visited_at;
    if (res && res.visit_count !== undefined) it.visit_count = res.visit_count;
    updateBadgesFromState();
  } catch(e) {
    it.visited = !newVisited;
    if (!newVisited) { it.visit_count = 0; it.visited_at = null; }
    refreshMarker(it);
    renderSheetActions(it);
    alert('APIエラー: ' + e.message);
  }
}

async function sheetToggleWantToGo(it, actionsEl) {
  const newVal = !it.want_to_go;
  it.want_to_go = newVal;
  _sheetChanged = true;
  refreshMarker(it);
  renderSheetActions(it);
  try {
    await apiPut(`/api/zoos/${it.id}/want_to_go`, { want_to_go: newVal });
  } catch(e) {
    it.want_to_go = !newVal;
    refreshMarker(it);
    renderSheetActions(it);
    alert('APIエラー: ' + e.message);
  }
}

const sortSel = $("sort");
if (sortSel) {
  // 前回の並び替えをlocalStorageから復元
  const savedSort = localStorage.getItem("zoo_sort");
  if (savedSort && sortSel.querySelector(`option[value="${savedSort}"]`)) {
    state.sort = savedSort;
    sortSel.value = savedSort;
  }

  sortSel.onchange = () => {
    state.sort = sortSel.value;
    localStorage.setItem("zoo_sort", state.sort);

    render();
  };
}

// Service Worker 登録（PWA対応）
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(err => {
      console.warn('SW registration failed:', err);
    });
  });
}
