# Phase 5 — wallet-connected dApp

A static, client-side app in `dapp/`, buildable for GitHub Pages. It reads
escrow state and, when configured and connected, sends the actions the
contract allows. No contract logic changed in this phase.

## Wallet reality

GenLayer is **not** an ordinary EVM wallet target. In `genlayer-js@1.1.8`,
`client.connect()` calls `wallet_getSnaps` and `wallet_requestSnaps`, so the
flow is **MetaMask plus the GenLayer Snap**, and the SDK checks for MetaMask
Flask. An injected EVM provider alone is not enough, and the app says so
rather than failing obscurely.

Verified SDK surface, read from the package's own type definitions and used
as-is:

```
createClient({ chain, account, provider })
client.connect(network, snapSource?)
client.readContract({ address, functionName, args, jsonSafeReturn })
client.writeContract({ address, functionName, args, value })
client.waitForTransactionReceipt({ hash, status, interval, retries })
```

No key or seed phrase is ever requested, read or stored; signing stays in the
wallet. A test asserts no provider method matching private/seed/mnemonic/export
is ever called. "Disconnect" clears local state only, and the UI says plainly
that MetaMask still lists the site until the user removes it there.

## Configuration, not a hardcoded deployment

There is no confirmed Bradbury deployment of the Phase 4 escrow, so no
address is baked in. `src/config.js` resolves the network and addresses from
build-time variables and reports every problem it finds. With no address, or
a malformed one, the app shows a "not configured" banner and **writes are
disabled**; reading is still allowed. Addresses and RPC endpoints are public
values, not secrets, and nothing secret is read by the frontend or committed.

## Calls are built from the contract's own signature

`src/methods.js` transcribes every write method from
`contracts/project_escrow.py`: its arguments in order, which inputs the UI
must collect, and whether it is payable. `buildCall()` produces the exact
`{ functionName, args, value }` and refuses to send anything when a required
input is missing or malformed, so a payable call can never go out with zero
value and a parameterised call can never go out with empty arguments.

That table also caught a real bug: an earlier draft offered
`expire_unavailable`, which the contract does not have. It is gone.

Two inputs are never typed by the user: the settlement nonce and the outflow
id come from the contract and the clicked row.

## What the app offers

The action list is computed from the connected account's role and the current
project and milestone state, mirroring the contract's own guards so no button
is offered that the contract would reject:

* client: fund, propose or accept settlement, withdraw their own proposal
* worker: submit and replace evidence, claim payment, appeal, abort a failed
  appeal, add and withdraw appeal credit, settlement actions
* anyone: resolve, mark a review stalled, expire a delivery, finalize a
  rejection, emit or confirm an outflow, sweep unmatched value, close the
  project, since those are deterministic and cannot redirect value

Actions are rendered **per milestone**, each labelled with its milestone, and
transfers are listed with their ids, kinds, amounts, recipients and statuses.
A bounced transfer offers redirection only to its recipient, matching the
contract's authorization, and tells anyone else why they cannot.

## Timing comes from the contract

Every deadline is read from the contract as an absolute epoch second
(`get_milestone_delivery_deadline`, `get_milestone_stall_eligible_at`,
`get_milestone_appeal_expiry`, `get_milestone_appeal_failure_threshold`,
`get_milestone_unavailable_grace_expiry`), with "0" meaning not applicable.
The app only compares those values against the current time, using the
contract's own boundary rules: a deadline action at `now >= deadline`, a
window action while `now < expiry`. No deadline is recomputed client-side.

## Network and account changes

The connected chain is read from the provider with `eth_chainId`, not assumed
from a successful `connect()`. The app subscribes to `accountsChanged` and
`chainChanged`, follows an account into its new role, and disables writes on
a chain mismatch with an explicit warning.

## Transaction progress

States are `submitted`, `accepted`, `finalized` and `failed`. Acceptance is
shown as "Accepted, not final yet", never as success. A transaction that
finalizes with `FINISHED_WITH_ERROR` is reported as **failed**, which probe
L9 showed is a real outcome. Errors are explained in the user's terms, and a
contract `UserError` is surfaced with the contract's own message rather than
a stack trace.

## GitHub Pages

`vite.config.js` sets `base: "./"`, so the build works under a repository
subpath without hardcoding the repository name; the emitted `index.html`
references `./assets/...`. The workflow in
`.github/workflows/dapp-pages.yml` installs, tests, builds and uploads the
build as an artifact. **Publishing is deliberately not enabled**: with no
confirmed deployment, a published site would imply a live app that does not
exist.

## Limitations

* **Not verified against a live contract.** Every read and write path is
  tested against a mocked client, because no escrow is deployed. The first
  real run belongs to Phase 6.
* Network-mismatch handling assumes `client.connect()` performs the chain
  switch it documents; this is not verified against a live wallet here.
* The registry views are not surfaced in the UI.
* Timing predicates are compared against the browser's clock, since there is
  no chain-time view to read before a transaction; the contract re-checks
  every deadline itself, so a wrong local clock can only show a button that
  the contract then rejects.
* The app does not yet pre-validate a pinned evidence URL against the
  configured allowed sources, so a malformed URL is rejected by the contract
  rather than by the form.
* The bundle is a single ~540 kB chunk, dominated by the SDK; no code
  splitting was attempted.
