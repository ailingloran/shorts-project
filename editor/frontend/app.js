// WoWS Shorts Editor — Phase A frontend
const $ = (id) => document.getElementById(id);
const api = (p, opts) => fetch(p, opts).then((r) => r.json());

const project = {
  schema_version: 1,
  project_id: "proj_" + Date.now(),
  title: "Untitled short",
  output: { w: 1080, h: 1920, fps: 30, crf: 20, preset: "veryfast" },
  sources: [],
  segments: [],
  audio: { music: { path: null, gain_db: -8, in_s: 0, fade_in_s: 0.4, fade_out_s: 0.8, duck: false } },
  overlays: { counters: [] },
  effects: { flash_on_kills: false, zoom_on_kills: false, zoom_amount: 1.15 },
  transition: { type: "none", duration: 0.4 },
  subtitles: { cues: [], font: "Arial Black", size: 54, margin_v: 180 },
  intro: { enabled: false, dur_s: 1.4, lines: [] },
  outro: { enabled: false, dur_s: 1.6, lines: [] },
};

let sources = [];
let currentSource = null;     // source meta (with width/height/proxy_url)
let selectedSegId = null;
let pendingIn = null, pendingOut = null;
let dragReframeX = null;      // source-px x for the crop box of next/selected segment

const video = $("preview");

// ---- sources ----
function renderSources() {
  const box = $("sources");
  box.innerHTML = "";
  sources.forEach((s) => {
    const el = document.createElement("div");
    el.className = "src" + (currentSource && s.path === currentSource.path ? " active" : "");
    el.innerHTML = `${s.name}<small>${s.width}×${s.height} · ${s.duration_s}s · ${s.audio_tracks} audio</small>`;
    el.onclick = () => selectSource(s);
    box.appendChild(el);
  });
}
async function loadSources() {
  const r = await api("/api/sources");
  sources = r.sources;
  renderSources();
}

function ensureProjectSource(s) {
  let ps = project.sources.find((x) => x.path === s.path);
  if (!ps) {
    ps = {
      id: "src" + project.sources.length,
      path: s.path, width: s.width, height: s.height, fps: s.fps, duration_s: s.duration_s,
      audio_stems: Array.from({ length: Math.max(s.audio_tracks, 1) }, (_, i) => ({
        track: i, role: i === 0 ? "game" : i === 1 ? "mic" : "mix",
        label: i === 0 ? "Game" : i === 1 ? "Mic" : "Track " + (i + 1),
        gain_db: i === 0 ? 0 : -6, mute: false,
      })),
    };
    project.sources.push(ps);
  }
  return ps;
}

function selectSource(s) {
  currentSource = s;
  renderSources();
  ensureProjectSource(s);
  dragReframeX = null;
  buildAudioPanel();
  video.onloadedmetadata = () => { layoutCropBox(); renderScrubber(); };   // set BEFORE src
  video.src = s.proxy_url;
  video.load();
  if (video.readyState >= 1) layoutCropBox();
  renderScrubber();
}

// ---- crop / reframe box ----
function cropWidthSrc() { return Math.round(currentSource.height * project.output.w / project.output.h); }
function defaultX() { return Math.round((currentSource.width - cropWidthSrc()) / 2); }

function layoutCropBox() {
  if (!currentSource) return;
  const crop = $("crop");
  const rect = video.getBoundingClientRect();
  const dispW = rect.width;
  if (dispW < 10) { setTimeout(layoutCropBox, 120); return; }  // video not sized yet
  const cw = cropWidthSrc();
  const x = dragReframeX == null ? defaultX() : dragReframeX;
  crop.style.width = (dispW * cw / currentSource.width) + "px";
  crop.style.left = (dispW * x / currentSource.width) + "px";
}
window.addEventListener("resize", layoutCropBox);

(function dragCrop() {
  const crop = $("crop");
  let dragging = false, startX, startLeft;
  crop.addEventListener("mousedown", (e) => {
    dragging = true; startX = e.clientX; startLeft = parseFloat(crop.style.left) || 0; e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging || !currentSource) return;
    const rect = video.getBoundingClientRect();
    let left = Math.max(0, Math.min(rect.width - crop.offsetWidth, startLeft + (e.clientX - startX)));
    crop.style.left = left + "px";
    dragReframeX = Math.round(left / rect.width * currentSource.width);
    if (selectedSegId) applyReframeToSelected();
  });
  window.addEventListener("mouseup", () => (dragging = false));
})();

