(function () {
  "use strict";

  var STATUS_META = {
    idle: { label: "Aguardando", color: "var(--text-faint)", pulsing: false },
    scanning: { label: "Processando", color: "var(--accent)", pulsing: true },
    clustering: { label: "Processando", color: "var(--accent)", pulsing: true },
    done: { label: "Concluído", color: "var(--ok)", pulsing: false },
    done_review: { label: "Revisão pendente", color: "var(--warn)", pulsing: false },
    error: { label: "Erro", color: "var(--warn)", pulsing: false },
  };

  var state = {
    events: [],
    activeId: localStorage.getItem("revelacao_active_event") || null,
    detail: null,
    pollTimer: null,
    currentPanelPerson: null,
  };

  function detailToMessage(detail, fallback) {
    if (!detail) return fallback;
    if (typeof detail === "string") return detail;
    // FastAPI validation errors are an array of {msg, loc, ...} objects.
    if (Array.isArray(detail)) {
      return detail.map(function (d) { return d && d.msg ? d.msg : JSON.stringify(d); }).join("; ");
    }
    return fallback;
  }

  function api(path, opts) {
    opts = opts || {};
    opts.headers = { "Content-Type": "application/json" };
    if (opts.body) opts.body = JSON.stringify(opts.body);
    return fetch(path, opts).then(function (res) {
      // Read as text first -- an unexpected non-JSON response (a proxy error
      // page, a plain-text 500, etc.) must not blow up in res.json() itself,
      // which in Safari throws an opaque "did not match expected pattern".
      return res.text().then(function (raw) {
        var data = null;
        if (raw) {
          try { data = JSON.parse(raw); } catch (parseErr) { data = null; }
        }
        if (!res.ok) {
          var msg = data ? detailToMessage(data.detail, res.statusText) : (raw || res.statusText);
          var err = new Error(msg || "Erro desconhecido (" + res.status + ")");
          err.status = res.status;
          throw err;
        }
        return data;
      });
    });
  }

  function fmt(n) { return (n || 0).toLocaleString("pt-BR"); }

  function statusKey(ev) {
    if (ev.status === "done" && ev.review_count > 0) return "done_review";
    return ev.status;
  }

  function showToast(msg) {
    var toast = document.getElementById("toast");
    toast.textContent = msg;
    toast.classList.add("show");
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { toast.classList.remove("show"); }, 3200);
  }

  function renderEmptyState(title, sub, showAddButton) {
    document.getElementById("empty-state").style.display = "";
    document.getElementById("event-content").style.display = "none";
    document.querySelector("#empty-state .empty-title").textContent = title;
    document.querySelector("#empty-state .empty-sub").textContent = sub;
    document.getElementById("btn-empty-add").style.display = showAddButton ? "" : "none";
  }

  // Only relevant when this page is served by the hub (not local standalone
  // mode) -- a 503 here specifically means "no local agent is connected for
  // this account", which needs a different message than "no events yet".
  function handleLoadError(err) {
    if (err && err.status === 503) {
      renderEmptyState(
        "Seu computador não está conectado",
        "Abra o revelação na máquina onde estão as fotos pra ver seus eventos aqui.",
        false
      );
      return;
    }
    showToast((err && err.message) || "Erro ao carregar.");
  }

  // ---- events list / switcher ----

  function loadEventsList() {
    return api("/api/events").then(function (events) {
      state.events = events;
      if (!state.activeId || !events.some(function (e) { return e.id === state.activeId; })) {
        state.activeId = events.length ? events[0].id : null;
      }
      renderEventMenu();
      return events;
    });
  }

  function renderEventMenu() {
    var html = "";
    state.events.forEach(function (ev) {
      var meta = STATUS_META[statusKey(ev)];
      html +=
        '<button class="event-row' + (ev.id === state.activeId ? " active" : "") + '" data-id="' + ev.id + '" type="button">' +
          '<span class="ev-dot' + (meta.pulsing ? " pulsing" : "") + '" style="background:' + meta.color + '"></span>' +
          '<span class="ev-row-text">' +
            '<span class="ev-row-name">' + escapeHtml(ev.label) + "</span>" +
            '<span class="ev-row-path">' + escapeHtml(ev.path.split(/[\\/]/).pop()) + "</span>" +
          "</span>" +
          '<span class="ev-row-pill" style="color:' + meta.color + '">' + meta.label + "</span>" +
        "</button>";
    });
    html +=
      '<button class="event-new" id="event-new-btn" type="button">' +
        '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>' +
        "Adicionar evento (selecionar pasta)" +
      "</button>";
    document.getElementById("event-menu").innerHTML = html;
  }

  function switchEvent(id) {
    state.activeId = id;
    localStorage.setItem("revelacao_active_event", id);
    document.getElementById("event-menu").classList.remove("open");
    document.getElementById("event-switch").setAttribute("aria-expanded", "false");
    refreshActive().catch(handleLoadError);
  }

  // ---- active event rendering ----

  function refreshActive() {
    if (!state.activeId) {
      renderEmptyState(
        "Nenhum evento ainda",
        "Adicione a pasta de fotos de um evento (casamento, formatura, espetáculo...) para começar.",
        true
      );
      document.getElementById("event-switch-name").textContent = "Nenhum evento ainda";
      document.getElementById("event-switch-dot").style.background = "var(--text-faint)";
      document.getElementById("topbar-path").textContent = "";
      stopPolling();
      return Promise.resolve();
    }
    return api("/api/events/" + state.activeId).then(function (detail) {
      state.detail = detail;
      renderActive(detail);
      if (detail.status === "scanning" || detail.status === "clustering") {
        startPolling();
      } else {
        stopPolling();
      }
    });
  }

  function renderActive(ev) {
    document.getElementById("empty-state").style.display = "none";
    document.getElementById("event-content").style.display = "";

    var meta = STATUS_META[statusKey(ev)];
    document.getElementById("event-switch-name").textContent = ev.label;
    var dot = document.getElementById("event-switch-dot");
    dot.style.background = meta.color;
    dot.className = "ev-dot" + (meta.pulsing ? " pulsing" : "");
    document.getElementById("topbar-path").textContent = ev.path;

    var pessoasTotal = ev.people.length + ev.review.length;
    document.getElementById("stat-fotos").textContent = fmt(ev.stats.fotos);
    document.getElementById("stat-rostos").textContent = fmt(ev.stats.rostos);
    document.getElementById("stat-pessoas").textContent = fmt(pessoasTotal);

    var busy = ev.status === "scanning" || ev.status === "clustering";
    document.getElementById("proc-title").textContent =
      ev.status === "scanning" ? "Analisando fotos" :
      ev.status === "clustering" ? "Agrupando rostos" : "Status do evento";
    document.getElementById("proc-count").textContent = fmt(ev.current) + " / " + fmt(ev.total);
    document.getElementById("proc-bar-fill").style.width =
      (ev.total ? Math.round((ev.current / ev.total) * 100) : (busy ? 0 : 100)) + "%";
    document.getElementById("proc-meta-left").textContent = busy ? "processando…" : (ev.status === "error" ? "erro no processamento" : "concluído");
    document.getElementById("proc-meta-right").textContent =
      ev.review.length > 0 ? ev.review.length + " sugestões pendentes" : "";
    var noteEl = document.getElementById("proc-note");
    noteEl.style.color = ev.status === "error" ? "var(--warn)" : "var(--ok)";
    document.getElementById("proc-note-text").textContent = ev.status === "error"
      ? ("Erro: " + (ev.error || "veja o terminal"))
      : "Processando nesta máquina — nenhuma foto sai da pasta";

    document.getElementById("btn-rescan").disabled = busy;
    document.getElementById("btn-rescan-label").textContent = busy ? "Processando…" : "Reprocessar pasta";

    document.getElementById("count-identified").textContent = pessoasTotal + " pessoas";
    document.getElementById("count-review").textContent = ev.review.length + " pares";
    document.getElementById("count-unidentified").textContent = fmt(ev.unidentified.rostos) + " rostos";

    var reviewHead = document.getElementById("section-head-review");
    var reviewHint = document.getElementById("section-hint-review");
    var showReview = !busy && ev.review.length > 0;
    reviewHead.style.display = showReview ? "" : "none";
    reviewHint.style.display = showReview ? "" : "none";

    // The face-grouping pass only runs once the whole scan finishes, so every
    // detected face sits with no person assigned yet while status is busy --
    // that is NOT the same as "couldn't identify this person", so the section
    // stays hidden during scanning/clustering instead of showing a scary count.
    var unidHead = document.getElementById("section-head-unidentified");
    var showUnid = !busy && ev.unidentified.rostos > 0;
    unidHead.style.display = showUnid ? "" : "none";
    document.getElementById("grid-unidentified").style.display = showUnid ? "" : "none";

    var identifiedNote = busy
      ? (ev.stats.rostos > 0
          ? fmt(ev.stats.rostos) + " rostos encontrados até agora — o agrupamento por pessoa roda quando a varredura terminar."
          : "Analisando… as primeiras pessoas aparecem aqui em breve.")
      : "Nenhuma pessoa identificada ainda.";

    document.getElementById("grid-identified").innerHTML = ev.people.map(function (p) {
      return personCard(p, false);
    }).join("") || emptyGridNote(identifiedNote);

    document.getElementById("grid-review").innerHTML = ev.review.map(function (p) {
      return personCard(p, true);
    }).join("");

    document.getElementById("grid-unidentified").innerHTML = showUnid ? (
      '<div class="card unident-card" tabindex="0">' +
        '<div class="sprocket"></div>' +
        '<div class="card-face">' + genericFaceIcon() + "</div>" +
        '<div class="card-body">' +
          '<span class="card-name">Não identificados</span>' +
          '<span class="card-sub">' + fmt(ev.unidentified.rostos) + " rostos · " + fmt(ev.unidentified.fotos) + " fotos</span>" +
        "</div>" +
      "</div>"
    ) : "";

    document.getElementById("footer-text").innerHTML =
      "<b>" + pessoasTotal + " pessoas</b> prontas · a estrutura de pastas espelha o nome de cada uma";
    document.getElementById("btn-export").disabled = pessoasTotal === 0;
  }

  function emptyGridNote(text) {
    return '<p style="color:var(--text-faint);font-size:12.5px;grid-column:1/-1;">' + escapeHtml(text) + "</p>";
  }

  function genericFaceIcon() {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" style="color:var(--text-faint);width:56px;height:56px;opacity:.5;"><circle cx="12" cy="8" r="4"/><path d="M4 20c0-4.4 3.6-7 8-7s8 2.6 8 7"/></svg>';
  }

  function personCard(p, isReview) {
    var flag = isReview ? '<span class="badge-flag">revisar</span>' : "";
    return (
      '<div class="card' + (isReview ? " review" : "") + '" tabindex="0" data-person-id="' + p.id + '" data-review="' + (isReview ? 1 : 0) +
        '" data-merge-id="' + (p.mergeWithId || "") + '" data-merge-name="' + escapeHtml(p.mergeWith || "") + '">' +
        '<div class="sprocket"></div>' +
        '<div class="card-face">' +
          '<img src="/api/people/' + p.id + '/cover" alt="" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement(\'span\'),{innerHTML:\'\'}))">' +
          flag +
          '<span class="badge-count">' + p.count + "</span>" +
        "</div>" +
        '<div class="card-body">' +
          '<span class="card-name">' + escapeHtml(p.name) + "</span>" +
          '<span class="card-sub">' + p.count + " fotos</span>" +
        "</div>" +
      "</div>"
    );
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function startPolling() {
    if (state.pollTimer) return;
    state.pollTimer = setInterval(function () {
      loadEventsList().catch(handleLoadError);
      refreshActive().catch(handleLoadError);
    }, 1600);
  }

  function stopPolling() {
    if (state.pollTimer) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  // ---- person detail panel ----

  function openPersonPanel(personId, isReview, mergeWithId, mergeWithName) {
    var overlay = document.getElementById("overlay");
    var nameEl = document.getElementById("panel-name");
    var avatarEl = document.getElementById("panel-avatar");
    var mergeBtn = document.getElementById("panel-merge");

    state.currentPanelPerson = personId;
    nameEl.textContent = "…";
    avatarEl.innerHTML = genericFaceIcon();
    document.getElementById("panel-sub").textContent = "";
    document.getElementById("panel-grid").innerHTML = "";

    if (isReview && mergeWithId) {
      mergeBtn.style.display = "";
      mergeBtn.textContent = "Mesclar com " + mergeWithName;
      mergeBtn.onclick = function () { mergePerson(personId, mergeWithId); };
    } else {
      mergeBtn.style.display = "none";
    }

    overlay.classList.add("open");

    api("/api/people/" + personId + "/photos").then(function (data) {
      nameEl.textContent = data.person;
      avatarEl.innerHTML = '<img src="/api/people/' + personId + '/cover" alt="">';
      document.getElementById("panel-sub").textContent = data.photos.length + " fotos";

      document.getElementById("panel-grid").innerHTML = data.photos.map(function (photo) {
        var tags = photo.with.map(function (o) {
          return '<span class="tag">+ ' + escapeHtml(o.name) + "</span>";
        }).join("");
        return (
          '<div class="pgrid-item">' +
            (tags ? '<div class="tags">' + tags + "</div>" : "") +
            '<img src="/api/photos/' + photo.id + '/thumbnail" alt="" loading="lazy">' +
            '<span class="fname">' + escapeHtml(photo.filename) + "</span>" +
          "</div>"
        );
      }).join("");
    }).catch(function (err) {
      nameEl.textContent = "Erro ao carregar";
      showToast(err.message);
    });
  }

  function closePersonPanel() {
    document.getElementById("overlay").classList.remove("open");
  }

  function saveRename() {
    var nameEl = document.getElementById("panel-name");
    var personId = state.currentPanelPerson;
    var name = nameEl.textContent.trim();
    if (!personId || !name) return;
    api("/api/people/" + personId + "/rename", { method: "POST", body: { name: name } })
      .then(function () { return refreshActive(); })
      .catch(function (err) { showToast(err.message); });
  }

  function mergePerson(sourceId, targetId) {
    api("/api/people/merge", { method: "POST", body: { source_id: sourceId, target_id: targetId } })
      .then(function () {
        closePersonPanel();
        showToast("Mesclado.");
        return refreshActive();
      })
      .catch(function (err) { showToast(err.message); });
  }

  // ---- new event modal ----

  function openNewEventModal() {
    document.getElementById("new-event-label").value = "";
    document.getElementById("new-event-path").value = "";
    document.getElementById("new-event-error").textContent = "";
    document.getElementById("overlay-new-event").classList.add("open");
    document.getElementById("event-menu").classList.remove("open");
  }

  function closeNewEventModal() {
    document.getElementById("overlay-new-event").classList.remove("open");
  }

  function pickFolder() {
    var btn = document.getElementById("new-event-pick");
    btn.disabled = true;
    btn.textContent = "Abrindo o Finder…";
    api("/api/pick-folder", { method: "POST" }).then(function (res) {
      if (res.path) {
        document.getElementById("new-event-path").value = res.path;
      } else if (res.error === "not_macos") {
        document.getElementById("new-event-error").textContent =
          "Seletor nativo só funciona no Mac — cole o caminho da pasta manualmente.";
      }
    }).catch(function () {
      document.getElementById("new-event-error").textContent = "Não foi possível abrir o seletor — cole o caminho manualmente.";
    }).finally(function () {
      btn.disabled = false;
      btn.textContent = "Selecionar…";
    });
  }

  function createEvent() {
    var label = document.getElementById("new-event-label").value.trim();
    var path = document.getElementById("new-event-path").value.trim();
    var errorEl = document.getElementById("new-event-error");
    if (!label || !path) {
      errorEl.textContent = "Preencha o nome e a pasta do evento.";
      return;
    }
    var btn = document.getElementById("new-event-create");
    btn.disabled = true;
    api("/api/events", { method: "POST", body: { label: label, folder_path: path } })
      .then(function (res) {
        closeNewEventModal();
        state.activeId = res.id;
        localStorage.setItem("revelacao_active_event", res.id);
        return loadEventsList().then(refreshActive);
      })
      .catch(function (err) { errorEl.textContent = err.message; })
      .finally(function () { btn.disabled = false; });
  }

  // ---- export ----

  function exportEvent() {
    var btn = document.getElementById("btn-export");
    btn.disabled = true;
    btn.textContent = "Criando pastas…";
    api("/api/events/" + state.activeId + "/export", { method: "POST", body: { output_path: state.detail.path + "/Organizado por pessoa" } })
      .then(function (res) {
        showToast(res.people.length + " pastas criadas em " + res.output_path);
      })
      .catch(function (err) { showToast(err.message); })
      .finally(function () {
        btn.disabled = false;
        btn.textContent = "Criar pastas no Finder";
      });
  }

  function rescanEvent() {
    api("/api/events/" + state.activeId + "/rescan", { method: "POST" }).then(function () {
      return refreshActive();
    });
  }

  // ---- wiring ----

  document.addEventListener("click", function (e) {
    var card = e.target.closest(".card[data-person-id]");
    if (card) {
      openPersonPanel(
        Number(card.getAttribute("data-person-id")),
        card.getAttribute("data-review") === "1",
        card.getAttribute("data-merge-id") ? Number(card.getAttribute("data-merge-id")) : null,
        card.getAttribute("data-merge-name")
      );
      return;
    }

    if (e.target.closest("#event-switch")) {
      var menu = document.getElementById("event-menu");
      var open = menu.classList.toggle("open");
      document.getElementById("event-switch").setAttribute("aria-expanded", open ? "true" : "false");
      return;
    }
    var row = e.target.closest(".event-row");
    if (row) { switchEvent(row.getAttribute("data-id")); return; }
    if (e.target.closest("#event-new-btn")) { openNewEventModal(); return; }

    if (!e.target.closest(".event-menu") && !e.target.closest("#event-switch")) {
      document.getElementById("event-menu").classList.remove("open");
      document.getElementById("event-switch").setAttribute("aria-expanded", "false");
    }
  });

  document.getElementById("panel-close").addEventListener("click", closePersonPanel);
  document.getElementById("overlay").addEventListener("click", function (e) {
    if (e.target.id === "overlay") closePersonPanel();
  });
  document.getElementById("panel-name").addEventListener("blur", saveRename);
  document.getElementById("panel-name").addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); e.target.blur(); }
  });

  document.getElementById("btn-empty-add").addEventListener("click", openNewEventModal);
  document.getElementById("new-event-close").addEventListener("click", closeNewEventModal);
  document.getElementById("overlay-new-event").addEventListener("click", function (e) {
    if (e.target.id === "overlay-new-event") closeNewEventModal();
  });
  document.getElementById("new-event-pick").addEventListener("click", pickFolder);
  document.getElementById("new-event-create").addEventListener("click", createEvent);

  document.getElementById("btn-export").addEventListener("click", exportEvent);
  document.getElementById("btn-rescan").addEventListener("click", rescanEvent);

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      closePersonPanel();
      closeNewEventModal();
      document.getElementById("event-menu").classList.remove("open");
    }
  });

  document.getElementById("btn-logout").addEventListener("click", function () {
    api("/api/auth/logout", { method: "POST" }).finally(function () {
      window.location.href = "/login.html";
    });
  });

  document.getElementById("btn-pair").addEventListener("click", function () {
    api("/api/pairing-tokens", { method: "POST" }).then(function (res) {
      window.prompt(
        "Cole este código no terminal do computador com as fotos (ele pede na primeira vez que você abrir o revelação lá):",
        res.token
      );
    }).catch(function (err) { showToast(err.message); });
  });

  function init() {
    loadEventsList().then(refreshActive).catch(handleLoadError);
  }

  // /api/auth/me only exists when this page is served by the hub (not the
  // local standalone server at 127.0.0.1:8420, which has no auth routes at
  // all -- that request 404s there, and local usage should proceed exactly
  // as before). 401 means hub mode but not logged in; 200 means logged in.
  fetch("/api/auth/me", { credentials: "same-origin" })
    .then(function (res) {
      if (res.status === 401) {
        window.location.href = "/login.html";
        return;
      }
      if (res.ok) {
        document.getElementById("btn-logout").style.display = "";
        document.getElementById("btn-pair").style.display = "";
      }
      init();
    })
    .catch(init);
})();
