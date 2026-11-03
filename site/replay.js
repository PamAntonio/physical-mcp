/* Physical MCP judges-site replay: two machines, one pipeline.
   Fixed scripts built from shared/fixtures/*.json and the recorded risk scores.
   Same brain, different machine. */
"use strict";

/* Paste the demo video link here and it plays in the #video section.
   MP4/file URLs use <video>; youtube.com/watch and youtu.be links embed. */
const VIDEO_URL = "";

const $ = (id) => document.getElementById(id);

/* ---------------- SVG frames ---------------- */

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function timerFrame(t1, pressing) {
  const btn = (id, x, y, w, h, label, rx) => {
    const hot = pressing === id ? ' class="f-btn pressing"' : ' class="f-btn"';
    const cx = x + w / 2, cy = y + h / 2;
    const ring = pressing === id
      ? `<circle class="f-ring" cx="${cx}" cy="${cy}" r="${Math.max(w, h) / 2 + 8}"/>` : "";
    return `<rect${hot} x="${x}" y="${y}" width="${w}" height="${h}" rx="${rx}"/>` +
      `<text class="f-btntext" x="${cx}" y="${cy + 7}" text-anchor="middle" font-size="20">${label}</text>` + ring;
  };
  const row = (label, value, y, bright) =>
    `<text class="f-lcdlab" x="70" y="${y + 30}" font-size="18">${label}</text>` +
    `<rect class="f-lcd" x="110" y="${y}" width="260" height="44" rx="5"/>` +
    `<text class="f-lcdtext${bright ? "" : " dim"}" x="240" y="${y + 31}" text-anchor="middle" font-size="26">${esc(value)}</text>`;
  return `<svg viewBox="0 0 480 640" role="img" aria-label="Timer panel replay frame">
    <rect x="30" y="14" width="420" height="612" rx="24" class="f-body"/>
    ${row("T1", t1, 48, true)}${row("T2", "00:00 00", 104, false)}
    ${row("T3", "00:00 00", 160, false)}${row("T4", "00:00 00", 216, false)}
    ${btn("btn_h", 60, 300, 104, 62, "H", 10)}
    ${btn("btn_m", 188, 300, 104, 62, "M", 10)}
    ${btn("btn_s", 316, 300, 104, 62, "S", 10)}
    ${btn("btn_10m", 60, 380, 104, 62, "10M", 10)}
    ${btn("btn_5m", 188, 380, 104, 62, "5M", 10)}
    ${btn("btn_10s", 316, 380, 104, 62, "10S", 10)}
    ${btn("btn_reset", 60, 462, 150, 66, "RESET", 33)}
    ${btn("btn_stsp", 232, 462, 188, 66, "ST/SP", 33)}
    <text class="f-tag" x="60" y="576" font-size="15">CAM-0 \u00B7 REPLAY \u00B7 TIMER-01</text>
  </svg>`;
}

function incubatorFrame(screen, pressing) {
  const btn = (id, x, y, w, h, label, start) => {
    const hot = pressing === id ? ' class="f-btn pressing"' : ' class="f-btn"';
    const cx = x + w / 2, cy = y + h / 2;
    const ring = pressing === id
      ? `<circle class="f-ring" cx="${cx}" cy="${cy}" r="${Math.max(w, h) / 2 + 8}"/>` : "";
    const labelSvg = label === "up"
      ? `<path d="M${cx} ${cy - 9} L${cx + 10} ${cy + 8} L${cx - 10} ${cy + 8} Z" fill="#e9eff8"/>`
      : label === "down"
        ? `<path d="M${cx} ${cy + 9} L${cx + 10} ${cy - 8} L${cx - 10} ${cy - 8} Z" fill="#e9eff8"/>`
        : `<text class="f-btntext" x="${cx}" y="${cy + 7}" text-anchor="middle" font-size="20">${label}</text>`;
    return `<rect${hot} x="${x}" y="${y}" width="${w}" height="${h}" rx="8"/>` + labelSvg + ring;
  };
  return `<svg viewBox="0 0 640 400" role="img" aria-label="Incubator panel replay frame">
    <rect x="20" y="14" width="600" height="372" rx="20" class="f-body"/>
    <text class="f-lcdlab" x="60" y="58" font-size="15" letter-spacing="4">INCUBATOR</text>
    <rect class="f-lcd" x="60" y="72" width="360" height="80" rx="6"/>
    <text class="f-lcdtext" x="240" y="124" text-anchor="middle" font-size="30">${esc(screen)}</text>
    <rect x="448" y="72" width="132" height="254" rx="8" class="f-lcd"/>
    ${btn("btn_set", 60, 178, 78, 52, "SET")}
    ${btn("btn_up", 150, 178, 60, 52, "up")}
    ${btn("btn_down", 222, 178, 60, 52, "down")}
    ${btn("btn_enter", 294, 178, 126, 52, "ENTER")}
    ${btn("btn_timer", 60, 246, 100, 52, "TIMER")}
    ${btn("btn_start", 294, 246, 126, 52, "START", true)}
    <text class="f-tag" x="60" y="362" font-size="15">CAM-0 \u00B7 REPLAY \u00B7 INCUBATOR-01 (DEMO)</text>
  </svg>`;
}

/* ---------------- machine scripts ----------------
   Each event: { t, run(ctx) }. Gates pause the clock until the human acts:
   { t, gate: "pause"|"approval" }. Post-gate continuations are scheduled
   dynamically so the clock never jumps. */

