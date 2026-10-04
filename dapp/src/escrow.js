/**
 * Reads and writes against ProjectEscrow, using only verified genlayer-js
 * calls: readContract, writeContract and waitForTransactionReceipt.
 */

import { stateFromReceipt, TX_SUBMITTED } from "./tx.js";

export async function readProject(client, address) {
  const [summary, accounting, settlement] = await Promise.all([
    client.readContract({ address, functionName: "parties", args: [] }),
    client.readContract({ address, functionName: "get_accounting", args: [] }),
    client.readContract({ address, functionName: "get_settlement", args: [] }),
  ]);

  return {
    interfaceId: summary.interface_id,
    client: summary.client,
    worker: summary.worker,
    status: summary.project_status,
    milestoneCount: Number(summary.milestone_count),
    totalFunded: summary.total_funded,
    totalReleased: summary.total_released,
    totalRefunded: summary.total_refunded,
    locked: accounting.locked,
    queuedOut: accounting.queued_out,
    inflightOut: accounting.inflight_out,
    bouncedHeld: accounting.bounced_held,
    unmatchedHeld: accounting.unmatched_held,
    appealCreditHeld: accounting.appeal_credit_held,
    appealBondHeld: accounting.appeal_bond_held,
    settlement,
  };
}

export async function readMilestone(client, address, index) {
  const call = (functionName, args = [index]) =>
    client.readContract({ address, functionName, args });

  const [status, amount, revisions, attempts] = await Promise.all([
    call("get_milestone_status"),
    call("get_milestone_amount"),
    call("get_milestone_revision_count"),
    call("get_attempt_count"),
  ]);

  return {
    index,
    status,
    amount,
    revisionCount: Number(revisions),
    attemptCount: Number(attempts),
  };
}

/**
 * Send a write and follow it to finality.
 *
 * onProgress receives each lifecycle state, so the UI can show "submitted",
 * then "accepted", then "finalized" or "failed". Nothing is reported as
 * succeeded before the network says finalized.
 */
export async function sendAction(
  client,
  { address, functionName, args = [], value = 0n },
  onProgress = () => {},
) {
  const hash = await client.writeContract({
    address,
    functionName,
    args,
    value,
  });

  onProgress(TX_SUBMITTED, { hash });

  const receipt = await client.waitForTransactionReceipt({
    hash,
    status: "FINALIZED",
  });

  const state = stateFromReceipt(receipt);

  onProgress(state, { hash, receipt });

  return { hash, receipt, state };
}
