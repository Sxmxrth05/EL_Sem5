// Browser UI for the decision service. All scoring happens on the server; this file only
// collects inputs, calls the API and draws results.
"use strict";

const API = "";  // same origin as the service
const TIER_COLOR = { "0": "var(--t0)", W: "var(--tW)", "1": "var(--t1)", "2": "var(--t2)", "3": "var(--t3)" };
const BASELINE_LABEL = {
  pin_only: "PIN only",
  always_strict: "OTP on every payment",
  amount_rule: "OTP above Rs 10,000",
  fri_only: "FRI-style rule",
};

// The five golden cases from the team guide (B2). ratio = amount / median.
const PRESETS = {
  "Chai stall": { amount: 288, median: 400, network: "mobile", fri: 0 },
  "Rent deposit": { amount: 45000, median: 250, network: "home", fri: 0, new_payee: true },
  "SIM-swap fraud": { amount: 45000, median: 250, network: "public", fri: 0,
    new_device: true, new_payee: true, sim_change: true },
  "Screen-share scam": { amount: 45000, median: 250, network: "home", fri: 0,
    new_payee: true, remote: true },
  "Stolen phone": { amount: 45000, median: 250, network: "home", fri: 0,
    new_payee: true, velocity: true, far_location: true },
};
const FLAGS = ["new_device", "new_payee", "velocity", "sim_change", "remote", "far_location"];

