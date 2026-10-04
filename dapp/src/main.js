/**
 * UI wiring. All decisions come from the tested pure modules; this file only
 * renders them and calls the verified client methods.
 */

import { resolveConfig, SUPPORTED_NETWORKS } from "./config.js";
import {
  availableActions,
  isPermissionless,
  roleOf,
  writesEnabled,
} from "./actions.js";
import { connectWallet, createReadClient, disconnectWallet } from "./wallet.js";
import { readMilestone, readProject, sendAction } from "./escrow.js";
import { explainError, labelFor, TX_IDLE } from "./tx.js";

const config = resolveConfig(import.meta.env || {});

const state = {
  client: null,
  account: null,
  project: null,
  milestones: [],
  txState: TX_IDLE,
};

const el = (id) => document.getElementById(id);
const text = (node, value) => {
  node.textContent = value;
};

function renderConfig() {
  if (config.ok) {
    text(
      el("config-banner"),
      `${SUPPORTED_NETWORKS[config.network].label} · escrow ${config.escrowAddress}`,
    );

    return;
  }

  text(
    el("config-banner"),
    "Not configured for a deployment yet, so this app is read-only: " +
      config.problems.join(" "),
  );
}

function renderAccount() {
  text(
    el("account"),
    state.account
      ? `Connected as ${state.account}`
      : "Not connected. Viewing is read-only.",
  );

  el("connect").hidden = Boolean(state.account);
  el("disconnect").hidden = !state.account;
}

function renderProject() {
  const node = el("project");

  if (!state.project) {
    node.innerHTML = "<p>No project loaded.</p>";

    return;
  }

  const p = state.project;

  node.innerHTML = `
    <h2>Project</h2>
    <table>
      <tr><th>Status</th><td>${p.status}</td></tr>
      <tr><th>Client</th><td><code>${p.client}</code></td></tr>
      <tr><th>Worker</th><td><code>${p.worker}</code></td></tr>
      <tr><th>Funded</th><td>${p.totalFunded}</td></tr>
      <tr><th>Locked</th><td>${p.locked}</td></tr>
      <tr><th>Released</th><td>${p.totalReleased}</td></tr>
      <tr><th>Refunded</th><td>${p.totalRefunded}</td></tr>
    </table>`;
}

function renderMilestones() {
  const rows = state.milestones
    .map(
      (m) =>
        `<tr><td>${m.index}</td><td>${m.status}</td><td>${m.amount}</td>` +
        `<td>${m.revisionCount}</td><td>${m.attemptCount}</td></tr>`,
    )
    .join("");

  el("milestones").innerHTML = state.milestones.length
    ? `<h2>Milestones</h2><table>
         <tr><th>#</th><th>Status</th><th>Amount</th><th>Revisions</th><th>Attempts</th></tr>
         ${rows}
       </table>`
    : "";
}

function renderActions() {
  const node = el("actions");
  const role = roleOf(state.account, state.project);
  const enabled = writesEnabled({
    configOk: config.ok,
    connected: Boolean(state.account),
    correctNetwork: true,
  });

  const actions = availableActions({
    role,
    project: state.project ? { ...state.project, account: state.account } : null,
    milestone: state.milestones[0] || null,
  });

  if (!actions.length) {
    node.innerHTML = `<h2>Actions</h2><p>Nothing to do as ${role}.</p>`;

    return;
  }

  node.innerHTML =
    `<h2>Actions (${role})</h2>` +
    actions
      .map(
        (a) =>
          `<button data-action="${a.action}" ${enabled ? "" : "disabled"}>` +
          `${a.label}${isPermissionless(a.action) ? " (anyone)" : ""}</button>`,
      )
      .join("");

  node.querySelectorAll("button[data-action]").forEach((button) => {
    button.addEventListener("click", () => run(button.dataset.action));
  });
}

function render() {
  renderConfig();
  renderAccount();
  renderProject();
  renderMilestones();
  renderActions();
  text(el("tx-status"), labelFor(state.txState));
}

async function refresh() {
  if (!config.ok) return;

  const client = state.client || createReadClient(config.network);

  state.project = await readProject(client, config.escrowAddress);

  const count = Math.min(state.project.milestoneCount, 20);
  const indexes = Array.from({ length: count }, (_, i) => i);

  state.milestones = await Promise.all(
    indexes.map((i) => readMilestone(client, config.escrowAddress, i)),
  );

  render();
}

async function run(action) {
  if (!state.client) return;

  try {
    await sendAction(
      state.client,
      { address: config.escrowAddress, functionName: action },
      (txState) => {
        state.txState = txState;
        text(el("tx-status"), labelFor(txState));
      },
    );

    await refresh();
  } catch (error) {
    text(el("tx-status"), explainError(error));
  }
}

el("connect").addEventListener("click", async () => {
  try {
    const result = await connectWallet(config.network);

    state.client = result.client;
    state.account = result.account;

    await refresh();
    render();
  } catch (error) {
    text(el("tx-status"), explainError(error));
  }
});

el("disconnect").addEventListener("click", () => {
  const result = disconnectWallet();

  state.client = null;
  state.account = null;

  text(el("tx-status"), result.note);
  render();
});

render();
refresh().catch((error) => text(el("tx-status"), explainError(error)));
