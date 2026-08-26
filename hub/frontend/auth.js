function postJSON(path, body) {
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(body),
  }).then(function (res) {
    return res.text().then(function (raw) {
      var data = null;
      if (raw) { try { data = JSON.parse(raw); } catch (e) { data = null; } }
      if (!res.ok) {
        var msg = (data && data.detail) || res.statusText || "Erro desconhecido";
        if (Array.isArray(msg)) msg = msg.map(function (d) { return d.msg || JSON.stringify(d); }).join("; ");
        throw new Error(msg);
      }
      return data;
    });
  });
}

function showMsg(el, text, kind) {
  el.textContent = text;
  el.className = "msg " + kind;
}

function wireForm(formId, msgId, submit) {
  var form = document.getElementById(formId);
  var msg = document.getElementById(msgId);
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    msg.className = "msg";
    var btn = form.querySelector("button[type=submit]");
    btn.disabled = true;
    submit(new FormData(form))
      .catch(function (err) { showMsg(msg, err.message, "error"); })
      .finally(function () { btn.disabled = false; });
  });
}
