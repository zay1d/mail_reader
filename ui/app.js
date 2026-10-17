/* Почта ресепшн — экран сотрудника */
(function () {
  'use strict';
  var state = null, catFilter = '', picked = null;   // picked: выбранная пилюля — null | 'all' | ключ направления
  var $ = function (id) { return document.getElementById(id); };

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function pd(iso) { if (!iso) return null; var m = iso.match(/(\d+)-(\d+)-(\d+)T(\d+):(\d+):(\d+)/); return m ? new Date(+m[1], m[2] - 1, +m[3], +m[4], +m[5], +m[6]) : null; }
  function two(n) { return (n < 10 ? '0' : '') + n; }
  function ft(iso) { var d = pd(iso); return d ? two(d.getHours()) + ':' + two(d.getMinutes()) : ''; }
  function fdt(iso) { var d = pd(iso); if (!d) return ''; var t = new Date(); var same = d.toDateString() === t.toDateString(); return (same ? 'сегодня' : two(d.getDate()) + '.' + two(d.getMonth() + 1)) + ' ' + ft(iso); }
  function mins(iso) { var d = pd(iso); return d ? Math.max(0, Math.floor((Date.now() - d.getTime()) / 60000)) : 0; }
  function dur(m) { m = Math.max(0, Math.round(m)); return Math.floor(m / 60) + ':' + two(m % 60); }
  function plural(n, one, few, many) { var a = n % 10, b = n % 100; return n + ' ' + (a === 1 && b !== 11 ? one : a >= 2 && a <= 4 && (b < 12 || b > 14) ? few : many); }
  function fsize(n) { n = +n || 0; return n < 1048576 ? Math.round(n / 1024) + ' КБ' : String(Math.round(n / 104857.6) / 10).replace('.', ',') + ' МБ'; }
  function api(path, body) {
    return fetch(path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}).then(function (r) { return r.json(); });
  }
  function toast(msg) { var t = document.createElement('div'); t.className = 'toast'; t.textContent = msg; document.body.appendChild(t); setTimeout(function () { t.remove(); }, 3500); }
  function show(id) { $(id).hidden = false; }
  function hide(id) { $(id).hidden = true; }

  // ---------- часы ----------
  function tick() { var d = new Date(); $('clock').textContent = two(d.getHours()) + ':' + two(d.getMinutes()); }
  setInterval(tick, 1000); tick();

  // ---------- отрисовка ----------
  function render() {
    if (!state) return;
    var c = state.counts, cfg = state.cfg, pn = cfg.priority_names;
    var st = $('status'); st.className = 'status ' + (state.status === 'ok' ? 'ok' : state.status === 'demo' ? 'demo' : state.status === 'error' ? 'error' : '');
    $('status-text').textContent = state.status_text || '';
    if (state.version) $('status-text').title = 'Версия программы ' + state.version;
    $('demo-bar').hidden = state.mode !== 'demo';
    // смена в шапке — та, которую открыли сотрудники; по часам она не меняется
    $('shift').firstChild.textContent = state.session ? (state.closable ? state.closable.label : 'Смена открыта') : 'Смена не открыта';
    $('shift-sub').textContent = state.session && state.session.staff.length ? 'на смене: ' + state.session.staff.join(', ') : '';
    $('shift-sub').className = state.session ? 'staff-line' : '';
    renderSession();
    // KPI
    $('k-queue').textContent = c.queue; $('k-answered').textContent = c.answered; $('k-received').textContent = c.received;
    $('k-overdue').textContent = c.overdue ? 'из них просрочено: ' + c.overdue : (c.urgent ? 'из них срочных: ' + c.urgent : (c.queue ? 'все пока в сроке' : 'все письма обработаны'));
    $('kpi-queue').className = 'kpi ' + (c.overdue ? 'alert' : c.queue ? '' : 'good');
    $('k-auto').textContent = c.auto_replied ? 'автоответ отправлен: ' + c.auto_replied : 'без рассылок и автоуведомлений';
    $('c-queue').textContent = c.queue; $('c-queue').className = 'count' + (c.queue ? '' : ' zero');
    document.title = (c.queue ? '(' + c.queue + ') ' : '') + 'Почта ресепшн';
    // направления: красная — есть без ответа, зелёная — всё отвечено, серая — писем не было
    var cats = $('cats');
    var csig = state.categories.map(function (x) { return x.key + ':' + x.received + ':' + (x.carried || 0) + ':' + x.answered + ':' + (x.forwarded || 0) + ':' + x.open; }).join(';') + '|' + picked + '|' + c.received + ':' + (c.carried || 0) + ':' + c.queue + ':' + (c.forwarded || 0);
    if (cats.dataset.sig !== csig) {
      cats.dataset.sig = csig;
      var pill = function (key, name, rec, ans, open) {
        var cls = 'pill' + (picked === (key || 'all') ? ' on' : '');
        var circ = open ? '<span class="circ red">' + open + '</span>' : (rec ? '<span class="circ green">' + ans + '</span>' : '<span class="circ none"></span>');
        return '<button class="' + cls + '" data-cat="' + esc(key) + '"><span class="nm">' + esc(name) + '</span><span class="n">' + rec + '</span>' + circ + '</button>';
      };
      // зелёный кружок = обработано (отвечено + переслано в отдел)
      // число на пилюле = пришло за смену + перешло с прошлых смен без ответа
      cats.innerHTML = pill('', 'Все', c.received + (c.carried || 0), c.answered + (c.forwarded || 0), c.queue) + state.categories.map(function (x) { return pill(x.key, x.name, x.received + (x.carried || 0), x.answered + (x.forwarded || 0), x.open); }).join('');
    }
    renderPick();
    // очередь
    var q = $('queue');
    var items = state.queue.filter(function (m) { return !catFilter || (m.category || 'general') === catFilter; });
    $('empty').hidden = items.length > 0;
    $('empty-t').textContent = catFilter && c.queue ? 'В этом направлении всё отвечено' : '✓ Все письма отвечены';
    var sig = items.map(function (m) { return m.id + '|' + (m.carried ? 1 : 0) + '|' + (m.auto_replied_at ? 1 : 0) + '|' + m.priority + '|' + m.thread_count + '|' + (m.threat_level || 0) + (m.threat_ack_at ? 'a' : '') + (m.junk ? 'j' : '') + '|' + (m.self_reply_at || '') + '|' + (m.client_email || '') + (m.reply_self ? 's' : '') + '|' + (m.ai_summary || ''); }).join(';');
    if (q.dataset.sig !== sig) {
      q.dataset.sig = sig; q.innerHTML = '';
      items.forEach(function (m) {
        var el = document.createElement('div'); el.className = 'card'; el.dataset.id = m.id; el.dataset.recv = m.sla_from || m.received_at; el.dataset.sla = m.sla;
        var att = (m.attachments || []).map(function (a) { return '<span class="att" title="' + esc(fsize(a.size)) + '">📎 ' + esc(a.name) + '</span>'; }).join('');
        el.innerHTML =
          '<div class="timer"><div class="t-lbl"></div><div class="t-num"></div><div class="t-unit"></div><div class="t-sla"></div></div>' +
          '<div class="body"><div class="from">' +
          (m.priority === 1 ? '<span class="pri p1">' + esc(pn['1']) + '</span>' : m.priority === 3 ? '<span class="pri p3">' + esc(pn['3']) + '</span>' : '') +
          esc(m.sender_name || m.sender) + (m.sender && m.sender_name && m.sender.toLowerCase() !== m.sender_name.toLowerCase() ? '<span class="addr">' + esc(m.sender) + '</span>' : '') +
          '<span class="tag cat">' + esc(m.category_name) + '</span>' +
          (m.thread_count > 1 ? '<span class="tag thr">переписка: ' + m.thread_count + ' письма</span>' : '') +
          (m.junk ? '<span class="tag orange">из «Нежелательной почты» — проверьте: настоящее → в Outlook «Не нежелательное», спам → удалить</span>' : '') + (m.night ? '<span class="tag">ночное · срок с ' + ft(m.sla_from) + '</span>' : '') + (m.carried ? '<span class="tag">с прошлой смены</span>' : '') + (m.threat_words && m.threat_words.length ? ((m.threat_level || 2) === 2 ? '<span class="tag red">угроза</span>' : '<span class="tag orange">' + (m.threat_ack_at ? 'проверено, угрозы нет' : 'проверить') + '</span>') : '') + '</div>' +
          '<div class="subj">' + esc(m.subject || '(без темы)') + '</div>' +
          (m.ai_summary ? '<div class="ai-sum" title="Кратко — определено ИИ">' + esc(m.ai_summary) + '</div>' : '') +
          '<div class="prev">' + esc(m.body_preview || '') + '</div>' +
          (att ? '<div class="atts">' + att + '</div>' : '') + clientNote(m) +
          '<div class="meta">Получено ' + fdt(m.received_at) + (m.auto_replied_at ? ' · <span class="auto">автоответ отправлен ' + ft(m.auto_replied_at) + '</span>' : '') + '</div></div>' +
          '<div class="acts"><button class="btn-primary" data-act="open">Ответить в Outlook</button></div>';
        q.appendChild(el);
      });
    }
    updateTimers();
    // отвечено (выбрана пилюля направления — только его письма)
    var byCat = function (m) { return !catFilter || (m.category || 'general') === catFilter; };
    var ans = state.answered.filter(byCat);
    $('c-answered').textContent = ans.length;
    var sa = catFilter + '|' + ans.map(function (m) { return m.id + ':' + m.thread_count; }).join(';');
    if ($('answered').dataset.sig !== sa) { $('answered').dataset.sig = sa; $('answered').innerHTML = ans.length ? ans.map(function (m) {
      var t = pd(m.received_at) && pd(m.answered_at) ? Math.round((pd(m.answered_at) - pd(m.received_at)) / 60000) : null;
      return '<div class="row"><span class="t">' + ft(m.received_at) + '</span><span>' + esc(m.sender_name || m.sender) + '</span><span>' + esc(m.subject) + ' <span class="tag cat">' + esc(m.category_name) + '</span>' + ((m.attachments || []).length ? ' 📎' + m.attachments.length : '') + (m.thread_count > 1 ? ' <span class="tag thr">' + m.thread_count + ' письма</span>' : '') + '</span><span class="ok">✓ ' + ft(m.answered_at) + (t != null ? ' · ' + dur(t) : '') + '</span></div>';
    }).join('') : '<div class="row" style="grid-template-columns:1fr;color:var(--mut)">Пока нет</div>'; }
    // переслано в отделы
    var fw = (state.forwarded || []).filter(byCat);
    $('c-forwarded').textContent = fw.length;
    var sf = catFilter + '|' + fw.map(function (m) { return m.id; }).join(';');
    if ($('forwarded').dataset.sig !== sf) { $('forwarded').dataset.sig = sf; $('forwarded').innerHTML = fw.length ? fw.map(function (m) {
      return '<div class="row"><span class="t">' + ft(m.received_at) + '</span><span>' + esc(m.sender_name || m.sender) + '</span><span>' + esc(m.subject) + ' <span class="tag cat">' + esc(m.category_name) + '</span></span><span class="rs">→ ' + esc(m.forwarded_to) + ' · ' + ft(m.forwarded_at) + '</span></div>';
    }).join('') : '<div class="row" style="grid-template-columns:1fr;color:var(--mut)">Пока нет</div>'; }
    // сняты без ответа: угроза передана в СБ, письмо удалено в Outlook — тоже на виду
    var cd = (state.closed || []).filter(byCat);
    $('c-closed').textContent = cd.length;
    var sc = catFilter + '|' + cd.map(function (m) { return m.id; }).join(';');
    if ($('closed-list').dataset.sig !== sc) { $('closed-list').dataset.sig = sc; $('closed-list').innerHTML = cd.length ? cd.map(function (m) {
      return '<div class="row"><span class="t">' + fdt(m.received_at) + '</span><span>' + esc(m.sender_name || m.sender) + '</span><span>' + esc(m.subject || '(без темы)') +
        ' <span class="tag cat">' + esc(m.category_name) + '</span></span><span class="rs">' + esc(m.no_reply_reason || '') + ' · ' + ft(m.no_reply_at) + '</span></div>';
    }).join('') : '<div class="row" style="grid-template-columns:1fr;color:var(--mut)">Пока нет</div>'; }
    // не попали в очередь: рассылки и автописьма (видно, что программа письмо получила и почему не показала)
    var hd = state.hidden || [];
    $('c-hidden').textContent = hd.length;
    var sh = hd.map(function (m) { return m.id; }).join(';');
    if ($('hidden-list').dataset.sig !== sh) { $('hidden-list').dataset.sig = sh; $('hidden-list').innerHTML = hd.length ? hd.map(function (m) {
      return '<div class="row hrow"><span class="t">' + fdt(m.received_at) + '</span><span class="who">' + esc(m.sender_name || m.sender) +
        (m.sender && m.sender_name && m.sender.toLowerCase() !== m.sender_name.toLowerCase() ? '<small>' + esc(m.sender) + '</small>' : '') + '</span><span>' + esc(m.subject || '(без темы)') +
        '<small class="why">почему: ' + esc(m.ignored || '') + '</small></span>' +
        (m.can_open ? '<button data-view="' + esc(m.id) + '">Открыть в Outlook</button>' : '<span></span>') +
        '<button class="btn-need" data-unignore="' + esc(m.id) + '">Нужен ответ</button></div>';
    }).join('') : '<div class="row" style="grid-template-columns:1fr;color:var(--mut)">За смену таких писем не было</div>'; }
    // кнопка смены
    $('btn-close').disabled = !state.closable;
    renderThreat();
  }

  // заявка с сайта / ответ, ушедший нам же: куда на самом деле отвечать
  function clientNote(m) {
    var h = '';
    if (m.self_reply_at) {
      h += '<div class="note bad">⚠ Ответ ' + fdt(m.self_reply_at) + ' ушёл на <b>' + esc(m.self_reply_to || 'наш ящик') + '</b>, а не клиенту. Клиент его <b>не получил</b>. ' +
        (m.client_email ? 'Нажмите «Ответить в Outlook» — откроется ответ на адрес клиента.' : 'Напишите клиенту на адрес из текста письма.') + '</div>';
    }
    if (m.client_email) {
      h += '<div class="note client">' + (m.reply_self ? 'Заявка с сайта · ' : '') + 'ответ клиенту: <b>' + esc(m.client_email) + '</b>' +
        (m.client_name ? ' (' + esc(m.client_name) + ')' : '') + (m.reply_self ? ' — кнопка «Ответить в Outlook» сама подставит этот адрес' : '') + '</div>';
    } else if (m.reply_self) {
      h += '<div class="note warn">Заявка с сайта: «Ответить» в Outlook уйдёт на наш же ящик, а адрес клиента в тексте не найден. Посмотрите письмо: e-mail или телефон клиента — в тексте заявки.</div>';
    }
    return h;
  }

  // письмо в панели под пилюлями: что с ним стало
  function letterStatus(m) {
    if (m.answered_at) return '<span class="st ok">✓ отвечено ' + fdt(m.answered_at) + '</span>';
    if (m.forwarded_at) return '<span class="st fw" title="' + esc(m.forwarded_to || '') + '">→ переслано ' + esc(m.forwarded_to || '') + ' · ' + ft(m.forwarded_at) + '</span>';
    if (m.no_reply_reason) return '<span class="st mut">' + esc(m.no_reply_reason) + '</span>';
    return '<span class="st open" title="' + esc(m.self_reply_at ? 'ответ ушёл на ' + (m.self_reply_to || '') + ', а не клиенту' : '') + '">ждёт ответа' + (m.self_reply_at ? ' · ответ не дошёл' : '') + '</span>';
  }

  function renderPick() {
    var box = $('pick');
    if (!picked) { box.hidden = true; return; }
    var name = 'Все направления';
    if (picked !== 'all') state.categories.forEach(function (x) { if (x.key === picked) name = x.name; });
    var list = (state.letters || []).filter(function (m) { return picked === 'all' || (m.category || 'general') === picked; });
    var open = list.filter(function (m) { return !m.answered_at && !m.forwarded_at && !m.no_reply_reason; }).length;
    var done = list.filter(function (m) { return m.answered_at; }).length;
    var fw = list.filter(function (m) { return !m.answered_at && m.forwarded_at; }).length;
    var sig = picked + '|' + list.map(function (m) { return m.id + (m.answered_at || '') + (m.forwarded_at || '') + (m.no_reply_reason || '') + (m.self_reply_at || ''); }).join(';');
    box.hidden = false;
    if (box.dataset.sig === sig) return;
    box.dataset.sig = sig;
    $('pick-t').textContent = name + ': ' + plural(list.length, 'письмо', 'письма', 'писем') + ' за смену';
    $('pick-s').textContent = list.length ? 'ждут ответа ' + open + ' · отвечено ' + done + ' · переслано в отделы ' + fw : '';
    $('pick-list').innerHTML = list.length ? list.map(function (m) {
      return '<div class="row prow"><span class="t">' + fdt(m.received_at) + '</span><span class="who">' + esc(m.sender_name || m.sender) +
        (m.client_email ? '<small>клиент: ' + esc(m.client_email) + '</small>' : '') + '</span><span>' + esc(m.subject || '(без темы)') +
        ' <span class="tag cat">' + esc(m.category_name) + '</span>' + (m.thread_count > 1 ? ' <span class="tag thr">' + m.thread_count + ' письма</span>' : '') +
        '</span>' + letterStatus(m) + (m.can_open ? '<button data-view="' + esc(m.id) + '">Открыть в Outlook</button>' : '<span></span>') + '</div>';
    }).join('') : '<div class="row" style="grid-template-columns:1fr;color:var(--mut)">За эту смену писем этого направления не было</div>';
  }

  // Цвет по остатку срока: > половины срока зелёный, 25–50 % жёлтый, < 25 % оранжевый, просрочено красный.
  // ---------- открытие смены ----------
  var opStep = 1;
  function renderSession() {
    if (!state) return;
    if (state.session) { hide('m-open'); opStep = 1; return; }
    var cl = state.closable;
    $('op-shift').textContent = state.next_shift || '';
    $('op-step1').hidden = opStep !== 1; $('op-step2').hidden = opStep !== 2;
    if (opStep === 2 && !$('op-staff').dataset.built) {
      $('op-staff').dataset.built = '1';
      if (state.staff.length) {
        $('op-staff').innerHTML = state.staff.map(function (n, i) { return '<label><input type="checkbox" value="' + esc(n) + '"> ' + esc(n) + '</label>'; }).join('');
        hide('op-free');
      } else { $('op-staff').innerHTML = ''; show('op-free'); }
    }
    show('m-open');
  }
  function chosenStaff() {
    if (state.staff.length) return Array.prototype.map.call(document.querySelectorAll('#op-staff input:checked'), function (i) { return i.value; });
    return $('op-names').value.split(',').map(function (s) { return s.trim(); }).filter(Boolean);
  }
  $('op-start').addEventListener('click', function () { opStep = 2; $('op-staff').dataset.built = ''; renderSession(); $('op-confirm').disabled = true; });
  $('op-staff').addEventListener('change', function () {
    Array.prototype.forEach.call(document.querySelectorAll('#op-staff label'), function (l) { l.className = l.querySelector('input').checked ? 'on' : ''; });
    $('op-confirm').disabled = chosenStaff().length === 0;
  });
  $('op-names').addEventListener('input', function () { $('op-confirm').disabled = chosenStaff().length === 0; });
  $('op-confirm').addEventListener('click', function () {
    var st = chosenStaff(); if (!st.length) return;
    $('op-confirm').disabled = true;
    api('/api/open-shift', { staff: st }).then(function (r) { if (r.error) { toast(r.error); $('op-confirm').disabled = false; } else { opStep = 1; poll(); } });
  });

  function updateTimers() {
    if (!state) return;
    Array.prototype.forEach.call(document.querySelectorAll('#queue .card'), function (el) {
      var mn = mins(el.dataset.recv), sla = parseInt(el.dataset.sla, 10) || 60, left = sla - mn;
      var frac = left / sla;
      var cls = left <= 0 ? 'over' : frac < 0.25 ? 'soon' : frac < 0.5 ? 'warn' : '';
      if (el.className !== 'card ' + cls) el.className = 'card ' + cls;
      var lbl = left > 0 ? 'осталось' : 'просрочено на';
      var tnum = dur(Math.abs(left));
      var tunit = 'ч:мм';
      var sl = 'ждёт ' + dur(mn) + ' · срок ' + dur(sla);
      var L = el.querySelector('.t-lbl'), n = el.querySelector('.t-num'), u = el.querySelector('.t-unit'), s = el.querySelector('.t-sla');
      if (L.textContent !== lbl) L.textContent = lbl;
      if (n.textContent !== tnum) n.textContent = tnum;
      if (u.textContent !== tunit) u.textContent = tunit;
      if (s.textContent !== sl) s.textContent = sl;
    });
  }
  setInterval(updateTimers, 15000);

  function renderThreat() {
    var th = state.threats;
    if (!th.length) { hide('threat'); hide('th-confirm'); return; }
    var m = th[0];
    $('th-mail').innerHTML = '<div><b>От:</b> ' + esc(m.sender_name || '') + ' &lt;' + esc(m.sender || '') + '&gt; · <b>Получено:</b> ' + fdt(m.received_at) + '</div>' +
      '<div><b>Тема:</b> ' + esc(m.subject) + '</div><div style="margin-top:6px"><b>Найдено:</b> ' + (m.threat_words || []).map(function (w) { return '<span class="w">' + esc(w) + '</span>'; }).join('') + '</div>' +
      '<div class="prev">' + esc(m.body_preview || '') + '</div>' + (th.length > 1 ? '<div style="margin-top:8px;color:var(--red)"><b>Ещё сигналов: ' + (th.length - 1) + '</b></div>' : '');
    $('th-phone').textContent = state.cfg.security_phone || 'по внутреннему телефону';
    var red = (m.threat_level || 2) === 2;
    $('threat').className = 'threat' + (red ? '' : ' yellow');
    $('th-title').textContent = red ? '⚠ ВНИМАНИЕ! УГРОЗА' : '⚠ ВОЗМОЖНАЯ УГРОЗА';
    $('th-sub').textContent = red ? 'В письме найдены слова, указывающие на угрозу посетителям и торговому центру' : 'Проверьте письмо: найдены слова, похожие на угрозу или запугивание';
    $('th-steps-red').hidden = !red; $('th-btns-red').hidden = !red || !$('th-confirm').hidden;   // подтверждение стоит на месте кнопок
    $('th-steps-yellow').hidden = red; $('th-btns-yellow').hidden = red;
    if (red !== ($('threat').dataset.level === '2')) hide('th-confirm');
    $('threat').dataset.level = red ? '2' : '1';
    $('threat').dataset.id = m.id;
    document.title = (red ? '⚠ УГРОЗА' : '⚠ ПРОВЕРЬТЕ ПИСЬМО') + ' — Почта ресепшн';
    show('threat');
  }

  // ---------- опрос ----------
  function poll() {
    api('/api/state').then(function (s) { state = s; render(); }).catch(function () {
      $('status').className = 'status error'; $('status-text').textContent = 'Программа не отвечает. Запустите «Почта ресепшн» заново.';
    });
  }
  poll(); setInterval(poll, 2000);   // отвеченное письмо должно уходить с экрана сразу

  // ---------- действия ----------
  document.addEventListener('click', function (e) {
    var b = e.target.closest('button'); if (!b) return;
    if (b.dataset.close != null) { b.closest('.modal').hidden = true; return; }
    if (b.dataset.demo) { api('/api/demo', { action: b.dataset.demo }).then(function () { setTimeout(poll, 400); }); return; }
    if (b.dataset.cat != null) {
      // пилюля: ниже показываются все письма направления за смену; повторное нажатие — скрыть
      var k = b.dataset.cat || 'all';
      picked = picked === k ? null : k;
      catFilter = picked && picked !== 'all' ? picked : '';
      render();
      return;
    }
    if (b.id === 'pick-x') { picked = null; catFilter = ''; render(); return; }
    if (b.dataset.view) {
      if (state.mode === 'demo') { toast('Демо-режим: письмо открылось бы в Outlook.'); return; }
      api('/api/open', { id: b.dataset.view, view: true }).then(function (r) { if (!r.ok) toast('Не удалось открыть письмо в Outlook. Откройте его вручную.'); });
      return;
    }
    if (b.dataset.unignore) {
      if (!confirm('Поставить это письмо в очередь на ответ?')) return;
      b.disabled = true;
      api('/api/unignore', { id: b.dataset.unignore }).then(function (r) { toast(r.ok ? 'Письмо поставлено в очередь' : 'Не получилось — обновите страницу'); poll(); });
      return;
    }
    var card = b.closest('.card');
    if (card && b.dataset.act === 'open') {
      if (state.mode === 'demo') { toast('Демо-режим: письмо открылось бы в Outlook. Нажмите «Ответить на первое» в жёлтой панели.'); return; }
      api('/api/open', { id: card.dataset.id }).then(function (r) { if (!r.ok) toast('Не удалось открыть письмо в Outlook. Откройте его вручную.'); });
      return;
    }
  });

  // закрыть смену
  $('btn-close').addEventListener('click', function () {
    if (!state || !state.closable) return;
    var c = state.counts, cl = state.closable;
    $('cl-title').textContent = 'Закрыть смену: ' + cl.name + '?';
    $('cl-text').textContent = 'Отчёт за смену (открыта в ' + cl.opened + ') будет ' + (state.cfg.manager_email ? 'отправлен руководителю на ' + state.cfg.manager_email : 'сохранён в папку «Отчёты»') + '. Пришло: ' + c.received + ', отвечено: ' + c.answered + '.';
    if (c.queue) { $('cl-warn').innerHTML = '<b>Без ответа осталось: ' + c.queue + '.</b> Они попадут в отчёт как неотвеченные и перейдут следующей смене. Лучше ответить сейчас.'; show('cl-warn'); } else hide('cl-warn');
    if (state.session && state.session.staff.length) {
      $('cl-staff').innerHTML = '<b>На смене:</b> ' + esc(state.session.staff.join(', ')) + '<br><span style="color:var(--mut);font-size:13px">После закрытия появится окно открытия следующей смены.</span>';
      show('cl-staff'); hide('cl-name-wrap');
    } else {
      hide('cl-staff'); show('cl-name-wrap');
      try { $('cl-name').value = localStorage.getItem('rm_name') || ''; } catch (e) {}
    }
    show('m-close');
  });
  $('cl-ok').addEventListener('click', function () {
    var name = $('cl-name').value.trim();
    try { localStorage.setItem('rm_name', name); } catch (e) {}
    $('cl-ok').disabled = true;
    api('/api/close-shift', { by: name }).then(function (r) {
      $('cl-ok').disabled = false; hide('m-close');
      if (r.error && !r.file) { $('info-title').textContent = 'Не удалось'; $('info-body').innerHTML = '<div class="warn">' + esc(r.error) + '</div>'; }
      else {
        var s = r.stats;
        $('info-title').textContent = 'Смена закрыта';
        $('info-body').innerHTML = (r.sent_to ? '<div class="info">Отчёт отправлен на ' + esc(r.sent_to) + '</div>' : '<div class="' + (r.error ? 'warn' : 'info') + '">' + esc(r.error || 'Отчёт сохранён в папку «Отчёты»') + '</div>') +
          '<p>Пришло: <b>' + s.received + '</b> · Отвечено: <b>' + s.answered + '</b> · Без ответа: <b style="color:' + (s.unanswered ? 'var(--red)' : 'var(--green)') + '">' + s.unanswered + '</b>' + (s.rate != null ? ' · Доля ответов: <b>' + String(s.rate).replace('.', ',') + '%</b>' : '') + '</p>' +
          '<p><a href="reports/' + esc(r.file) + '" target="_blank">Открыть отчёт</a></p>';
      }
      show('m-info'); poll();
    }).catch(function () { $('cl-ok').disabled = false; toast('Ошибка связи с программой'); });
  });

  // угроза
  function openThreatMail() {
    if (state && state.mode === 'demo') { toast('Демо-режим: в Outlook открылось бы именно это письмо.'); return; }
    // view: само письмо, а не окно ответа автору угрозы (угроза могла прийти через форму сайта)
    api('/api/open', { id: $('threat').dataset.id, view: true }).then(function (r) { if (!r.ok) toast('Не удалось открыть письмо в Outlook. Откройте его вручную.'); });
  }
  $('th-open').addEventListener('click', openThreatMail);
  $('th-open2').addEventListener('click', openThreatMail);
  $('th-esc').addEventListener('click', function () {
    if (!confirm('Поднять тревогу? Включится сирена, сигнал уйдёт в службу безопасности.')) return;
    api('/api/threat-escalate', { id: $('threat').dataset.id }).then(poll);
  });
  $('th-clear').addEventListener('click', function () { api('/api/threat-clear', { id: $('threat').dataset.id }).then(poll); });
  // id угрозы запоминаем при нажатии «Я сообщил»: обновление экрана раз в 2 с может показать другую угрозу
  var ackId = null;
  $('th-ack').addEventListener('click', function () { ackId = $('threat').dataset.id; hide('th-btns-red'); show('th-confirm'); });
  $('th-no').addEventListener('click', function () { ackId = null; hide('th-confirm'); show('th-btns-red'); });
  $('th-yes').addEventListener('click', function () {
    if (!ackId || ackId !== $('threat').dataset.id) { ackId = null; hide('th-confirm'); show('th-btns-red'); return; }
    var id = ackId; ackId = null;
    api('/api/threat-ack', { id: id }).then(function () { hide('th-confirm'); poll(); });
  });
})();