const SCORER = "meta-models/muse-spark-open-1026";

const MACHINES = {
  timer: {
    request: "Set T1 to 2 hours and start it",
    steps: [
      { id: "s1", label: "Add an hour to T1 (1 h)", sub: "press H" },
      { id: "s2", label: "Add an hour to T1 (2 h)", sub: "press H" },
      { id: "s3", label: "Start T1", sub: "press ST/SP \u00B7 needs approval" },
    ],
    approval: {
      intro: "Step 3 wants to press a button that turns something on. Nothing moves until you approve.",
      action: "press \u00D71 (1 s hold)",
      target: "ST/SP",
      tier: "medium",
      reason: "Starts T1 countdown from 2 hours as requested.",
      scorer: SCORER,
      screen: "screen_t1: \u201802:00 00\u2019 (high)",
    },
    pauseText: "The check after the first H came back low: three reads disagreed. The backend pauses here instead of pressing blind. Resume reads the screen again first.",
    frame: (s) => timerFrame(s.screen, s.pressing),
    init: { screen: "00:00 00", pressing: null },
    build(ctx) {
      const E = (t, run) => ({ t, run });
      return [
        E(0.0, () => { ctx.status("queued"); ctx.log("Job submitted."); }),
        E(0.8, () => { ctx.status("planning"); ctx.log("Planning from the live screen\u2026"); }),
        E(1.6, () => { ctx.screen("screen_t1 reads \u201800:00 00\u2019", "high"); ctx.log("Planned from screen_t1 reads \u201800:00 00\u2019 (high)."); }),
        E(2.4, () => { ctx.plan(); ctx.status("running"); ctx.log("Plan ready: H, H, ST/SP \u2014 step 3 needs approval."); }),
        E(3.0, () => { ctx.step("s1", "running"); ctx.press("btn_h"); ctx.log("Step 1: press H."); }),
        E(3.8, () => { ctx.press(null); ctx.log("Verifying\u2026"); }),
        E(4.4, () => { ctx.screen("screen_t1 reads \u201801:08 00\u2019", "low"); ctx.log("Three reads disagreed (\u201801:08 00\u2019, low) \u2014 pausing, nothing pressed blind."); }),
        { t: 4.4, gate: "pause" },
        E(4.4, () => {
          ctx.screen("screen_t1 reads \u201801:00 00\u2019", "high");
          ctx.log("Re-read is clear: screen_t1 reads \u201801:00 00\u2019 (high).");
          ctx.step("s1", "succeeded");
        }),
        E(5.2, () => { ctx.step("s2", "running"); ctx.press("btn_h"); ctx.log("Step 2: press H."); }),
        E(6.0, () => { ctx.press(null); ctx.log("screen_t1 still reads \u201801:00 00\u2019 \u2014 the press didn\u2019t register."); }),
        E(6.6, () => { ctx.press("btn_h"); ctx.log("Retry: press H again."); }),
        E(7.4, () => {
          ctx.press(null); ctx.screen("screen_t1 reads \u201802:00 00\u2019", "high");
          ctx.log("screen_t1 reads \u201802:00 00\u2019 (high)."); ctx.step("s2", "succeeded");
        }),
        E(8.0, () => { ctx.log(`Risk scored: medium \u2014 ${ctx.machine.approval.reason} (${SCORER}).`); }),
        { t: 8.0, gate: "approval" },
        // scheduled after the human approves:
        // +0.2 press ST/SP, +1.2 verify + done
      ];
    },
    afterApprove: [
      { dt: 0.2, run(ctx) { ctx.step("s3", "running"); ctx.press("btn_stsp"); ctx.log("Step 3: press ST/SP."); } },
      { dt: 1.2, run(ctx) {
        ctx.press(null); ctx.screen("screen_t1 reads \u201801:59 58\u2019", "high");
        ctx.step("s3", "succeeded"); ctx.status("succeeded");
        ctx.log("Job done. T1 is counting down from 2 hours.");
      } },
    ],
  },

  incubator: {
    request: "Set the incubator to 37 \u00B0C and run it for 2 hours",
    steps: [
      { id: "s1", label: "Open the temperature set-point", sub: "press SET" },
      { id: "s2", label: "Raise the set-point to 37.0", sub: "press \u25B2 \u00D712" },
      { id: "s3", label: "Confirm the new set-point", sub: "press ENTER" },
      { id: "s4", label: "Open the timer", sub: "press TIMER" },
      { id: "s5", label: "Set the timer to 2 hours", sub: "press \u25B2 \u00D72" },
      { id: "s6", label: "Start the run", sub: "press START \u00B7 needs approval" },
    ],
    approval: {
      intro: "Step 6 wants to press a button that turns something on. Nothing moves until you approve.",
      action: "press \u00D71",
      target: "START",
      tier: "medium",
      reason: "Starts incubator at 37 \u00B0C for 2 hours as requested.",
      scorer: SCORER,
      screen: "screen_main: \u2018TIMER 02:00\u2019",
    },
    pauseText: "",
    frame: (s) => incubatorFrame(s.screen, s.pressing),
    init: { screen: "TEMP 25.0", pressing: null },
    build(ctx) {
      const E = (t, run) => ({ t, run });
      return [
        E(0.0, () => { ctx.status("queued"); ctx.log("Job submitted."); }),
        E(0.8, () => { ctx.status("planning"); ctx.log("Planning from the live screen\u2026"); }),
        E(1.8, () => { ctx.plan(); ctx.status("running"); ctx.log("Plan ready: SET, \u25B2\u00D712, ENTER, TIMER, \u25B2\u00D72, START \u2014 step 6 needs approval."); }),
        E(2.4, () => { ctx.step("s1", "running"); ctx.press("btn_set"); ctx.log("Step 1: press SET."); }),
        E(3.0, () => { ctx.press(null); ctx.screen("SET TEMP 25.0"); ctx.log("Screen reads \u2018SET TEMP 25.0\u2019."); ctx.step("s1", "succeeded"); }),
        E(3.4, () => { ctx.step("s2", "running"); ctx.press("btn_up"); ctx.log("Step 2: press \u25B2 \u00D712."); }),
        E(4.0, () => { ctx.screen("SET TEMP 27.0"); }),
        E(4.8, () => { ctx.screen("SET TEMP 31.0"); }),
        E(5.6, () => { ctx.press(null); ctx.screen("SET TEMP 36.0"); ctx.log("Expected 37.0, read 36.0: one press was missed."); }),
        E(6.2, () => { ctx.press("btn_up"); ctx.log("Retry: press \u25B2 once more."); }),
        E(6.8, () => { ctx.press(null); ctx.screen("SET TEMP 37.0"); ctx.log("Screen reads \u2018SET TEMP 37.0\u2019."); ctx.step("s2", "succeeded"); }),
        E(7.2, () => { ctx.step("s3", "running"); ctx.press("btn_enter"); ctx.log("Step 3: press ENTER."); }),
        E(7.8, () => { ctx.press(null); ctx.screen("TEMP 25.0 > 37.0"); ctx.log("Screen reads \u2018TEMP 25.0 > 37.0\u2019."); ctx.step("s3", "succeeded"); }),
        E(8.2, () => { ctx.step("s4", "running"); ctx.press("btn_timer"); ctx.log("Step 4: press TIMER."); }),
        E(8.8, () => { ctx.press(null); ctx.screen("TIMER 00:00"); ctx.log("Screen reads \u2018TIMER 00:00\u2019."); ctx.step("s4", "succeeded"); }),
        E(9.2, () => { ctx.step("s5", "running"); ctx.press("btn_up"); ctx.log("Step 5: press \u25B2 \u00D72."); }),
        E(10.0, () => { ctx.press(null); ctx.screen("TIMER 02:00"); ctx.log("Screen reads \u2018TIMER 02:00\u2019."); ctx.step("s5", "succeeded"); }),
        E(10.6, () => { ctx.log(`Risk scored: medium \u2014 ${ctx.machine.approval.reason} (${SCORER}).`); }),
        { t: 10.6, gate: "approval" },
      ];
    },
    afterApprove: [
      { dt: 0.2, run(ctx) { ctx.step("s6", "running"); ctx.press("btn_start"); ctx.log("Step 6: press START."); } },
      { dt: 1.2, run(ctx) {
        ctx.press(null); ctx.screen("RUN 37.0 02:00");
        ctx.step("s6", "succeeded"); ctx.status("succeeded");
        ctx.log("Job done. Incubator is running at 37.0 for 2 hours.");
      } },
    ],
  },
};

