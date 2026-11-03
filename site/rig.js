/* The rig section. Drag scrubs through frames of the rig film (site/rig/frames),
   each layer button plays that layer's clip. Everything draws to one canvas, so
   hand-offs between frames and clips can be seamless or crossfaded.
   The clips chain: the end of one is the start of the next. */
"use strict";

(function () {
  const root = document.getElementById("rig");
  if (!root) return;

  const stage = document.getElementById("rig-stage");
  const canvas = document.getElementById("rig-canvas");
  const ctx = canvas.getContext("2d");
  const slider = document.getElementById("rig-slider");
  const tourBtn = document.getElementById("rig-tour");
  const soundBtn = document.getElementById("rig-sound");
  const hint = document.getElementById("rig-hint");
  const chips = [...root.querySelectorAll(".rig-chip")];
  const hots = [...root.querySelectorAll(".rig-hot")];
  const panels = [...root.querySelectorAll(".rig-panel")];
  const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* Source frame numbers at 60 fps. Denser where the rig comes apart, so the
     slider spends half its travel on that one second. */
  const IDS = [];
  for (let i = 0; i < 240; i += 5) IDS.push(i);
  for (let i = 240; i < 312; i += 2) IDS.push(i);
  for (let i = 315; i < 360; i += 5) IDS.push(i);
  IDS.push(359);
  const LAST = IDS.length - 1;
  const SECS = IDS.map((n) => n / 60);
  const DURATION = SECS[LAST];

  /* hold: where the layer's layout is complete, before the wipe at the clip's end.
     pose: the film frame that matches the hold, for going back to dragging. */
  const LAYERS = [
    { src: "rig/eyes.mp4", hold: 3.6, pose: LAST },
    { src: "rig/brain.mp4", hold: 3.6, pose: LAST },
    { src: "rig/gut.mp4", hold: 3.6, pose: LAST },
    { src: "rig/fingers.mp4", hold: 3.6, pose: 42 },
  ];
  const videos = LAYERS.map((l) => {
    const v = document.createElement("video");
    v.muted = true;
    v.playsInline = true;
    v.setAttribute("playsinline", "");
    v.preload = "none";
    v.src = l.src;
    v.className = "rig-video";
    v.addEventListener("ended", onEnded);
    v.addEventListener("seeked", () => { dirty = true; });
    v.addEventListener("loadeddata", () => { dirty = true; });
    stage.appendChild(v);
    return v;
  });

  /* ---------- state ---------- */
  const frames = new Array(IDS.length).fill(null);
  let loaded = 0;
  let mode = "rig"; // "rig" = dragging frames, "clip" = a layer's video
  let pos = calm ? LAST : 0; // frame index, fractional while easing
  let target = pos;
  let current = -1; // layer index while in a clip
  let next = null; // layer to run into when this clip ends
  let auto = null; // { t0 } while the film plays by itself
  let rush = null; // layer to open once the frames reach the end
  let wantIntro = !calm;
  let tour = false;
  let sound = false;
  let visible = false;
  let resume = false;
  let dirty = true;
  let raf = 0;
  let pendingFade = false;
  let fadeStart = 0;
  const FADE_MS = 220;
  const snap = document.createElement("canvas");

  /* ---------- frames ---------- */
  function loadFrames() {
    const order = [0, LAST];
    for (const stride of [8, 4, 2, 1]) {
      for (let i = 0; i <= LAST; i += stride) if (!order.includes(i)) order.push(i);
    }
    if (calm) order.reverse();
    let at = 0;
    const pump = () => {
      if (at >= order.length) return;
      const n = order[at++];
      const img = new Image();
      img.onload = img.onerror = () => {
        if (img.naturalWidth) { frames[n] = img; loaded++; dirty = true; }
        pump();
      };
      img.src = `rig/frames/${String(n).padStart(2, "0")}.webp`;
    };
    for (let i = 0; i < 6; i++) pump();
  }

  function nearestFrame(i) {
    const c = Math.round(i);
    for (let d = 0; d <= LAST; d++) {
      if (frames[c - d]) return frames[c - d];
      if (frames[c + d]) return frames[c + d];
    }
    return null;
  }

  function frameAt(sec) {
    let i = 0;
    while (i < LAST && SECS[i + 1] <= sec) i++;
    if (i === LAST) return LAST;
    return i + (sec - SECS[i]) / (SECS[i + 1] - SECS[i]);
  }

  /* ---------- drawing ---------- */
  const lerp = (a, b, t) => a + (b - a) * Math.min(1, Math.max(0, t));
  const narrow = () => stage.clientWidth <= 640;
  const CROP_W = 0.62;

  /* On phones the stage shows a square-ish crop that follows the rig. */
  function cropX() {
    if (!narrow()) return 0;
    if (mode === "rig") {
      const sec = SECS[Math.round(pos)];
      return lerp(0.18, 0.38, (sec - 4) / 1.3);
    }
    return 0.38; // past the title the clips burn in on the left
  }

  function placeHotspots(x0) {
    const w = narrow() ? CROP_W : 1;
    for (const h of hots) {
      const fx = +h.dataset.x, fw = +h.dataset.w;
      h.style.left = `${((fx - x0) / w) * 100}%`;
      h.style.width = `${(fw / w) * 100}%`;
    }
  }

  function hotspotsOn() {
    if (mode === "rig") return pos > LAST - 1 && !auto && rush === null;
    return current < 2 && videos[current].paused;
  }

  function draw(now) {
    let src, sw, sh;
    if (mode === "rig") {
      src = nearestFrame(pos);
      if (!src) return;
      sw = src.naturalWidth; sh = src.naturalHeight;
    } else {
      src = videos[current];
      if (src.readyState < 2 || src.seeking) return;
      sw = src.videoWidth; sh = src.videoHeight;
    }
    if (pendingFade) {
      snap.width = canvas.width; snap.height = canvas.height;
      snap.getContext("2d").drawImage(canvas, 0, 0);
      fadeStart = now;
      pendingFade = false;
    }
    const x0 = cropX();
    const w = narrow() ? CROP_W : 1;
    ctx.imageSmoothingQuality = "high";
    ctx.globalAlpha = 1;
    ctx.drawImage(src, x0 * sw, 0, w * sw, sh, 0, 0, canvas.width, canvas.height);
    if (x0 > 0 && x0 < 0.315) {
      /* the crop would slice the credit line in half */
      ctx.fillStyle = "#0c1017";
      ctx.fillRect(0, canvas.height * 0.93, ((0.315 - x0) / w) * canvas.width, canvas.height * 0.07);
    }
    if (x0 > 0 && mode === "clip") {
      /* same for the last letters of the clip's burned-in title */
      const edge = ctx.createLinearGradient(0, 0, canvas.width * 0.08, 0);
      edge.addColorStop(0.3, "#0c1017");
      edge.addColorStop(1, "rgba(12, 16, 23, 0)");
      ctx.fillStyle = edge;
      ctx.fillRect(0, 0, canvas.width * 0.08, canvas.height);
    }
    const a = fadeStart ? 1 - (now - fadeStart) / FADE_MS : 0;
    if (a > 0) {
      ctx.globalAlpha = a;
      ctx.drawImage(snap, 0, 0);
      ctx.globalAlpha = 1;
      dirty = true;
    } else {
      fadeStart = 0;
    }
    placeHotspots(x0);
    stage.classList.toggle("hot-on", hotspotsOn());
    stage.classList.toggle("lead-whole", mode === "rig" && pos < 48);
    stage.classList.toggle("lead-layers", mode === "rig" && hotspotsOn());
  }

  function resize() {
    const r = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(stage.clientWidth * r);
    canvas.height = Math.round(stage.clientHeight * r);
    dirty = true;
  }

  /* ---------- the loop, only while on screen ---------- */
  function tick(now) {
    raf = visible ? requestAnimationFrame(tick) : 0;

    if (wantIntro && loaded >= 24) {
      wantIntro = false;
      auto = { t0: now };
    }
    if (auto) {
      const sec = (now - auto.t0) / 1000;
      pos = target = frameAt(sec);
      dirty = true;
      if (sec >= DURATION) {
        auto = null;
        pos = target = LAST;
        if (tour) startClip(0, true);
        else showHint();
      }
    } else if (mode === "rig" && rush !== null) {
      pos = Math.min(LAST, pos + 1.6);
      target = pos;
      dirty = true;
      if (pos >= LAST) {
        const i = rush;
        rush = null;
        startClip(i, i === 0);
      }
    } else if (mode === "rig" && Math.abs(target - pos) > 0.01) {
      pos += (target - pos) * 0.28;
      dirty = true;
    }

    if (mode === "clip") {
      const v = videos[current];
      const hold = LAYERS[current].hold;
      if (!v.paused) {
        dirty = true;
        if (next === null && v.currentTime >= hold) {
          v.pause();
          if (tour) endTour();
        }
      }
      chips[current].style.setProperty("--p", Math.min(1, v.currentTime / hold));
    }

    if (mode === "rig" && document.activeElement !== slider || dragging) slider.value = pos;
    if (dirty || pendingFade) {
      dirty = false;
      draw(now);
    }
  }

  /* ---------- clips ---------- */
  function onEnded() {
    if (next === null) return;
    const i = next;
    next = null;
    startClip(i, true);
  }

  function setActive(i) {
    chips.forEach((c, n) => {
      c.setAttribute("aria-pressed", String(n === i));
      if (n !== i) c.style.setProperty("--p", 0);
    });
    panels.forEach((p) => { p.hidden = p.dataset.layer !== (i < 0 ? "rig" : String(i)); });
  }

  function stopVideos() {
    for (const v of videos) if (!v.paused) v.pause();
  }

  function startClip(i, seamless) {
    stopVideos();
    mode = "clip";
    current = i;
    next = tour && i < LAYERS.length - 1 ? i + 1 : null;
    pendingFade = !seamless;
    setActive(i);
    const v = videos[i];
    v.muted = !sound;
    v.preload = "auto";
    if (calm && !tour) {
      v.currentTime = LAYERS[i].hold;
      dirty = true;
      return;
    }
    v.currentTime = 0;
    const p = v.play();
    if (p) p.catch(() => { v.currentTime = LAYERS[i].hold; dirty = true; });
    dirty = true;
  }

  function selectLayer(i) {
    cancelMotion();
    hideHint();
    if (mode === "rig") {
      if (pos >= LAST - 0.5 || calm) {
        pos = target = LAST;
        startClip(i, i === 0);
      } else {
        rush = i;
        setActive(i);
      }
      return;
    }
    const v = videos[current];
    if (i === current + 1 && v.currentTime >= LAYERS[current].hold - 0.1 && !calm) {
      /* already at this clip's hold: run its tail into the next one */
      next = i;
      setActive(i);
      v.play();
      return;
    }
    startClip(i, false);
  }

  function backToRig(frame) {
    stopVideos();
    next = null;
    mode = "rig";
    pendingFade = true;
    pos = target = frame;
    current = -1;
    setActive(-1);
    dirty = true;
  }

  /* ---------- tour ---------- */
  function setTour(on) {
    tour = on;
    tourBtn.textContent = on ? "Stop" : "Play it all";
  }

  function endTour() {
    setTour(false);
  }

  function startTour() {
    cancelMotion();
    hideHint();
    setTour(true);
    if (mode === "clip") backToRig(0);
    else if (pos > 0) pendingFade = true;
    setActive(-1);
    pos = target = 0;
    auto = { t0: performance.now() };
    dirty = true;
  }

  function stopTour() {
    setTour(false);
    next = null;
    auto = null;
    if (mode === "clip" && videos[current].currentTime >= LAYERS[current].hold) videos[current].pause();
  }

  /* Anything the visitor does takes over from anything playing by itself. */
  function cancelMotion() {
    wantIntro = false;
    auto = null;
    rush = null;
    if (tour) setTour(false);
    if (mode === "clip") next = null;
  }

  /* ---------- hint ---------- */
  let hintUsed = false;
  function showHint() { if (!hintUsed) hint.classList.add("on"); }
  function hideHint() { hintUsed = true; hint.classList.remove("on"); }

  /* ---------- drag ---------- */
  let dragging = false;
  let down = null;
  stage.addEventListener("pointerdown", (e) => {
    if (e.button) return;
    down = { x: e.clientX, id: e.pointerId };
  });
  stage.addEventListener("pointermove", (e) => {
    if (!down || e.pointerId !== down.id) return;
    const dx = e.clientX - down.x;
    if (!dragging) {
      if (Math.abs(dx) < 6) return;
      dragging = true;
      cancelMotion();
      hideHint();
      stage.setPointerCapture(e.pointerId);
      stage.classList.add("dragging");
      if (mode === "clip") backToRig(LAYERS[current].pose);
      else setActive(-1);
      down.from = target = pos;
    }
    target = Math.min(LAST, Math.max(0, down.from + (dx / stage.clientWidth) * LAST));
  });
  const release = () => {
    down = null;
    dragging = false;
    stage.classList.remove("dragging");
  };
  stage.addEventListener("pointerup", release);
  stage.addEventListener("pointercancel", release);

  /* ---------- controls ---------- */
  slider.max = LAST;
  slider.value = pos;
  slider.addEventListener("input", () => {
    cancelMotion();
    hideHint();
    const val = +slider.value;
    if (mode === "clip") backToRig(LAYERS[current].pose);
    else setActive(-1);
    target = val;
    if (calm) pos = val;
  });
  chips.forEach((c, i) => c.addEventListener("click", () => selectLayer(i)));
  hots.forEach((h) => h.addEventListener("click", () => selectLayer(+h.dataset.layer)));
  tourBtn.addEventListener("click", () => (tour ? stopTour() : startTour()));
  if (calm) tourBtn.hidden = true;
  soundBtn.addEventListener("click", () => {
    sound = !sound;
    soundBtn.setAttribute("aria-pressed", String(sound));
    soundBtn.textContent = sound ? "Sound on" : "Sound off";
    for (const v of videos) v.muted = !sound;
  });

  /* ---------- start up ---------- */
  new ResizeObserver(resize).observe(stage);
  resize();

  let started = false;
  new IntersectionObserver(([e]) => {
    if (e.isIntersecting && !started) {
      started = true;
      loadFrames();
      for (const v of videos) v.preload = "auto";
    }
  }, { rootMargin: "800px" }).observe(stage);

  new IntersectionObserver(([e]) => {
    visible = e.isIntersecting;
    if (visible) {
      if (resume && mode === "clip") videos[current].play();
      resume = false;
      dirty = true;
      if (!raf) raf = requestAnimationFrame(tick);
    } else if (mode === "clip" && !videos[current].paused) {
      resume = true;
      videos[current].pause();
    }
  }, { threshold: 0.35 }).observe(stage);

  if (calm) showHint();
})();