function currentReframe() {
  return { mode: "static", x: dragReframeX == null ? defaultX() : dragReframeX,
           y: 0, w: cropWidthSrc(), h: currentSource.height };
}
function applyReframeToSelected() {
  const seg = project.segments.find((s) => s.id === selectedSegId);
  if (!seg) return;
  const r = currentReframe();
  // update the static box coords but preserve mode + any keyframes
  seg.reframe.x = r.x; seg.reframe.y = r.y; seg.reframe.w = r.w; seg.reframe.h = r.h;
}

$("kfadd").onclick = () => {
  const seg = project.segments.find((s) => s.id === selectedSegId);
  if (!seg) return alert("Select a segment first (click it in the timeline).");
  const t = Math.max(0, Math.min(seg.out_s - seg.in_s, video.currentTime - seg.in_s));
  const r = currentReframe();
  seg.reframe.mode = "keyframed";
  seg.reframe.keyframes = seg.reframe.keyframes || [];
  seg.reframe.keyframes = seg.reframe.keyframes.filter((k) => Math.abs(k.t - t) > 0.05);
  seg.reframe.keyframes.push({ t: +t.toFixed(2), x: r.x, y: r.y, w: r.w, h: r.h });
  seg.reframe.keyframes.sort((a, b) => a.t - b.t);
  renderTimeline();
  setStatus(`reframe key @ ${t.toFixed(1)}s · ${seg.reframe.keyframes.length} total (2+ = pan)`);
};

// ---- transport / segments ----
$("play").onclick = () => (video.paused ? video.play() : video.pause());
video.ontimeupdate = () => { $("time").textContent = `${video.currentTime.toFixed(2)} / ${(video.duration || 0).toFixed(2)}`; updatePlayhead(); };
$("setin").onclick = () => { pendingIn = video.currentTime; updateInOut(); renderScrubber(); };
$("setout").onclick = () => { pendingOut = video.currentTime; updateInOut(); renderScrubber(); };
function updateInOut() {
  $("inout").textContent = `in ${pendingIn == null ? "—" : pendingIn.toFixed(2)} · out ${pendingOut == null ? "—" : pendingOut.toFixed(2)}`;
}

// ---- source scrubber (kill markers + segment bands + playhead) ----
function srcDur() { return (currentSource && currentSource.duration_s) || video.duration || 1; }

function renderScrubber() {
  const sc = $("scrubber");
  if (!currentSource) { sc.innerHTML = ""; return; }
  const dur = srcDur();
  const ps = currentProjectSource();
  let html = "";
  // marked segment bands for this source
  (project.segments || []).filter((s) => ps && s.source_id === ps.id).forEach((s) => {
    html += `<div class="segband" style="left:${s.in_s / dur * 100}%;width:${(s.out_s - s.in_s) / dur * 100}%"></div>`;
  });
  // pending IN/OUT
  if (pendingIn != null && pendingOut != null) {
    const a = Math.min(pendingIn, pendingOut), w = Math.abs(pendingOut - pendingIn);
    html += `<div class="pendband" style="left:${a / dur * 100}%;width:${w / dur * 100}%"></div>`;
  } else if (pendingIn != null) {
    html += `<div class="killtick" style="left:${pendingIn / dur * 100}%;background:var(--accent)"></div>`;
  }
  // kill ticks (battle_t → src_t via offset)
  const off = (ps && ps.battle_offset_s) || 0;
  attachedKills.forEach((k) => {
    const t = k.t - off;
    if (t < 0 || t > dur) return;
    const label = (k.victim_ship || k.victim || "kill") + " · " + (k.weapon || "").toLowerCase() + " @ " + t.toFixed(0) + "s";
    html += `<div class="killtick" data-t="${t}" title="${label}" style="left:${t / dur * 100}%"></div>`;
  });
  html += `<div class="playhead" id="playhead" style="left:${(video.currentTime || 0) / dur * 100}%"></div>`;
  sc.innerHTML = html;
  sc.querySelectorAll(".killtick[data-t]").forEach((el) => {
    el.onclick = (e) => { e.stopPropagation(); video.currentTime = Math.max(0, parseFloat(el.dataset.t)); };
  });
}
function updatePlayhead() {
  const ph = $("playhead");
  if (ph) ph.style.left = (video.currentTime || 0) / srcDur() * 100 + "%";
}
$("scrubber").onclick = (e) => {
  if (!currentSource) return;
  const r = e.currentTarget.getBoundingClientRect();
  video.currentTime = Math.max(0, (e.clientX - r.left) / r.width * srcDur());
};
window.addEventListener("resize", renderScrubber);