/* ---------------- engine ---------------- */

const engine = {
  key: "timer",
  events: [],
  idx: 0,
  clock: 0,
  playing: false,
  speed: 1,
  waiting: null, // null | "pause" | "approval"
  approved: false,
  finished: false,
  frame: { screen: "", pressing: null },
  timerId: null,

  get machine() { return MACHINES[this.key]; },

  ctx() {
    const self = this;
    return {
      machine: self.machine,
      status: (s) => self.setStatus(s),
      log: (text, tone) => self.addLog(text, tone),
      screen: (text, conf) => self.setScreen(text, conf),
      press: (id) => self.setPress(id),
      step: (id, st) => self.setStep(id, st),
      plan: () => self.renderSteps("pending"),
    };
  },

  reset(key) {
    const prev = this.key;
    if (key === "real" && prev !== "real") {
      this.speed = 5;
      $("rp-speed").innerHTML = "5&times;";
    }
    if (key !== "real" && prev === "real") {
      this.speed = 1;
      $("rp-speed").innerHTML = "1&times;";
    }
    if (key === "real") {
      realReset();
      return;
    }
    this.stopTick();
    this.key = key;
    this.clock = 0;
    this.idx = 0;
    this.playing = false;
    this.waiting = null;
    this.approved = false;
    this.finished = false;
    this.frame = { ...this.machine.init };
    this.events = this.machine.build(this.ctx());
    $("rp-request").textContent = this.machine.request;
    $("rp-status").textContent = "queued";
    $("rp-status").dataset.s = "queued";
    $("rp-clock").textContent = "0.0 s";
    $("rp-log").innerHTML = "";
    $("rp-steps").innerHTML = "";
    $("rp-screen").innerHTML = "&hellip;";
    $("rp-approval").hidden = true;
    $("rp-pause").hidden = true;
    $("ap-note").textContent = "";
    $("rr-timeline").hidden = true;
    $("rp-framecap").hidden = true;
    $("rr-models").hidden = true;
    this.holdReset();
    this.draw();
    this.playLabel();
    document.querySelectorAll(".machine-tabs button").forEach((b) =>
      b.setAttribute("aria-selected", String(b.dataset.machine === key)));
  },

  play() {
    if (this.key === "real") {
      realPlay();
      return;
    }
    if (this.waiting || this.finished) return;
    if (this.idx >= this.events.length) return;
    this.playing = true;
    this.playLabel();
    this.stopTick();
    let last = performance.now();
    this.timerId = setInterval(() => {
      const now = performance.now();
      const dt = ((now - last) / 1000) * this.speed;
      last = now;
      this.clock += dt;
      this.pump(false);
      $("rp-clock").textContent = this.clock.toFixed(1) + " s";
      if (this.idx >= this.events.length && !this.waiting) {
        this.playing = false;
        this.stopTick();
        this.playLabel();
      }
    }, 100);
  },

  pause() {
    this.playing = false;
    this.stopTick();
    this.playLabel();
  },

  toggle() {
    if (this.playing) this.pause();
    else this.play();
  },

  stopTick() {
    if (this.timerId) { clearInterval(this.timerId); this.timerId = null; }
  },

  playLabel() {
    $("rp-play").textContent = this.playing ? "Pause" : (this.idx === 0 && this.clock === 0 ? "Play" : "Resume");
  },

  /* Fire every event up to the clock. On a gate, stop and wait for the
     human — unless skipping, in which case auto-resolve pause gates. */
  pump(skipping) {
    while (this.idx < this.events.length && this.events[this.idx].t <= this.clock) {
      const ev = this.events[this.idx++];
      if (ev.gate) {
        if (ev.gate === "pause") {
          if (skipping) {
            this.addLog("You resumed, reading the screen again.", "you");
            continue;
          }
          this.onPauseGate();
          return;
        }
        if (ev.gate === "approval") {
          this.onApprovalGate();
          return;
        }
      } else {
        ev.run(this.ctx());
      }
    }
  },

  skipToApproval() {
    if (this.key === "real") {
      realSkip();
      return;
    }
    if (this.finished || this.approved) return;
    const target = this.events.find((e, i) => e.gate === "approval" && i >= this.idx);
    if (!target) return;
    this.pause();
    $("rp-approval").hidden = true;
    this.clock = target.t;
    this.pump(true); // fires everything before, auto-resolves the pause gate
    this.pump(false); // lands on the approval gate
    $("rp-clock").textContent = this.clock.toFixed(1) + " s";
    this.playLabel();
  },

  /* ----- gates ----- */

  onPauseGate() {
    this.pause();
    this.waiting = "pause";
    this.setStatus("paused");
    $("pause-text").textContent = this.machine.pauseText;
    $("rp-pause").hidden = false;
  },

  resume() {
    if (this.waiting !== "pause") return;
    $("rp-pause").hidden = true;
    this.waiting = null;
    this.addLog("You resumed, reading the screen again.", "you");
    this.setStatus("running");
    this.play();
  },

  onApprovalGate() {
    this.pause();
    this.waiting = "approval";
    this.setStatus("awaiting_approval");
    const last = this.machine.steps[this.machine.steps.length - 1];
    this.setStep(last.id, "awaiting_approval");
    const a = this.machine.approval;
    $("ap-intro").textContent = a.intro;
    $("ap-action").textContent = a.action;
    $("ap-target").textContent = a.target;
    const tier = $("ap-tier");
    tier.textContent = a.tier;
    tier.className = "tier " + a.tier;
    $("ap-reason").textContent = a.reason;
    $("ap-scorer").textContent = a.scorer;
    $("ap-screen").textContent = a.screen;
    $("rp-approval").hidden = false;
  },

  approve() {
    if (this.key === "real") {
      realApprove();
      return;
    }
    if (this.waiting !== "approval") return;
    $("rp-approval").hidden = true;
    this.waiting = null;
    this.approved = true;
    const target = this.machine.approval.target;
    this.addLog(`You approved ${target} (hold, 1.5s).`, "you");
    this.setStatus("running");
    // schedule the finger + verify right after the decision
    const cont = this.machine.afterApprove.map((s) => ({ t: this.clock + s.dt, run: s.run }));
    this.events = this.events.concat(cont).sort((a, b) => a.t - b.t);
    // keep the consumed prefix consumed: every event sorts after clock except
    // ones already fired, so re-point idx at the first event past the clock
    this.idx = this.events.findIndex((e) => e.t > this.clock);
    if (this.idx < 0) this.idx = this.events.length;
    this.play();
    this.finished = false;
  },

  reject() {
    if (this.key === "real") {
      realReject();
      return;
    }
    if (this.waiting !== "approval") return;
    const target = this.machine.approval.target;
    this.addLog(`You rejected ${target}, job paused.`, "you");
    this.setStatus("paused");
    $("ap-note").textContent = "Rejected \u2014 nothing was pressed. Approve to carry on.";
  },

  /* ----- rendering ----- */

  setStatus(s) {
    const el = $("rp-status");
    el.textContent = s.replace(/_/g, " ");
    el.dataset.s = s;
    if (s === "succeeded" || s === "failed" || s === "stopped") this.finished = true;
  },

  addLog(text, tone) {
    const li = document.createElement("li");
    if (tone) li.className = tone;
    const t = document.createElement("span");
    t.className = "lt";
    t.textContent = this.clock.toFixed(1) + "s";
    li.appendChild(t);
    li.appendChild(document.createTextNode(text));
    const log = $("rp-log");
    log.appendChild(li);
    log.scrollTop = log.scrollHeight;
  },

  setScreen(text, conf) {
    if (this.key === "timer") {
      const m = text.match(/\u2018([^\u2019]*)\u2019/);
      if (m) this.frame.screen = m[1];
    } else {
      this.frame.screen = text;
    }
    let html = esc(text);
    if (conf) html += `<span class="conf ${conf}">${conf}</span>`;
    $("rp-screen").innerHTML = html;
    this.draw();
  },

  setPress(id) {
    this.frame.pressing = id;
    this.draw();
  },

  draw() {
    if (this.key === "real") {
      $("rp-frame").innerHTML = timerFrame(this.frame.screen, this.frame.pressing);
      return;
    }
    $("rp-frame").innerHTML = this.machine.frame(this.frame);
  },

  renderSteps(def) {
    const box = $("rp-steps");
    box.innerHTML = "";
    this.machine.steps.forEach((s, i) => {
      const div = document.createElement("div");
      div.className = "rp-step";
      div.dataset.st = def;
      div.id = "step-" + s.id;
      div.innerHTML = `<span class="n">${i + 1}</span><span>${esc(s.label)} <span class="fine">${esc(s.sub)}</span></span><span class="st">${def}</span>`;
      box.appendChild(div);
    });
  },

  setStep(id, st) {
    const el = $("step-" + id);
    if (!el) return;
    el.dataset.st = st;
    el.querySelector(".st").textContent = st.replace(/_/g, " ");
  },

  /* ----- hold to approve ----- */

  holdT0: 0,
  holdRaf: 0,
  holdMs: 1500,

  holdStart(ev) {
    if (this.waiting !== "approval") return;
    if (ev) ev.preventDefault();
    if (this.holdRaf) return;
    this.holdT0 = performance.now();
    const fill = $("ap-holdfill");
    const label = $("ap-holdlabel");
    const step = (now) => {
      const p = Math.min(1, (now - this.holdT0) / this.holdMs);
      fill.style.width = (p * 100).toFixed(1) + "%";
      label.textContent = p >= 1 ? "Approved" : `Hold 1.5 s to approve (${(1.5 * (1 - p)).toFixed(1)}s)`;
      if (p >= 1) {
        this.holdRaf = 0;
        this.approve();
        return;
      }
      this.holdRaf = requestAnimationFrame(step);
    };
    this.holdRaf = requestAnimationFrame(step);
  },

  holdCancel() {
    if (this.holdRaf) {
      cancelAnimationFrame(this.holdRaf);
      this.holdRaf = 0;
      this.holdReset();
    }
  },

  holdReset() {
    $("ap-holdfill").style.width = "0%";
    $("ap-holdlabel").textContent = "Hold 1.5 s to approve";
  },
};

