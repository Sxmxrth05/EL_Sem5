// Minimal stand-in for reference/upi_reference_core.js, used only to test that
// tools/run_reference.js can load a script, find its functions and print JSON.
// It is NOT the real reference and its numbers mean nothing.
"use strict";

const W = { new_device: 30, net_mobile: 5, net_public: 15, new_payee: 15, amt_2x_10x: 10,
  amt_over_10x: 25, velocity: 10, sim_change: 25, remote: 30, far_location: 15,
  fri_1: 10, fri_2: 20, fri_3: 35 };

function score(tx, weights) {
  const w = weights || W;
  let s = 0;
  if (tx.new_device) s += w.new_device;
  if (tx.network === "mobile") s += w.net_mobile;
  if (tx.network === "public") s += w.net_public;
  if (tx.new_payee) s += w.new_payee;
  if (tx.ratio >= 2 && tx.ratio <= 10) s += w.amt_2x_10x;
  else if (tx.ratio > 10) s += w.amt_over_10x;
  for (const k of ["velocity", "sim_change", "remote", "far_location"]) if (tx[k]) s += w[k];
  if (tx.fri) s += w[`fri_${tx.fri}`];
  return Math.min(s, 100);
}

function generate(cfg, seed) {
  const mk = (i, kind) => ({ kind, attack_type: kind === "attack" ? "replay" : null,
    amount: 100 + ((i * 37 + seed) % 50), ratio: 1, network: "home", fri: 0, u: 0.5 });
  return { legit: [0, 1, 2, 3].map((i) => mk(i, "legit")), attacks: [0, 1].map((i) => mk(i, "attack")) };
}

function makeEngine(weights, shift) {
  return (rows) => rows.map((r) => (score(r, weights) >= 20 + shift ? "1" : "0"));
}

function getBaselines() {
  return { pin_only: (rows) => rows.map(() => "0") };
}

function evaluate(policy, legit, attacks) {
  const t = policy(legit);
  return { prevented_pct: 0, friction: (t.filter((x) => x !== "0").length / legit.length) * 1000,
    extra: 0 };
}

module.exports = { score, generate, makeEngine, getBaselines, evaluate };