$("addseg").onclick = () => {
  if (!currentSource) return alert("Pick a source on the left first.");
  const dur = srcDur();
  let inS = pendingIn, outS = pendingOut;
  // Forgiving: fill in whatever wasn't marked, so "Add" always makes a clip.
  if (inS == null && outS == null) { inS = video.currentTime; outS = Math.min(dur, inS + 10); }
  else if (outS == null) { outS = video.currentTime; }
  else if (inS == null) { inS = outS; outS = video.currentTime; }
  if (outS <= inS) outS = Math.min(dur, inS + 10);          // ensure positive length
  if (outS - inS < 0.3) return alert("Scrub forward a bit before adding — the clip is too short.");
  const ps = ensureProjectSource(currentSource);
  project.segments.push({
    id: "seg" + Date.now(), source_id: ps.id,
    in_s: +inS.toFixed(2), out_s: +outS.toFixed(2), speed: 1.0,
    reframe: currentReframe(),
  });
  pendingIn = pendingOut = null; updateInOut(); renderTimeline(); renderScrubber();
  setStatus(`added clip ${inS.toFixed(1)}s → ${outS.toFixed(1)}s`);
};

function renderTimeline() {
  const tl = $("timeline"); tl.innerHTML = "";
  project.segments.forEach((seg, i) => {
    const el = document.createElement("div");
    el.className = "seg" + (seg.id === selectedSegId ? " active" : "");
    const spd = seg.speed && seg.speed !== 1 ? ` · ${seg.speed}×` : "";
    const nkf = seg.reframe && seg.reframe.mode === "keyframed" && (seg.reframe.keyframes || []).length;
    const kf = nkf ? ` · ${nkf}◇ pan` : "";
    el.innerHTML = `#${i + 1} <span class="del">✕</span><small>${seg.in_s}s → ${seg.out_s}s${spd}${kf}</small>
      <select class="spd">
        <option value="0.25">0.25× slo-mo</option><option value="0.5">0.5×</option>
        <option value="1">1× normal</option><option value="1.5">1.5×</option><option value="2">2× fast</option>
      </select>`;
    el.onclick = (e) => { if (!e.target.classList.contains("del") && e.target.tagName !== "SELECT") selectSegment(seg); };
    el.querySelector(".del").onclick = () => { project.segments = project.segments.filter((x) => x.id !== seg.id); renderTimeline(); renderScrubber(); };
    const sel = el.querySelector(".spd");
    sel.value = String(seg.speed || 1);
    sel.onchange = () => { seg.speed = parseFloat(sel.value); renderTimeline(); };
    tl.appendChild(el);
  });
}

function selectSegment(seg) {
  selectedSegId = seg.id;
  const s = sources.find((x) => project.sources.find((p) => p.id === seg.source_id)?.path === x.path);
  if (s) {
    if (currentSource?.path !== s.path) selectSource(s);
    dragReframeX = seg.reframe?.x ?? null;
    video.currentTime = seg.in_s;
    setTimeout(layoutCropBox, 50);
  }
  renderTimeline();
  renderScrubber();
}

// ---- audio / music / cards ----
function buildAudioPanel() {
  const ps = project.sources.find((x) => x.path === currentSource.path);
  const box = $("audio"); box.innerHTML = "";
  ps.audio_stems.forEach((st) => {
    const row = document.createElement("label");
    row.innerHTML = `${st.label} <input type="range" min="-30" max="6" value="${st.gain_db}"> <span>${st.gain_db} dB</span> <input type="checkbox" ${st.mute ? "checked" : ""}> mute`;
    const [range, , chk] = row.children;   // children: [range, span, checkbox]
    range.oninput = () => { st.gain_db = +range.value; range.nextElementSibling.textContent = st.gain_db + " dB"; };
    chk.onchange = () => (st.mute = chk.checked);
    box.appendChild(row);
  });
}
$("musicpath").oninput = (e) => (project.audio.music.path = e.target.value || null);
$("musicgain").oninput = (e) => { project.audio.music.gain_db = +e.target.value; $("musicgainv").textContent = e.target.value + " dB"; };
$("musicduck").onchange = (e) => { project.audio.music.duck = e.target.checked; };