/* ---------------- real recorded run ----------------
   Plays site/data/real-run/job.json + events.json through the same panel.
   Every number shown comes from those files at runtime. */

const REAL_JOB_URL = "data/real-run/job.json";
const REAL_EVENTS_URL = "data/real-run/events.json";

const realCache = {
  loaded: false,
  loading: false,
  failed: false,
  job: null,
  events: [],
  finishElapsed: 0,
  approvalElapsed: -1,
  nextRunningElapsed: -1,
  approvalStep: null,
  maxUsageSec: 1,
  promise: null,
};

function realBtnLabel(id) {
  if (id === "btn_h") return "H";
  if (id === "btn_stsp") return "ST/SP";
  if (typeof id === "string" && id.slice(0, 4) === "btn_") return id.slice(4).toUpperCase();
  return "not recorded";
}

function realFmt1(n) {
  return Number(n).toFixed(1);
}

function realFmtInt(n) {
  return Number(n).toLocaleString("en-US");
}

function loadRealData() {
  if (realCache.loaded) return Promise.resolve();
  if (realCache.loading) return realCache.promise;
  realCache.loading = true;
  realCache.promise = (async () => {
  try {
    const res = await Promise.all([fetch("data/real-run/job.json"), fetch("data/real-run/events.json")]);
    if (!res[0].ok || !res[1].ok) throw new Error("fetch failed");
    const job = await res[0].json();
    const raw = await res[1].json();
    const t0 = new Date(job.created_at).getTime();
    const events = raw.map((e) => {
      const out = {};
      for (const k in e) out[k] = e[k];
      out.elapsed = (new Date(e.ts).getTime() - t0) / 1000;
      return out;
    });
    let finishElapsed = 0;
    for (const e of events) {
      if (e.type === "job.finished") finishElapsed = e.elapsed;
    }
    const gateIdx = events.findIndex((e) => e.type === "job.status" && e.status === "awaiting_approval");
    let approvalElapsed = -1;
    let nextRunningElapsed = -1;
    if (gateIdx >= 0) {
      approvalElapsed = events[gateIdx].elapsed;
      for (let i = gateIdx + 1; i < events.length; i++) {
        const s = events[i].step;
        if (events[i].type === "step.updated" && s && s.status === "running") {
          nextRunningElapsed = events[i].elapsed;
          break;
        }
      }
    }
    const steps = (job.plan && job.plan.steps) || [];
    let approvalStep = null;
    for (const s of steps) {
      if (s.requires_approval) {
        approvalStep = s;
        break;
      }
    }
    let maxUsageSec = 1;
    for (const u of (job.usage || [])) {
      if (u.seconds > maxUsageSec) maxUsageSec = u.seconds;
    }
    realCache.job = job;
    realCache.events = events;
    realCache.finishElapsed = finishElapsed;
    realCache.approvalElapsed = approvalElapsed;
    realCache.nextRunningElapsed = nextRunningElapsed;
    realCache.approvalStep = approvalStep;
    realCache.maxUsageSec = maxUsageSec;
    realCache.loaded = true;
    realCache.failed = false;
  } catch (err) {
    realCache.failed = true;
  } finally {
    realCache.loading = false;
  }
  })();
  return realCache.promise;
}

