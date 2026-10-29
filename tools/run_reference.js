#!/usr/bin/env node
// Runs the read-only JavaScript reference (reference/upi_reference_core.js) and prints, as
// JSON, the golden scores and aggregate results so tests/test_parity.py can compare them with
// the Python pipeline.
//
//   node tools/run_reference.js --seeds 1,2,3 [--reference path] [--config-json file|-]
//
// The reference file is loaded in a sandbox whether it is a CommonJS module or a plain
// browser script that defines globals. Its exact function names were not available when this
// harness was written, so every call into it goes through the ADAPTER below. If the reference
// uses other names or argument shapes, change only the ADAPTER.
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

// ------------------------------------------------------------------ arguments

function parseArgs(argv) {
  const out = {
    reference: path.join(__dirname, "..", "reference", "upi_reference_core.js"),
    seeds: [7], configJson: null,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--reference") out.reference = argv[++i];
    else if (a === "--seeds") out.seeds = argv[++i].split(",").map(Number);
    else if (a === "--config-json") out.configJson = argv[++i];
    else if (a === "--help" || a === "-h") {
      console.log("usage: node tools/run_reference.js --seeds 1,2 [--reference f] [--config-json f|-]");
      process.exit(0);
    } else fail(`unknown argument ${a}`);
  }
  return out;
}

function fail(msg, code = 2) {
  process.stderr.write(`run_reference.js: ${msg}\n`);
  process.exit(code);
}

// ------------------------------------------------------------------ loading

function loadReference(file) {
  if (!fs.existsSync(file)) fail(`reference file not found: ${file}`);
  const code = fs.readFileSync(file, "utf8");
  const module = { exports: {} };
  const sandbox = { module, exports: module.exports, console, Math, Date, JSON };
  sandbox.globalThis = sandbox; sandbox.window = sandbox; sandbox.self = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: file });
  // Prefer explicit exports, then fall back to globals the script defined.
  const api = Object.assign({}, sandbox, module.exports);
  if (sandbox.UPI && typeof sandbox.UPI === "object") Object.assign(api, sandbox.UPI);
  return api;
}

function pick(api, names, what) {
  for (const n of names) if (typeof api[n] === "function") return api[n];
  const fns = Object.keys(api).filter((k) => typeof api[k] === "function");
  fail(`could not find ${what} (tried ${names.join(", ")}). ` +
       `Functions the reference defines: ${fns.join(", ") || "none"}. Update the ADAPTER.`);
}

// ------------------------------------------------------------------ ADAPTER

function makeAdapter(api) {
  const scoreFn = pick(api, ["score", "scoreTx", "scorePayment"], "score(tx, weights)");
  const generateFn = pick(api, ["generate", "simulate", "generateData"], "generate(cfg, seed)");
  const evaluateFn = pick(api, ["evaluate", "evaluatePolicy"], "evaluate(policy, legit, attacks, assume)");
  const engineFn = pick(api, ["makeEngine", "make_engine", "engine"], "makeEngine(weights, shift)");
  const baselinesFn = pick(api, ["getBaselines", "get_baselines", "baselines"], "getBaselines(cfg)");
  return {
    // Must return a number (the capped score) or {score} / [score, breakdown].
    score: (tx, cfg) => {
      const r = scoreFn(tx, cfg ? cfg.policy.weights : undefined);
      return typeof r === "number" ? r : Array.isArray(r) ? r[0] : r.score;
    },
    // Must return {legit, attacks} as arrays of A3-like objects.
    generate: (cfg, seed) => {
      const r = generateFn(cfg, seed);
      return Array.isArray(r) ? { legit: r[0], attacks: r[1] } : r;
    },
    policies: (cfg) => {
      const b = baselinesFn(cfg);
      return { ...b, engine: engineFn(cfg ? cfg.policy.weights : undefined, 0) };
    },
    evaluate: (policy, data, cfg) => evaluateFn(policy, data.legit, data.attacks, cfg ? cfg.costs : undefined),
  };
}

// ------------------------------------------------------------------ golden cases

const OFF = { new_device: false, new_payee: false, velocity: false, sim_change: false,
  remote: false, far_location: false, fri: 0 };
const GOLDEN = {
  A_chai: { ...OFF, network: "mobile", ratio: 0.72 },
  B_rent: { ...OFF, network: "home", new_payee: true, ratio: 180 },
  C_simswap: { ...OFF, network: "public", new_device: true, new_payee: true, sim_change: true, ratio: 180 },
  D_screenshare: { ...OFF, network: "home", remote: true, new_payee: true, ratio: 180 },
  E_stolen: { ...OFF, network: "home", new_payee: true, velocity: true, far_location: true, ratio: 180 },
};

function tierFor(score, thr) {
  if (score >= thr[2]) return "3";
  if (score >= thr[1]) return "2";
  if (score >= thr[0]) return "1";
  return "0";
}

// ------------------------------------------------------------------ main

function main() {
  const args = parseArgs(process.argv.slice(2));
  let cfg = null;
  if (args.configJson) {
    const text = args.configJson === "-" ? fs.readFileSync(0, "utf8") : fs.readFileSync(args.configJson, "utf8");
    cfg = JSON.parse(text);
  }
  const ad = makeAdapter(loadReference(args.reference));
  const thr = cfg ? cfg.policy.thresholds : [20, 50, 70];

  const golden = {};
  for (const [name, tx] of Object.entries(GOLDEN)) {
    for (const k of ["amount", "median"]) if (!(k in tx)) tx[k] = k === "median" ? 250 : Math.round(250 * tx.ratio);
    const s = ad.score(tx, cfg);
    golden[name] = { score: s, tier: tierFor(s, thr) };
  }

  const per = {};
  for (const seed of args.seeds) {
    const data = ad.generate(cfg, seed);
    for (const [name, pol] of Object.entries(ad.policies(cfg))) {
      const m = ad.evaluate(pol, data, cfg);
      const row = (per[name] ||= { prevented_pct: [], friction: [], extra: [] });
      row.prevented_pct.push(m.prevented_pct ?? m.prevented ?? m.preventedPct);
      row.friction.push(m.friction);
      row.extra.push(m.extra ?? m.extraChecks);
    }
  }
  const mean = (a) => a.reduce((x, y) => x + y, 0) / a.length;
  const aggregates = {};
  for (const [name, row] of Object.entries(per)) {
    aggregates[name] = Object.fromEntries(Object.entries(row).map(([k, v]) => [k, mean(v)]));
  }
  process.stdout.write(JSON.stringify({ seeds: args.seeds, golden, aggregates }, null, 2) + "\n");
}

main();
