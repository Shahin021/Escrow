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

## What the app offers

The action list is computed from the connected account's role and the current
project and milestone state, mirroring the contract's own guards so no button
is offered that the contract would reject:

* client: fund, propose or accept settlement, withdraw their own proposal
* worker: submit and replace evidence, claim payment, appeal, abort a failed
  appeal, add and withdraw appeal credit, settlement actions
* anyone: resolve, mark a review stalled, expire a delivery, finalize a
  rejection, confirm an outflow, sweep unmatched value, close the project,
  since those are deterministic and cannot redirect value

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
* The UI renders the first milestone's actions; a full per-milestone
  interface and the registry views are not built yet.
* Chain-specific timing predicates (stallable, expirable, finalizable,
  appeal abortable) are passed into the action logic as flags and are not yet
  derived in the UI from the contract's timestamp views.
* The bundle is a single ~540 kB chunk, dominated by the SDK; no code
  splitting was attempted.