function realReset() {
  engine.stopTick();
  engine.key = "real";
  engine.clock = 0;
  engine.idx = 0;
  engine.playing = false;
  engine.waiting = null;
  engine.approved = false;
  engine.finished = false;
  engine.frame = { screen: "00:00 00", pressing: null };
  document.querySelectorAll(".machine-tabs button").forEach((b) =>
    b.setAttribute("aria-selected", String(b.dataset.machine === "real")));
  $("rp-request").textContent = "\u2026";
  $("rp-status").textContent = "queued";
  $("rp-status").dataset.s = "queued";
  $("rp-clock").textContent = "0.0 s";
  $("rp-log").innerHTML = "";
  $("rp-steps").innerHTML = "";
  $("rp-screen").innerHTML = "&hellip;";
  $("rp-approval").hidden = true;
  $("rp-pause").hidden = true;
  $("ap-note").textContent = "";
  engine.holdReset();
  $("rp-framecap").hidden = false;
  $("rr-timeline").hidden = true;
  $("rr-models").hidden = true;
  engine.draw();
  engine.playLabel();
  if (realCache.loaded) {
    initRealUI();
  } else if (realCache.failed) {
    showRealError();
  } else {
    $("rp-request").textContent = "Loading the recorded run\u2026";
    loadRealData().then(() => {
      if (engine.key !== "real") return;
      if (realCache.loaded) initRealUI();
      else showRealError();
    });
  }
}

