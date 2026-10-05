/**
 * The application's decision layer, independent of the DOM.
 *
 * It holds the loaded state, decides which actions each milestone and outflow
 * offers, and sends exactly the call the contract expects. The UI renders
 * what this returns; tests drive it directly.
 */

import { availableActions, roleOf, writesEnabled } from "./actions.js";
import { readMilestone, readOutflows, readProject, sendAction } from "./escrow.js";
import { buildCall } from "./methods.js";
import { milestonePredicates, outflowActions, projectPredicates } from "./predicates.js";
import { describeMismatch, networkMatches } from "./network.js";
import { TX_IDLE } from "./tx.js";

export function createController({ config, networkInfo }) {
  const state = {
    client: null,
    account: null,
    chainId: null,
    project: null,
    milestones: [],
    outflows: [],
    now: 0,
    txState: TX_IDLE,
    lastError: null,
  };

  const api = {
    state,

    setWallet({ client, account, chainId }) {
      state.client = client || null;
      state.account = account || null;
      state.chainId = chainId === undefined ? state.chainId : chainId;
    },

    setChainId(chainId) {
      state.chainId = chainId;
    },

    setAccount(account) {
      state.account = account;
    },

    get correctNetwork() {
      return networkMatches(state.chainId, networkInfo.chainId);
    },

    get writesEnabled() {
      return writesEnabled({
        configOk: config.ok,
        connected: Boolean(state.account && state.client),
        correctNetwork: api.correctNetwork,
      });
    },

    get networkWarning() {
      if (!state.account) return null;

      return api.correctNetwork
        ? null
        : describeMismatch(state.chainId, networkInfo);
    },

    /** Load everything the UI and the predicates need. */
    async refresh(readClient, now) {
      const client = state.client || readClient;

      if (!config.ok || !client) return state;

      const project = await readProject(client, config.escrowAddress);
      const count = Math.min(project.milestoneCount, 20);

      const milestones = await Promise.all(
        Array.from({ length: count }, (_, i) =>
          readMilestone(client, config.escrowAddress, i),
        ),
      );

      const outflows = await readOutflows(
        client,
        config.escrowAddress,
        project.outflowCount,
      );

      const activeIndex = milestones.findIndex(
        (m) =>
          m.status !== "LOCKED" &&
          m.status !== "RELEASED" &&
          m.status !== "REFUNDED" &&
          m.status !== "CANCELLED",
      );

      state.now = now;
      state.outflows = outflowActions(outflows, state.account);
      state.project = projectPredicates(project, outflows, state.account);
      state.milestones = milestones.map((m) =>
        milestonePredicates(m, now, activeIndex),
      );

      return state;
    },

    role() {
      return roleOf(state.account, state.project);
    },

    /** Actions per milestone, so every milestone is actionable, not just #0. */
    actionsByMilestone() {
      const role = api.role();

      return state.milestones.map((milestone) => ({
        milestoneIndex: milestone.index,
        status: milestone.status,
        actions: availableActions({ role, project: state.project, milestone }),
      }));
    },

    /** Outflow rows with their permitted actions and ownership reasons. */
    outflowRows() {
      return state.outflows.map((outflow) => ({
        ...outflow,
        actions: [
          ...(outflow.confirmable ? ["confirm_outflow"] : []),
          ...(outflow.redirectable ? ["redirect_outflow"] : []),
        ],
      }));
    },

    /** Inputs the contract supplies rather than the user. */
    implicitInput(action, { outflowId } = {}) {
      if (action === "accept_settlement") {
        return { nonce: state.project.settlement.nonce };
      }

      if (action === "confirm_outflow" || action === "redirect_outflow") {
        return { outflow_id: outflowId };
      }

      return {};
    },

    async dispatch(action, { milestoneIndex = 0, input = {}, outflowId } = {}) {
      if (!api.writesEnabled) {
        throw new Error(
          api.networkWarning ||
            "Writes are disabled: connect a wallet and configure a deployed escrow.",
        );
      }

      const call = buildCall(action, {
        milestoneIndex,
        input: { ...api.implicitInput(action, { outflowId }), ...input },
      });

      return sendAction(
        state.client,
        { address: config.escrowAddress, ...call },
        (txState) => {
          state.txState = txState;
        },
      );
    },
  };

  return api;
}