const $ = (sel) => document.querySelector(sel);
let policy = null;

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const res = await fetch(API + path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${res.status}`);
  return data;
}

function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "style") n.style.cssText = v; else if (k === "class") n.className = v; else n.setAttribute(k, v);
  }
  for (const c of children) n.append(c);
  return n;
}
const tag = (tier) => el("span", { class: "tag", style: `background:${TIER_COLOR[tier]}` }, tier);

// ------------------------------------------------------------------ payment form

function readPayment() {
  const f = $("#payment-form");
  const p = {
    amount: Number(f.amount.value), median: Number(f.median.value),
    network: f.network.value, fri: Number(f.fri.value),
  };
  for (const k of FLAGS) p[k] = f[k].checked;
  return p;
}

function fillPayment(preset) {
  const f = $("#payment-form");
  f.amount.value = preset.amount; f.median.value = preset.median;
  f.network.value = preset.network; f.fri.value = String(preset.fri);
  for (const k of FLAGS) f[k].checked = Boolean(preset[k]);
}

function drawMeter(score, thresholds) {
  const m = $("#meter");
  m.replaceChildren();
  const cut = [0, ...thresholds.map((t) => Math.max(0, Math.min(100, t))), 100];
  const tiers = ["0", "1", "2", "3"];
  for (let i = 0; i < 4; i++) {
    const w = cut[i + 1] - cut[i];
    if (w <= 0) continue;
    m.append(el("div", { class: "band", style: `width:${w}%;background:${TIER_COLOR[tiers[i]]}` },
      el("span", {}, `${tiers[i]} from ${cut[i]}`)));
  }
  m.append(el("div", { class: "marker", style: `left:calc(${Math.min(score, 100)}% - 1px)` }));
}

async function scoreCurrent() {
  $("#form-error").textContent = "";
  const btn = $("#payment-form button[type=submit]");
  btn.disabled = true;
  try {
    const r = await api("/compare", readPayment());
    const e = r.engine;
    $("#result").hidden = false;
    $("#score").textContent = e.score;
    const pill = $("#tier-pill");
    pill.textContent = `Tier ${e.tier}: ${e.tier_name}`;
    pill.style.background = TIER_COLOR[e.tier];
    $("#raw-total").textContent = e.raw_total > e.score
      ? `raw total ${e.raw_total}, capped at ${e.score}` : `thresholds ${e.thresholds.join(" / ")}`;
    drawMeter(e.score, e.thresholds);

    const bd = $("#breakdown tbody");
    bd.replaceChildren();
    if (!e.breakdown.length) bd.append(el("tr", {}, el("td", { class: "muted" }, "No risk signals on")));
    for (const b of e.breakdown) {
      bd.append(el("tr", {}, el("td", {}, b.signal), el("td", { class: "num" }, `+${b.points}`)));
    }
    bd.append(el("tr", {}, el("td", {}, el("b", {}, "Total (capped)")), el("td", { class: "num" }, el("b", {}, String(e.score)))));

    const ct = $("#compare tbody");
    ct.replaceChildren(el("tr", {}, el("td", {}, el("b", {}, "Our engine")), el("td", {}, tag(e.tier)), el("td", { class: "muted" }, e.tier_name)));
    for (const [name, t] of Object.entries(r.baselines)) {
      ct.append(el("tr", {}, el("td", {}, BASELINE_LABEL[name] || name), el("td", {}, tag(t)), el("td", { class: "muted" }, r.tier_names[t])));
    }
    loadLog();
  } catch (err) {
    $("#form-error").textContent = err.message;
  } finally {
    btn.disabled = false;
  }
}

// ------------------------------------------------------------------ strictness

function showPolicy(p) {
  policy = p;
  $("#policy-info").textContent =
    `Effective thresholds ${p.thresholds.join(" / ")} (base ${p.base_thresholds.join(" / ")}, shift ${p.shift}) · version ${p.policy_version}`;
  $("#status").textContent = `service ok · policy ${p.policy_version}`;
}

async function setStrictness(strictness) {
  $("#strictness-out").textContent = strictness;
  try {
    showPolicy(await api("/policy", { shift: -strictness }));
    if (!$("#result").hidden) scoreCurrent();
  } catch (err) {
    $("#policy-info").textContent = err.message;
  }
}

// ------------------------------------------------------------------ simulation chart

function drawChart(r) {
  const W = 760, H = 380, P = { l: 56, r: 20, t: 16, b: 46 };
  const all = [...r.curve, ...r.baselines.filter((b) => b.friction <= 1000)];
  const maxX = Math.max(50, ...all.map((d) => d.friction)) * 1.05;
  const x = (v) => P.l + (v / maxX) * (W - P.l - P.r);
  const y = (v) => H - P.b - (v / 100) * (H - P.t - P.b);
  const ns = "http://www.w3.org/2000/svg";
  const s = (name, attrs, text) => {
    const n = document.createElementNS(ns, name);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (text !== undefined) n.textContent = text;
    return n;
  };
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "fraud stopped versus friction" });
  for (let i = 0; i <= 5; i++) {
    const yv = i * 20, xv = (maxX / 5) * i;
    svg.append(s("line", { class: "axis", x1: P.l, x2: W - P.r, y1: y(yv), y2: y(yv) }));
    svg.append(s("text", { x: P.l - 8, y: y(yv) + 4, "text-anchor": "end" }, `${yv}%`));
    svg.append(s("text", { x: x(xv), y: H - P.b + 16, "text-anchor": "middle" }, Math.round(xv)));
  }
  svg.append(s("text", { x: (W + P.l) / 2, y: H - 8, "text-anchor": "middle" }, "friction per 1,000 genuine payments"));
  svg.append(s("text", { x: 14, y: H / 2, transform: `rotate(-90 14 ${H / 2})`, "text-anchor": "middle" }, "fraud value stopped"));
  const pts = [...r.curve].sort((a, b) => a.friction - b.friction);
  svg.append(s("polyline", { class: "curve", points: pts.map((d) => `${x(d.friction)},${y(d.prevented_pct)}`).join(" ") }));
  for (const d of r.curve) {
    const c = s("circle", { class: "pt", cx: x(d.friction), cy: y(d.prevented_pct), r: 2.6 });
    c.append(s("title", {}, `shift ${d.shift}: friction ${d.friction.toFixed(0)}, stopped ${d.prevented_pct.toFixed(1)}%`));
    svg.append(c);
  }
  for (const b of r.baselines) {
    if (b.friction > maxX) continue;
    const g = s("g", {});
    g.append(s("rect", { class: "base", x: x(b.friction) - 4, y: y(b.prevented_pct) - 4, width: 8, height: 8 }));
    g.append(s("text", { x: x(b.friction) + 8, y: y(b.prevented_pct) - 6 }, BASELINE_LABEL[b.name] || b.name));
    svg.append(g);
  }
  const c = r.current;
  svg.append(s("circle", { class: "cur", cx: x(c.friction), cy: y(c.prevented_pct), r: 6 }));
  svg.append(s("text", { x: x(c.friction) + 9, y: y(c.prevented_pct) + 16 }, `current (shift ${c.shift})`));
  $("#chart").replaceChildren(svg);
  const strict = r.baselines.find((b) => b.name === "always_strict");
  $("#sim-info").textContent =
    `${r.n_legit.toLocaleString()} genuine, ${r.n_attacks} attacks, seed ${r.seed}${r.evasive ? ", evasive" : ""} · ` +
    `current policy stops ${c.prevented_pct.toFixed(1)}% at friction ${c.friction.toFixed(0)}` +
    (strict ? ` · OTP-on-every-payment (friction ${strict.friction.toFixed(0)}, ${strict.prevented_pct.toFixed(1)}%) is off the right edge` : "") +
    ` · ${r.elapsed_ms} ms`;
}

async function runSim(ev) {
  ev?.preventDefault();
  const f = $("#sim-form");
  $("#sim-error").textContent = "";
  const btn = f.querySelector("button");
  btn.disabled = true;
  try {
    drawChart(await api("/simulate", {
      seed: Number(f.seed.value), n_users: Number(f.n_users.value),
      payments_per_user: Number(f.payments_per_user.value),
      attacks_per_type: Number(f.attacks_per_type.value), evasive: f.evasive.checked,
    }));
  } catch (err) {
    $("#sim-error").textContent = err.message;
  } finally {
    btn.disabled = false;
  }
}

// ------------------------------------------------------------------ decision log

async function loadLog() {
  try {
    const { decisions } = await api("/decisions?limit=50");
    const tb = $("#log-table tbody");
    tb.replaceChildren();
    if (!decisions.length) tb.append(el("tr", {}, el("td", { colspan: 6, class: "muted" }, "No decisions yet")));
    for (const d of decisions) {
      tb.append(el("tr", {},
        el("td", {}, d.timestamp.replace("T", " ").slice(0, 19)),
        el("td", { class: "num" }, Number(d.inputs.amount).toLocaleString()),
        el("td", { class: "num" }, String(d.score)),
        el("td", {}, tag(d.tier)),
        el("td", { class: "muted" }, d.breakdown.map((b) => `${b.signal} +${b.points}`).join(", ") || "none"),
        el("td", { class: "muted" }, d.policy_version)));
    }
  } catch (err) {
    console.warn("decision log:", err.message);
  }
}

// ------------------------------------------------------------------ boot

async function boot() {
  const presets = $("#presets");
  for (const [name, p] of Object.entries(PRESETS)) {
    const b = el("button", { type: "button" }, name);
    b.addEventListener("click", () => {
      presets.querySelectorAll("button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      fillPayment(p);
      scoreCurrent();
    });
    presets.append(b);
  }
  fillPayment(PRESETS["Chai stall"]);
  $("#payment-form").addEventListener("submit", (e) => { e.preventDefault(); scoreCurrent(); });
  $("#sim-form").addEventListener("submit", runSim);
  $("#refresh-log").addEventListener("click", loadLog);

  const slider = $("#strictness");
  let timer = null;
  slider.addEventListener("input", () => {
    $("#strictness-out").textContent = slider.value;
    clearTimeout(timer);
    timer = setTimeout(() => setStrictness(Number(slider.value)), 150);
  });
  $("#reset-policy").addEventListener("click", async () => {
    showPolicy(await api("/policy", { reset: true }));
    slider.value = -policy.shift;
    $("#strictness-out").textContent = slider.value;
    if (!$("#result").hidden) scoreCurrent();
  });

  try {
    const h = await api("/health");
    $("#status").className = "status ok";
    $("#status").textContent = `service ok · policy ${h.policy_version}`;
    showPolicy(await api("/policy"));
    const [lo, hi] = policy.strictness_range;
    slider.min = lo; slider.max = hi; slider.value = -policy.shift;
    $("#strictness-out").textContent = slider.value;
  } catch {
    $("#status").className = "status down";
    $("#status").textContent = "service unreachable";
  }
  loadLog();
  runSim();
}

boot();