function showRealError() {
  $("rp-request").textContent = "Couldn't load the recorded run";
  const log = $("rp-log");
  log.innerHTML = "";
  const li = document.createElement("li");
  li.textContent = "Couldn't load the recorded run";
  log.appendChild(li);
  $("rr-timeline").hidden = true;
  $("rr-models").hidden = true;
  $("rp-framecap").hidden = true;
}

function initRealUI() {
  const job = realCache.job;
  $("rp-request").textContent = job.request;
  renderRealSteps();
  renderModelCalls();
  setupTimeline();
  $("rr-timeline").hidden = false;
  $("rr-models").hidden = false;
  $("rp-framecap").hidden = false;
  engine.draw();
}

function renderRealSteps() {
  const box = $("rp-steps");
  box.innerHTML = "";
  const steps = realCache.job.plan.steps;
  steps.forEach((s, i) => {
    const div = document.createElement("div");
    div.className = "rp-step";
    div.dataset.st = "pending";
    div.id = "step-" + s.step_id;
    const btn = realBtnLabel(s.action && s.action.control_id);
    const hasCount = s.action && typeof s.action.count === "number";
    const countTxt = hasCount ? "\u00D7" + s.action.count : "not recorded";
    const expect = (s.expect && s.expect.text_contains) || "not recorded";
    div.innerHTML = `<span class="n">${i + 1}</span><span>${esc(s.intent)} <span class="fine">press ${esc(btn)} ${esc(countTxt)} \u00B7 expect \u201C${esc(expect)}\u201D</span></span><span class="st">pending</span>`;
    box.appendChild(div);
  });
}

function renderModelCalls() {
  const usage = realCache.job.usage || [];
  const totalsEl = $("rr-totals");
  const rowsEl = $("rr-rows");
  rowsEl.innerHTML = "";
  const perKind = {};
  let totalTokens = 0;
  for (const u of usage) {
    perKind[u.kind] = (perKind[u.kind] || 0) + 1;
    totalTokens += (u.prompt_tokens || 0) + (u.completion_tokens || 0);
  }
  const kinds = Object.keys(perKind).sort();
  const parts = kinds.map((k) => perKind[k] + " " + k);
  totalsEl.textContent = usage.length + " calls (" + parts.join(", ") + ") \u00B7 " + realFmtInt(totalTokens) + " tokens";
  for (const u of usage) {
    const total = (u.prompt_tokens || 0) + (u.completion_tokens || 0);
    const pct = realCache.maxUsageSec ? (u.seconds / realCache.maxUsageSec) * 100 : 0;
    const div = document.createElement("div");
    div.className = "rr-call";
    const meta = document.createElement("div");
    meta.className = "rr-meta mono";
    meta.textContent = u.kind + " \u00B7 " + u.model + " \u00B7 " + realFmtInt(total) + " tokens \u00B7 " + u.seconds + " s";
    const bar = document.createElement("div");
    bar.className = "rr-bar";
    bar.setAttribute("aria-hidden", "true");
    const fill = document.createElement("div");
    fill.style.width = pct.toFixed(1) + "%";
    bar.appendChild(fill);
    div.appendChild(meta);
    div.appendChild(bar);
    rowsEl.appendChild(div);
  }
}

function setupTimeline() {
  const scrub = $("rr-scrub");
  scrub.min = "0";
  scrub.max = String(realCache.finishElapsed);
  scrub.step = "0.1";
  scrub.value = "0";
  const marks = $("rr-marks");
  marks.innerHTML = "";
  const finish = realCache.finishElapsed;
  const addMark = (elapsed, cls, title) => {
    if (!(finish > 0)) return;
    const d = document.createElement("div");
    d.className = "rr-mark" + (cls ? " " + cls : "");
    d.style.left = ((elapsed / finish) * 100).toFixed(2) + "%";
    d.title = title;
    marks.appendChild(d);
  };
  for (const e of realCache.events) {
    if (e.type === "plan.ready") addMark(e.elapsed, "", "Plan ready");
  }
  for (const e of realCache.events) {
    if (e.note && e.note.kind === "retry") addMark(e.elapsed, "retry", "Retry");
  }
  if (realCache.approvalElapsed >= 0) addMark(realCache.approvalElapsed, "approval", "Approval wait");
  for (const e of realCache.events) {
    if (e.type === "job.finished") addMark(e.elapsed, "finish", "Finish");
  }
}

