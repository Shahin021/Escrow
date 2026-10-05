/**
 * UI layer. Every decision comes from the tested controller; this file only
 * collects inputs, renders what the controller reports, and calls it back.
 */

import { resolveConfig, SUPPORTED_NETWORKS } from "./config.js";
import { createController } from "./controller.js";
import { isPermissionless } from "./actions.js";
import { methodFor } from "./methods.js";
import { connectWallet, createReadClient, disconnectWallet } from "./wallet.js";
import { readChainId, watchWallet } from "./network.js";
import { explainError, labelFor } from "./tx.js";

const config = resolveConfig(import.meta.env || {});
const networkInfo = SUPPORTED_NETWORKS[config.network] || { label: config.network, chainId: 0 };
const controller = createController({ config, networkInfo });

let readClient = null;
let unwatch = () => {};

const el = (id) => document.getElementById(id);
const setText = (node, value) => {
  node.textContent = value;
};

function chainNow() {
  // Until a wallet is connected there is no chain clock to read, so local
  // time is used only to decide which buttons to show; the contract itself
  // re-checks every deadline.
  return Math.floor(Date.now() / 1000);
}

function fieldsHtml(action, scope) {
  return methodFor(action)
    .fields.map(
      (field) =>
        `<label>${field.label}<input data-input="${field.name}" data-scope="${scope}" ` +
        `type="text" inputmode="${field.kind === "amount" ? "numeric" : "text"}" /></label>`,
    )
    .join("");
}

function collectInput(scope) {
  const input = {};

  document
    .querySelectorAll(`[data-input][data-scope="${scope}"]`)
    .forEach((node) => {
      input[node.dataset.input] = node.value.trim();
    });

  return input;
}

function renderConfig() {
  setText(
    el("config-banner"),
    config.ok
      ? `${networkInfo.label} · escrow ${config.escrowAddress}`
      : "Not configured for a deployment yet, so this app is read-only: " +
          config.problems.join(" "),
  );
}

function renderWallet() {
  const { account } = controller.state;

  setText(
    el("account"),
    account
      ? `Connected as ${account} (chain ${controller.state.chainId ?? "unknown"})`
      : "Not connected. Viewing is read-only.",
  );

  el("connect").hidden = Boolean(account);
  el("disconnect").hidden = !account;

  setText(
    el("network-warning"),
    controller.reconnectWarning || controller.networkWarning || "",
  );
}

function renderProject() {
  const p = controller.state.project;

  if (!p) {
    el("project").innerHTML = "<p>No project loaded.</p>";

    return;
  }

  el("project").innerHTML = `
    <h2>Project</h2>
    <table>
      <tr><th>Status</th><td>${p.status}</td></tr>
      <tr><th>Client</th><td><code>${p.client}</code></td></tr>
      <tr><th>Worker</th><td><code>${p.worker}</code></td></tr>
      <tr><th>Locked</th><td>${p.locked}</td></tr>
      <tr><th>Released</th><td>${p.totalReleased}</td></tr>
      <tr><th>Refunded</th><td>${p.totalRefunded}</td></tr>
      <tr><th>Appeal credit</th><td>${p.appealCreditHeld}</td></tr>
      <tr><th>Appeal bond</th><td>${p.appealBondHeld}</td></tr>
      <tr><th>Unmatched</th><td>${p.unmatchedHeld}</td></tr>
    </table>
    ${
      p.settlement.active
        ? `<p>Active settlement proposal from <code>${p.settlement.proposer}</code>: ` +
          `worker ${p.settlement.toWorker}, client ${p.settlement.toClient} ` +
          `(nonce ${p.settlement.nonce}).</p>`
        : ""
    }`;
}

function actionBlock(action, scope, milestoneIndex, outflowId) {
  const enabled = controller.writesEnabled;
  const method = methodFor(action);

  return `
    <form class="action" data-action="${action}" data-scope="${scope}"
          data-milestone="${milestoneIndex ?? ""}" data-outflow="${outflowId ?? ""}">
      <fieldset ${enabled ? "" : "disabled"}>
        <legend>${method.label}${isPermissionless(action) ? " (anyone)" : ""}</legend>
        ${fieldsHtml(action, scope)}
        <button type="submit">${method.label}</button>
      </fieldset>
    </form>`;
}