// ---- subtitles ----
function renderSubs() {
  const box = $("sublist");
  if (!project.subtitles.cues.length) { box.textContent = "no captions"; return; }
  box.innerHTML = "";
  project.subtitles.cues
    .sort((a, b) => a.proj_in_s - b.proj_in_s)
    .forEach((c, i) => {
      const row = document.createElement("div");
      row.innerHTML = `${c.proj_in_s}-${c.proj_out_s}s: ${c.text} <span style="color:var(--red);cursor:pointer">✕</span>`;
      row.querySelector("span").onclick = () => {
        project.subtitles.cues.splice(i, 1); renderSubs();
      };
      box.appendChild(row);
    });
}
$("subadd").onclick = () => {
  const text = $("subtext").value.trim();
  if (!text) return;
  const a = parseFloat($("subin").value) || 0, b = parseFloat($("subout").value) || 0;
  if (b <= a) return alert("Caption OUT must be after IN.");
  project.subtitles.cues.push({ proj_in_s: a, proj_out_s: b, text });
  $("subtext").value = "";
  renderSubs();
};

function syncTransition() {
  project.transition = { type: $("trtype").value, duration: parseFloat($("trdur").value) || 0.4 };
}
function syncEffects() {
  project.effects = { flash_on_kills: $("fxflash").checked, zoom_on_kills: $("fxzoom").checked,
                      zoom_amount: parseFloat($("fxamt").value) || 1.15 };
}

function syncCards() {
  project.title = $("title").value;
  project.intro = { enabled: $("introon").checked, dur_s: 1.4,
    lines: $("introtext").value ? [{ text: $("introtext").value, style: "huge_gold" }] : [] };
  project.outro = { enabled: $("outroon").checked, dur_s: 1.6,
    lines: $("outrotext").value ? [{ text: $("outrotext").value, style: "med" }] : [] };
}

// ---- counters (replay data) ----
function currentProjectSource() {
  return currentSource && project.sources.find((x) => x.path === currentSource.path);
}

async function loadReplays() {
  const r = await api("/api/replays");
  const sel = $("replaysel");
  r.replays.slice(0, 60).forEach((rep) => {
    const o = document.createElement("option");
    o.value = rep.path; o.textContent = rep.name;
    sel.appendChild(o);
  });
}

$("attachreplay").onclick = async () => {
  const ps = currentProjectSource();
  if (!ps) return alert("Load a source first.");
  const replay = $("replaysel").value;
  if (!replay) return alert("Pick a replay.");
  ps.replay_path = replay;
  ps.focus_player = null;
  ps.battle_offset_s = parseFloat($("offset").value) || 0;
  $("replayinfo").textContent = "decoding…";
  const d = await api(`/api/decode?replay=${encodeURIComponent(replay)}`);
  ps.events_available = true;
  attachedKills = d.kills || [];
  $("replayinfo").textContent = `POV ${d.focus_player}${d.focus_ship ? " · " + d.focus_ship : ""} · ${d.frags_total} frags · ${d.kill_count} kills · ${d.map}`;
  renderKillChips();
  renderScrubber();
};

let attachedKills = [];
function renderKillChips() {
  const box = $("killchips"); box.innerHTML = "";
  const ps = currentProjectSource();
  if (!ps) return;
  const off = ps.battle_offset_s || 0;
  attachedKills.forEach((k) => {
    const src = k.t - off;
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = `${src.toFixed(0)}s · ${(k.weapon || "kill").toLowerCase()} ${k.victim}`;
    chip.onclick = () => { if (src >= 0) video.currentTime = src; };
    box.appendChild(chip);
  });
}

$("suggest").onclick = async () => {
  const ps = currentProjectSource();
  if (!ps || !ps.replay_path) return alert("Attach a replay first.");
  const dur = currentSource.duration_s || "";
  const r = await api(`/api/suggest?replay=${encodeURIComponent(ps.replay_path)}&offset=${ps.battle_offset_s || 0}&max_src=${dur}`);
  if (!r.segments.length) return alert("No highlight moments land inside this clip — check the battle offset.");
  r.segments.forEach((s) => project.segments.push({
    id: "seg" + Date.now() + Math.random().toString(36).slice(2, 5),
    source_id: ps.id, in_s: s.in_s, out_s: s.out_s, speed: 1.0, reframe: currentReframe(),
  }));
  renderTimeline();
  setStatus(`added ${r.segments.length} suggested segment(s)`);
};