function realAddLog(elapsed, message, tone) {
  const li = document.createElement("li");
  if (tone) li.className = tone;
  li.textContent = "[" + realFmt1(elapsed) + " s] " + message;
  const log = $("rp-log");
  log.appendChild(li);
  log.scrollTop = log.scrollHeight;
}

function realSetT1(text, conf, controlId) {
  if (text == null) return;
  engine.frame.screen = text;
  let html = esc((controlId || "screen_t1") + " reads \u2018" + text + "\u2019");
  if (conf) html += `<span class="conf ${esc(conf)}">${esc(conf)}</span>`;
  $("rp-screen").innerHTML = html;
  engine.draw();
}

function applyRealEvent(ev) {
  const elapsed = ev.elapsed;
  if (ev.type === "job.status") {
    engine.setStatus(ev.status);
    if (ev.status === "awaiting_approval") return "gate";
    return null;
  }
  if (ev.type === "plan.ready") {
    realAddLog(elapsed, "Plan: " + ev.plan.summary + " (" + ev.plan.planner + ")");
    if (ev.note) {
      realAddLog(elapsed, ev.note.message);
      if (ev.note.read && ev.note.read.text) {
        realSetT1(ev.note.read.text, ev.note.read.confidence, ev.note.read.control_id);
      }
    }
    return null;
  }
  if (ev.type === "step.updated") {
    const st = ev.step;
    engine.setStep(st.step_id, st.status);
    const cid = st.action && st.action.control_id;
    if (st.status === "running") {
      engine.frame.pressing = cid || null;
    } else if (engine.frame.pressing && engine.frame.pressing === cid) {
      engine.frame.pressing = null;
    }
    let t1text = null;
    let t1conf = null;
    let t1ctrl = null;
    if (ev.note && ev.note.read && ev.note.read.text) {
      t1text = ev.note.read.text;
      t1conf = ev.note.read.confidence;
      t1ctrl = ev.note.read.control_id;
    } else if (st.evidence && st.evidence.screen_text) {
      t1text = st.evidence.screen_text;
      t1conf = st.evidence.screen_confidence || null;
      t1ctrl = (st.expect && st.expect.control_id) || "screen_t1";
    }
    if (t1text) realSetT1(t1text, t1conf, t1ctrl);
    else engine.draw();
    if (ev.note) realAddLog(elapsed, ev.note.message);
    return null;
  }
  if (ev.type === "job.finished") {
    realAddLog(elapsed, ev.result.message + " T1 reads " + ev.result.final_screen_text);
    engine.setStatus(ev.result.outcome || "succeeded");
    return "finished";
  }
  return null;
}

function realPlay() {
  if (engine.waiting || engine.finished) return;
  if (!realCache.loaded) return;
  if (engine.idx >= realCache.events.length) return;
  engine.playing = true;
  engine.playLabel();
  engine.stopTick();
  let last = performance.now();
  engine.timerId = setInterval(() => {
    const now = performance.now();
    const dt = ((now - last) / 1000) * engine.speed;
    last = now;
    engine.clock += dt;
    if (engine.clock > realCache.finishElapsed) engine.clock = realCache.finishElapsed;
    pumpReal();
    $("rp-clock").textContent = realFmt1(engine.clock) + " s";
    const scrub = $("rr-scrub");
    if (scrub) scrub.value = String(engine.clock);
    if (engine.idx >= realCache.events.length && !engine.waiting) {
      engine.playing = false;
      engine.stopTick();
      engine.playLabel();
    }
  }, 100);
}

function realPause() {
  engine.playing = false;
  engine.stopTick();
  engine.playLabel();
}

function pumpReal() {
  const events = realCache.events;
  while (engine.idx < events.length && events[engine.idx].elapsed <= engine.clock) {
    const ev = events[engine.idx++];
    const res = applyRealEvent(ev);
    if (res === "gate") {
      onRealApprovalGate();
      return;
    }
  }
}

function onRealApprovalGate() {
  realPause();
  engine.waiting = "approval";
  engine.setStatus("awaiting_approval");
  const step = realCache.approvalStep;
  if (step) engine.setStep(step.step_id, "awaiting_approval");
  if (step) {
    $("ap-intro").textContent = step.approval_reason || "not recorded";
    const hasCount = step.action && typeof step.action.count === "number";
    const holdMs = (step.action && step.action.hold_ms) || 0;
    let actionTxt = hasCount ? "press \u00D7" + step.action.count : "not recorded";
    if (hasCount && holdMs) actionTxt += " (" + (holdMs / 1000) + " s hold)";
    $("ap-action").textContent = actionTxt;
    $("ap-target").textContent = realBtnLabel(step.action && step.action.control_id);
    const tier = $("ap-tier");
    tier.textContent = step.risk_tier || "not recorded";
    tier.className = "tier " + (step.risk_tier || "");
    $("ap-reason").textContent = step.risk_reason || "not recorded";
    $("ap-scorer").textContent = step.risk_scored_by || "not recorded";
    $("ap-screen").textContent = engine.frame.screen;
  }
  $("ap-note").textContent = "";
  $("rp-approval").hidden = false;
}

