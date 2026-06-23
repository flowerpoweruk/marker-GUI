/* Marker Studio — client logic */
(() => {
  "use strict";

  const SUPPORTED = ["pdf","png","jpg","jpeg","gif","webp","tiff","bmp","docx","pptx","xlsx","html","htm","epub"];
  const $ = (id) => document.getElementById(id);

  const state = {
    files: [],          // { uid, file, name, ext }
    outputDir: "",
    format: "markdown",
    batchId: null,
    polling: null,
    converting: false,
  };

  let uid = 0;

  /* ----------------------------------------------------------- status -- */
  async function pollStatus() {
    try {
      const r = await fetch("/api/status");
      const s = await r.json();
      const dot = $("status-dot"), txt = $("status-text");
      if (s.error) {
        dot.className = "dot bad";
        txt.textContent = "Model load failed";
      } else if (s.models_ready) {
        dot.className = "dot ready";
        txt.textContent = `Ready · ${s.device.toUpperCase()}`;
      } else {
        dot.className = "dot warming";
        txt.textContent = "Loading models…";
      }
      if (!state.outputDir && s.default_output) {
        setFolder(s.default_output, /*silent*/ true);
      }
    } catch {
      $("status-dot").className = "dot bad";
      $("status-text").textContent = "Backend offline";
    }
  }

  /* ------------------------------------------------------------ files -- */
  function addFiles(fileList) {
    let added = 0, rejected = 0;
    for (const file of fileList) {
      const ext = (file.name.includes(".") ? file.name.split(".").pop() : "").toLowerCase();
      if (!SUPPORTED.includes(ext)) { rejected++; continue; }
      if (state.files.some((f) => f.name === file.name && f.file.size === file.size)) continue;
      state.files.push({ uid: ++uid, file, name: file.name, ext });
      added++;
    }
    renderQueue();
    refreshConvert();
    if (rejected) toast(`Skipped ${rejected} unsupported file${rejected > 1 ? "s" : ""}.`, true);
  }

  function removeFile(u) {
    state.files = state.files.filter((f) => f.uid !== u);
    renderQueue();
    refreshConvert();
  }

  function renderQueue() {
    const list = $("queue"), head = $("queue-head");
    list.innerHTML = "";
    head.hidden = state.files.length === 0;
    $("queue-count").textContent = state.files.length;

    for (const f of state.files) {
      const li = document.createElement("li");
      li.className = "qitem";
      li.dataset.uid = f.uid;
      li.innerHTML = `
        <span class="qbadge">${f.ext}</span>
        <span class="qmeta">
          <span class="qname">${escapeHtml(f.name)}</span>
          <span class="qstate" data-role="state">${humanSize(f.file.size)}</span>
        </span>
        <span data-role="action"></span>`;
      const remove = document.createElement("button");
      remove.className = "qremove";
      remove.innerHTML = "&times;";
      remove.title = "Remove";
      remove.onclick = () => removeFile(f.uid);
      if (!state.converting) li.querySelector('[data-role="action"]').replaceWith(remove);
      list.appendChild(li);
    }
  }

  /* ----------------------------------------------------------- folder -- */
  function setFolder(path, silent) {
    state.outputDir = path || "";
    const el = $("folder-path");
    if (state.outputDir) {
      el.textContent = state.outputDir;
      el.classList.remove("unset");
    } else {
      el.textContent = "Choose output folder…";
      el.classList.add("unset");
    }
    refreshConvert();
    if (!silent && state.outputDir) toast("Output folder set.");
  }

  async function pickFolder() {
    try {
      const r = await fetch("/api/pick-folder", { method: "POST" });
      const data = await r.json();
      if (data.path) { setFolder(data.path); return; }
      if (data.error) { promptForFolder(data.error); }
    } catch {
      promptForFolder("Couldn't open the folder picker.");
    }
  }

  function promptForFolder(reason) {
    const p = window.prompt(`${reason}\nType or paste a full folder path:`, state.outputDir || "");
    if (p) setFolder(p.trim());
  }

  /* --------------------------------------------------------- settings -- */
  function collectSettings() {
    const s = {
      output_format: state.format,
      page_range: $("page_range").value.trim(),
      force_ocr: $("force_ocr").checked,
      strip_existing_ocr: $("strip_existing_ocr").checked,
      paginate_output: $("paginate_output").checked,
      disable_image_extraction: $("disable_image_extraction").checked,
      disable_ocr_math: $("disable_ocr_math").checked,
      use_llm: $("use_llm").checked,
    };
    if (s.use_llm) {
      const svc = $("llm_service").value;
      s.llm_service = svc;
      s.llm_api_key = $("llm_api_key").value;
      s.llm_model = $("llm_model").value.trim();
      s.redo_inline_math = $("redo_inline_math").checked;
      const base = $("llm_base_url").value.trim();
      if (svc === "openai") s.openai_base_url = base;
      if (svc === "ollama") s.ollama_base_url = base || "http://localhost:11434";
    }
    return s;
  }

  /* ---------------------------------------------------------- convert -- */
  function refreshConvert() {
    const btn = $("convert-btn"), label = $("convert-label");
    const n = state.files.length;
    const ok = n > 0 && state.outputDir && !state.converting;
    btn.disabled = !ok;
    if (state.converting) label.textContent = "Converting…";
    else label.textContent = n === 0 ? "Convert" : `Convert ${n} file${n > 1 ? "s" : ""}`;
  }

  async function startConvert() {
    if (!state.files.length || !state.outputDir) return;
    state.converting = true;
    refreshConvert();
    renderQueue();
    markAll("queued", "Queued");

    try {
      const fd = new FormData();
      state.files.forEach((f) => fd.append("files", f.file, f.name));
      const up = await fetch("/api/upload", { method: "POST", body: fd });
      if (!up.ok) throw new Error((await up.json()).detail || "Upload failed");
      const { files } = await up.json();

      const body = { files, output_dir: state.outputDir, settings: collectSettings() };
      const cv = await fetch("/api/convert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!cv.ok) throw new Error((await cv.json()).detail || "Couldn't start");
      const { batch_id } = await cv.json();
      state.batchId = batch_id;
      pollJobs();
    } catch (e) {
      state.converting = false;
      refreshConvert();
      renderQueue();
      toast(e.message || "Something went wrong.", true);
    }
  }

  function pollJobs() {
    clearInterval(state.polling);
    state.polling = setInterval(async () => {
      try {
        const r = await fetch(`/api/jobs/${state.batchId}`);
        const data = await r.json();
        applyJobStates(data.jobs);
        if (data.jobs.every((j) => ["done", "error", "skipped"].includes(j.status))) {
          clearInterval(state.polling);
          state.converting = false;
          refreshConvert();
          const done = data.jobs.filter((j) => j.status === "done").length;
          const failed = data.jobs.filter((j) => j.status === "error").length;
          toast(failed ? `${done} done · ${failed} failed.` : `All ${done} files converted.`, !!failed);
        }
      } catch { /* keep trying */ }
    }, 900);
  }

  // map staged job (by filename) onto the queue rows
  function applyJobStates(jobs) {
    for (const j of jobs) {
      const f = state.files.find((x) => x.name === j.name);
      if (!f) continue;
      const li = document.querySelector(`.qitem[data-uid="${f.uid}"]`);
      if (!li) continue;
      const stateEl = li.querySelector('[data-role="state"]');
      const actionEl = li.querySelector('[data-role="action"]') || li.lastElementChild;

      stateEl.className = "qstate";
      if (j.status === "converting") {
        stateEl.classList.add("is-converting");
        stateEl.innerHTML = `<span class="spin"></span> Reading the document…`;
      } else if (j.status === "queued") {
        stateEl.textContent = "Queued";
      } else if (j.status === "done") {
        stateEl.classList.add("is-done");
        stateEl.textContent = "Converted";
        addReveal(li, j.output_dir);
      } else if (j.status === "error") {
        stateEl.classList.add("is-error");
        stateEl.textContent = j.message || "Failed";
      }
    }
  }

  function addReveal(li, dir) {
    const slot = li.querySelector(".qremove, .qaction");
    const btn = document.createElement("button");
    btn.className = "qaction";
    btn.textContent = "Reveal";
    btn.onclick = () => fetch("/api/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: dir }),
    });
    if (slot) slot.replaceWith(btn);
  }

  function markAll(status, text) {
    state.files.forEach((f) => {
      const li = document.querySelector(`.qitem[data-uid="${f.uid}"]`);
      if (li) li.querySelector('[data-role="state"]').textContent = text;
    });
  }

  /* ------------------------------------------------------------ utils -- */
  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function humanSize(b) {
    if (b < 1024) return b + " B";
    if (b < 1048576) return (b / 1024).toFixed(0) + " KB";
    return (b / 1048576).toFixed(1) + " MB";
  }
  let toastTimer;
  function toast(msg, bad) {
    const t = $("toast");
    t.textContent = msg;
    t.className = "toast show" + (bad ? " bad" : "");
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.className = "toast"; }, 2600);
  }

  /* ------------------------------------------------------------ wiring -- */
  function init() {
    const dz = $("dropzone");
    ["dragenter", "dragover"].forEach((ev) =>
      dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
    ["dragleave", "drop"].forEach((ev) =>
      dz.addEventListener(ev, (e) => { e.preventDefault(); if (ev === "drop" || e.target === dz) dz.classList.remove("drag"); }));
    dz.addEventListener("drop", (e) => { if (e.dataTransfer?.files) addFiles(e.dataTransfer.files); });
    dz.addEventListener("click", (e) => { if (e.target.id !== "browse-btn") $("file-input").click(); });

    $("browse-btn").addEventListener("click", (e) => { e.stopPropagation(); $("file-input").click(); });
    $("file-input").addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });
    $("clear-btn").addEventListener("click", () => { state.files = []; renderQueue(); refreshConvert(); });

    // format segmented control
    document.querySelectorAll(".seg").forEach((b) =>
      b.addEventListener("click", () => {
        document.querySelectorAll(".seg").forEach((x) => { x.classList.remove("active"); x.setAttribute("aria-checked", "false"); });
        b.classList.add("active"); b.setAttribute("aria-checked", "true");
        state.format = b.dataset.value;
      }));

    // LLM reveal + provider fields
    $("use_llm").addEventListener("change", (e) => { $("llm-config").hidden = !e.target.checked; });
    $("llm_service").addEventListener("change", (e) => {
      const v = e.target.value;
      $("baseurl-field").hidden = !(v === "ollama" || v === "openai");
      $("apikey-field").hidden = v === "ollama";
    });

    // advanced disclosure
    $("adv-toggle").addEventListener("click", () => {
      const open = $("advanced").hidden;
      $("advanced").hidden = !open;
      $("adv-toggle").setAttribute("aria-expanded", String(open));
    });

    $("folder-btn").addEventListener("click", pickFolder);
    $("convert-btn").addEventListener("click", startConvert);

    pollStatus();
    setInterval(pollStatus, 2500);
    refreshConvert();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