function setOffset(v) {
  $("offset").value = (Math.round(v * 2) / 2).toFixed(1);
  const ps = currentProjectSource();
  if (ps) ps.battle_offset_s = parseFloat($("offset").value);
  renderKillChips();
  renderScrubber();
}
$("offset").oninput = () => setOffset(parseFloat($("offset").value) || 0);
$("offminus").onclick = () => setOffset((parseFloat($("offset").value) || 0) - 2);
$("offplus").onclick = () => setOffset((parseFloat($("offset").value) || 0) + 2);

function buildCounters() {
  const c = [];
  if ($("ctrfrags").checked) c.push({ kind: "frags", enabled: true, anchor: "bottom_left", dx: 0, dy: 0 });
  if ($("ctrdmg").checked) c.push({ kind: "damage", enabled: true, anchor: "bottom_right", dx: 0, dy: 0 });
  project.overlays = { counters: c };
}

// ---- save / export ----
$("save").onclick = async () => { syncCards(); buildCounters(); syncTransition(); syncEffects(); await api("/api/project", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(project) }); setStatus("saved ✓"); };

$("export").onclick = async () => {
  if (!project.segments.length) return alert("Add at least one segment.");
  syncCards(); buildCounters(); syncTransition(); syncEffects();
  const needsReplay = project.overlays.counters.length || project.effects.flash_on_kills || project.effects.zoom_on_kills;
  const hasReplay = project.sources.some((s) => s.replay_path);
  if (needsReplay && !hasReplay &&
      !confirm("Counters and flash/zoom-on-kills need replay data.\n\nAttach a replay first: Counters panel → pick the replay → Attach to source.\n\nExport anyway without them?")) {
    return;
  }
  setStatus("exporting…");
  await api("/api/export/" + project.project_id, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(project) });
  const poll = setInterval(async () => {
    const st = await api("/api/export/" + project.project_id);
    if (st.state === "done") { clearInterval(poll); setStatus("done ✓"); showResult(st.out); }
    else if (st.state === "error") { clearInterval(poll); setStatus("error"); alert("Export failed:\n" + st.error); }
    else setStatus("exporting…");
  }, 1200);
};

function showResult(url) {
  const w = window.open("", "_blank", "width=420,height=820");
  w.document.write(`<title>Export</title><body style="margin:0;background:#000"><video src="${url}" controls autoplay style="width:100%"></video><p style="color:#fff;font-family:sans-serif;text-align:center"><a style="color:#ffcd46" href="${url}" download>download mp4</a></p>`);
}
function setStatus(t) { $("status").textContent = t; }

// ---- OBS capture ----
let obsPoll = null;
$("obsconnect").onclick = async () => {
  const pw = encodeURIComponent($("obspass").value || "");
  const r = await api(`/api/obs/connect?password=${pw}`, { method: "POST" });
  if (r.connected) {
    $("obsstatus").textContent = `OBS ${r.obs_version} ✓  (rec → ${r.record_dir || "?"})`;
    $("obscontrols").style.display = "block";
  } else {
    $("obsstatus").textContent = "not connected — open OBS, enable Tools → WebSocket (port 4455)";
  }
};
$("obsstart").onclick = async () => {
  const r = await api("/api/obs/start", { method: "POST" });
  if (!r.ok) return alert("Start failed: " + (r.error || ""));
  $("obsrec").textContent = "● recording…";
  clearInterval(obsPoll);
  obsPoll = setInterval(async () => {
    const s = await api("/api/obs/status");
    $("obsrec").textContent = s.recording
      ? `● recording ${s.rec_duration_s || 0}s` + (s.has_battle_start ? ` · offset ${s.battle_offset}s` : " · ⚓ mark battle start!")
      : "stopped";
  }, 1000);
};
$("obsbattle").onclick = async () => {
  const r = await api("/api/obs/battlestart", { method: "POST" });
  if (r.ok) $("obsrec").textContent = `● recording · battle start set (offset ${r.battle_offset}s)`;
};
$("obsmark").onclick = async () => { await api("/api/obs/mark", { method: "POST" }); };
$("obsstop").onclick = async () => {
  clearInterval(obsPoll);
  const r = await api("/api/obs/stop", { method: "POST" });
  if (!r.ok) return alert("Stop failed: " + (r.error || ""));
  $("obsrec").textContent = `saved · offset ${r.battle_offset}s · ${r.markers.length} marks`;
  if (r.source) {
    sources.unshift(r.source);
    selectSource(r.source);
    const ps = currentProjectSource();
    if (ps) { ps.battle_offset_s = r.source.battle_offset_s || 0; $("offset").value = ps.battle_offset_s; }
    setStatus("recording loaded — attach its replay for counters");
  }
};

loadSources();
loadReplays();