function realApprove() {
  if (engine.waiting !== "approval") return;
  $("rp-approval").hidden = true;
  engine.waiting = null;
  engine.approved = true;
  const diff = realCache.nextRunningElapsed - realCache.approvalElapsed;
  realAddLog(engine.clock, "In the recorded run, the human approved after " + realFmt1(diff) + " s", "you");
  engine.setStatus("running");
  realPlay();
}

function realReject() {
  if (engine.waiting !== "approval") return;
  realAddLog(engine.clock, "Rejected here. In the recorded run this step was approved. Nothing was pressed.", "you");
  engine.waiting = null;
  engine.finished = true;
  engine.playing = false;
  engine.stopTick();
  engine.setStatus("stopped");
  $("rp-approval").hidden = true;
  engine.playLabel();
}

function realSkip() {
  if (engine.finished || engine.approved) return;
  if (engine.waiting === "approval") return;
  if (!realCache.loaded) return;
  realPause();
  $("rp-approval").hidden = true;
  rebuildUpTo(realCache.approvalElapsed);
}

function rebuildUpTo(T) {
  realPause();
  engine.clock = T;
  engine.idx = 0;
  engine.waiting = null;
  engine.approved = false;
  engine.finished = false;
  engine.frame = { screen: "00:00 00", pressing: null };
  $("rp-log").innerHTML = "";
  renderRealSteps();
  $("rp-screen").innerHTML = "&hellip;";
  $("rp-approval").hidden = true;
  $("ap-note").textContent = "";
  engine.holdReset();
  engine.setStatus("queued");
  engine.finished = false;
  const pastGate = T > realCache.approvalElapsed;
  const events = realCache.events;
  let i = 0;
  for (; i < events.length; i++) {
    const ev = events[i];
    if (ev.elapsed > T) break;
    if (ev.type === "job.status" && ev.status === "awaiting_approval") {
      engine.setStatus(ev.status);
      if (pastGate) {
        engine.approved = true;
        realAddLog(ev.elapsed, "approved (recorded)", "you");
        continue;
      }
      engine.idx = i + 1;
      engine.clock = T;
      $("rp-clock").textContent = realFmt1(T) + " s";
      const scrub = $("rr-scrub");
      if (scrub) scrub.value = String(T);
      engine.draw();
      onRealApprovalGate();
      engine.clock = T;
      $("rp-clock").textContent = realFmt1(T) + " s";
      return;
    }
    applyRealEvent(ev);
  }
  engine.idx = i;
  if (pastGate) engine.approved = true;
  $("rp-clock").textContent = realFmt1(T) + " s";
  const scrub = $("rr-scrub");
  if (scrub) scrub.value = String(T);
  engine.draw();
  engine.playLabel();
}

$("rr-scrub").addEventListener("input", (e) => {
  if (engine.key !== "real" || !realCache.loaded) return;
  const T = parseFloat(e.target.value);
  if (!Number.isFinite(T)) return;
  rebuildUpTo(T);
});

/* ---------------- wire up ---------------- */

document.querySelectorAll(".machine-tabs button").forEach((b) =>
  b.addEventListener("click", () => {
    if (engine.key !== b.dataset.machine) engine.reset(b.dataset.machine);
  }));

$("rp-play").addEventListener("click", () => engine.toggle());
$("rp-restart").addEventListener("click", () => engine.reset(engine.key));
$("rp-skip").addEventListener("click", () => engine.skipToApproval());
$("rp-speed").addEventListener("click", () => {
  if (engine.key === "real") {
    engine.speed = engine.speed === 1 ? 5 : engine.speed === 5 ? 20 : 1;
    $("rp-speed").innerHTML = engine.speed === 1 ? "1&times;" : engine.speed === 5 ? "5&times;" : "20&times;";
    return;
  }
  engine.speed = engine.speed === 1 ? 2 : 1;
  $("rp-speed").innerHTML = engine.speed === 1 ? "1&times;" : "2&times;";
});
$("pause-resume").addEventListener("click", () => engine.resume());
$("ap-reject").addEventListener("click", () => engine.reject());

const hold = $("ap-hold");
hold.addEventListener("pointerdown", (e) => engine.holdStart(e));
hold.addEventListener("pointerup", () => engine.holdCancel());
hold.addEventListener("pointerleave", () => engine.holdCancel());
hold.addEventListener("keydown", (e) => {
  if ((e.key === " " || e.key === "Enter") && !e.repeat) engine.holdStart(e);
});
hold.addEventListener("keyup", () => engine.holdCancel());

/* demo video: file/mp4 -> <video>, youtube -> embed, empty -> placeholder */
(function video() {
  if (!VIDEO_URL) return;
  const shell = $("video-shell");
  let node;
  const yt = VIDEO_URL.match(/(?:youtube\.com\/watch\?v=|youtu\.be\/)([\w-]{6,})/);
  if (yt) {
    node = document.createElement("iframe");
    node.src = `https://www.youtube.com/embed/${yt[1]}`;
    node.title = "Demo video";
    node.allow = "accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture";
    node.allowFullscreen = true;
    node.style.cssText = "width:100%;aspect-ratio:16/9;border:0;display:block";
  } else {
    node = document.createElement("video");
    node.src = VIDEO_URL;
    node.controls = true;
    node.playsInline = true;
    node.preload = "metadata";
  }
  shell.innerHTML = "";
  shell.appendChild(node);
})();

engine.reset("timer");