function renderProjectActions() {
  const actions = controller.projectActions();

  el("actions").innerHTML = actions.length
    ? `<h2>Project actions</h2>` +
      actions
        .map((a) => actionBlock(a.action, `p-${a.action}`, null))
        .join("")
    : "";
}

function renderMilestones() {
  const rows = controller.actionsByMilestone();

  el("milestones").innerHTML = rows.length
    ? `<h2>Milestones</h2>` +
      rows
        .map((row) => {
          const m = controller.state.milestones[row.milestoneIndex];

          return `
            <article>
              <h3>Milestone ${row.milestoneIndex} — ${row.status}</h3>
              <p>Amount ${m.amount} · revisions ${m.revisionCount} · attempts ${m.attemptCount}
                 ${m.deliveryDeadline !== "0" ? `· delivery deadline ${m.deliveryDeadline}` : ""}
                 ${m.appealExpiry !== "0" ? `· appeal expiry ${m.appealExpiry}` : ""}</p>
              ${
                row.actions.length
                  ? row.actions
                      .map((a) =>
                        actionBlock(a.action, `m${row.milestoneIndex}-${a.action}`, row.milestoneIndex),
                      )
                      .join("")
                  : "<p>No actions available for this account right now.</p>"
              }
            </article>`;
        })
        .join("")
    : "";
}

function renderOutflows() {
  const rows = controller.outflowRows();

  el("outflows").innerHTML = rows.length
    ? `<h2>Transfers</h2>` +
      rows
        .map(
          (row) => `
          <article>
            <h3>Transfer ${row.id} — ${row.kind} — ${row.status}</h3>
            <p>Amount ${row.amount} to <code>${row.recipient}</code>
               (milestone ${row.milestone})</p>
            ${row.redirectBlockedReason ? `<p>${row.redirectBlockedReason}</p>` : ""}
            ${row.actions
              .map((action) => actionBlock(action, `o${row.id}-${action}`, row.milestone, row.id))
              .join("")}
          </article>`,
        )
        .join("")
    : "";
}

function bindForms() {
  document.querySelectorAll("form.action").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();

      const { action, scope, milestone, outflow } = form.dataset;

      try {
        await controller.dispatch(action, {
          milestoneIndex: milestone === "" ? 0 : Number(milestone),
          outflowId: outflow === "" ? undefined : Number(outflow),
          input: collectInput(scope),
        });

        setText(el("tx-status"), labelFor(controller.state.txState));
        await refresh();
      } catch (error) {
        setText(el("tx-status"), explainError(error));
      }
    });
  });
}

function render() {
  renderConfig();
  renderWallet();
  renderProject();
  renderProjectActions();
  renderMilestones();
  renderOutflows();
  bindForms();
  setText(el("tx-status"), labelFor(controller.state.txState));
}

async function refresh() {
  if (!config.ok) {
    render();

    return;
  }

  readClient = readClient || createReadClient(config.network);

  try {
    await controller.refresh(readClient, chainNow());
  } catch (error) {
    setText(el("tx-status"), explainError(error));
  }

  render();
}

el("connect").addEventListener("click", async () => {
  try {
    const { client, account } = await connectWallet(config.network);
    const provider = globalThis.window.ethereum;
    const chainId = await readChainId(provider);

    controller.setWallet({ client, account, chainId });

    unwatch();
    unwatch = watchWallet(provider, {
      onAccountsChanged: (next) => {
        // The client is bound to the old account, so the controller drops it
        // and writes stay disabled until the user reconnects.
        controller.setAccount(next);
        refresh();
      },
      onChainChanged: (next) => {
        controller.setChainId(next);
        render();
      },
    });

    await refresh();
  } catch (error) {
    setText(el("tx-status"), explainError(error));
  }
});

el("disconnect").addEventListener("click", () => {
  const result = disconnectWallet();

  unwatch();
  unwatch = () => {};
  controller.setWallet({ client: null, account: null, chainId: null });

  setText(el("tx-status"), result.note);
  refresh();
});

render();
refresh();
