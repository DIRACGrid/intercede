# IC-ADR-003: Backend credentials — typed, declared, provider-supplied

## Metadata

- **Created By:** Alexandre Boyer
- **Date:** 2026-07-03
- **Status:** Draft
- **Decision Maker(s):** Federico Stagni, Christophe Haen, Chris Burr
- **Stakeholders:** interCEde backend implementers; DIRAC/DiracX SiteDirector and PushJobAgent maintainers; DiracX token-issuance maintainers; site/VO operators
- **Depends on:** IC-ADR-001 (contract shape, lifecycle, Tier A/B/C surface)

> **Scope and altitude.** Direction-setting, like IC-ADR-001. This ADR decides how *backend*
> authentication credentials — bearer tokens and X.509 proxies — are **typed, declared, supplied,
> refreshed, scoped and materialised**. It does not decide token *issuance* (DiracX's), *payload*
> credential renewal (pilot-side, DIRAC's `_monitorProxy` lineage), or the exact dataclass fields
> (implementation). Long-lived transport secrets (SSH keys) are configuration, not credentials, and
> are only delimited here.

## Abstract

Backend auth is on the critical path for the ARC and HTCondor-CE backends, and DIRAC's incumbent
model does not survive the move to a stateless, cached, async library: credentials are **mutated
onto** long-lived CE objects (`setProxy`/`setToken`), freshness policy is **duplicated across
callers**, token opt-in is a **stringly-typed CS tag**, and audience is a bare attribute. This ADR
replaces that with three pieces: **typed, immutable credential values** (`BearerToken`,
`X509Proxy`, grouped in a `CredentialSet`); **backend-declared `CredentialRequirements`** (which
kinds it accepts, for which audience/scopes — data computed from the backend's resolved config,
replacing `Tag: Token[:vo]` and `audienceName`); and **provider-based supply** — backends pull a
fresh `CredentialSet` from a caller-supplied `CredentialProvider` when theirs is missing or near
expiry, so freshness *mechanics* live in one place while issuance/renewal *policy* stays entirely
in the consumer (DiracX token machinery, DIRAC's proxy manager, or a static file). Materialisation
(token/proxy files, env injection for CLI backends) is internal (Tier C). ARC's proxy delegation
remains backend-internal mechanics consuming a provider-supplied proxy.

## Motivation

What the DIRAC code actually does today (all verified against the current codebase):

1. **Mutating setters on cached objects.** `ComputingElement.setProxy`/`setToken` mutate CE
   instances that `QueueCECache` caches and reuses across agent cycles. Credential state and
   connection state are entangled on the same long-lived object — exactly the hazard for a library
   whose backends are cached and context-managed (IC-ADR-001 §2, lifecycle).
2. **Caller-side renewal policy, duplicated.** `SiteDirector._setCredentials` inspects
   `ce.proxy.getRemainingSecs()` and re-supplies; `WMSUtilities.setPilotCredentials` reimplements
   the same gate for the pilot-kill/log service path. Two copies of the same freshness loop is the
   documented drift pattern IC-ADR-001 exists to end.
3. **Stringly-typed opt-in.** A CE accepts tokens iff `"Token"` or `f"Token:{vo}"` appears in its
   CS `Tag` list. A *capability declaration* is encoded in a free-form tag shared with scheduling
   metadata.
4. **Audience as a bare attribute.** Callers mint tokens with `audience=ce.audienceName` — an
   untyped per-CE string (AREX: `https://<host>:<port>`; HTCondorCE: `<host>:9619`) with no
   accompanying scopes/kind information.
5. **Per-backend materialisation, ad hoc.** HTCondorCE writes the token to a temp file and injects
   `_CONDOR_*` env vars around each CLI call; the base writes proxies to files and exports
   `X509_USER_PROXY`; Cloud reads a `cloud.auth` ini with a magic `PROXY` secret that dumps the
   pilot proxy.
6. **ARC needs more than a header.** AREX supports bearer tokens *and* X.509 proxy delegations
   (create a delegation via CSR, sign with the proxy chain, upload, renew) — and can need **both at
   once** (`AlwaysIncludeProxy`).
7. **HTCondorCE needs both at once — today, in production.** Even when SCITOKENS authenticates the
   channel, DIRAC's submit description *unconditionally* contains `use_x509userproxy = true`, and
   the code says why: *"For now, we still need to include a proxy in the submit file — HTCondor
   extracts VOMS attribute from it for the sites"* — the proxy rides along for **site-side
   consumption** (per-VO attribution in site accounting, i.e. the APEL pipeline), not for channel
   auth. `_executeCondorCommand` even refuses to run with neither token nor proxy, and token mode
   sets `_CONDOR_DELEGATE_JOB_GSI_CREDENTIALS=false` to work around a condor 24.4 delegation bug
   (HTCONDOR-2904). Together with ARC's pairing, any model that assumes "one credential per
   backend" is wrong on day one — twice.

### Drivers

- **Stateless and cache-safe.** Credentials must not be hidden mutable state on cached backends.
- **Policy with the consumer.** Where credentials *come from* (DiracX token service, proxy
  download, a file) and *when they are renewed* is consumer policy; interCEde owns only the
  mechanics of asking at the right time and using them correctly.
- **Typed declaration.** A consumer must be able to ask a backend "what do you need?" and get data
  — not parse tags.
- **One supply path for one and many credentials.** Token-only, proxy-only, and token+proxy
  backends use the same machinery.
- **Testable in containers.** The model must work with IC-ADR-002's ephemeral per-run credentials
  (arcctl test-CA, IDTOKENS, ssh-keygen).

## Specification

### 1. Credential values (Tier A)

Immutable, typed values with an expiry the machinery can read:

```python
@dataclass(frozen=True)
class BearerToken:
    value: str                      # opaque to interCEde; never logged
    expires_at: datetime | None

@dataclass(frozen=True)
class X509Proxy:
    pem: bytes                      # full chain, PEM; opaque to interCEde beyond expiry
    expires_at: datetime | None

Credential = BearerToken | X509Proxy

@dataclass(frozen=True)
class CredentialSet:                # what a provider returns; may satisfy >1 kind
    def get(self, kind: type[Credential]) -> Credential | None: ...
```

`CredentialSet` exists because of ARC's token+proxy case: requirements may name several kinds, and
one provider call returns everything needed, atomically. interCEde never inspects credential
*contents* (no VOMS parsing, no JWT decoding) beyond expiry bookkeeping.

### 2. Requirements — the backend declares, as data (Tier A)

```python
@dataclass(frozen=True)
class CredentialRequirements:
    kinds: frozenset[type[Credential]]      # the kinds needed TOGETHER (all-of, not a menu)
    audience: str | None = None             # token audience, if kinds include BearerToken
    scopes: frozenset[str] = frozenset()    # e.g. compute.create/compute.read (WLCG profile)
```

A backend computes its `CredentialRequirements` from its resolved configuration (it knows its
endpoint, hence its audience) and exposes them as a read-only property. This single piece of data
replaces both the `Tag: Token[:vo]` opt-in *and* `audienceName`: the consumer reads the
requirements and mints/fetches accordingly. Configuration may still *narrow* a backend that
accepts several kinds ("this site: proxy only"); it can never widen beyond what the backend
declares.

Two semantic rules, both forced by the grid CEs:

- **`kinds` means "all of these, together".** Requirements are a concrete ask for the backend's
  *active configuration*, never a menu of alternatives — both first-party grid CEs need a *set*
  (ARC `AlwaysIncludeProxy`; HTCondorCE token + proxy, Motivation 7). A backend that supports
  alternative modes (token-only *or* proxy-only) exposes the mode as configuration, and its
  requirements reflect the configured mode.
- **A required kind need not authenticate the channel.** HTCondorCE's proxy is materialised into
  the submit description (`use_x509userproxy`) for *site-side* consumption — VOMS attributes
  feeding the site's accounting (APEL) — while the token authenticates the channel. The provider
  sees no difference (it supplies the set); what each member is *for* is backend mechanics
  (Tier C).

### 3. Supply — a provider the backend pulls from (Tier A)

```python
@runtime_checkable
class CredentialProvider(Protocol):
    async def get(self, requirements: CredentialRequirements) -> CredentialSet: ...
```

- The provider is part of the backend's construction config (registry request). A trivial
  `StaticCredentials(CredentialSet)` provider covers files/fixed tokens — and the integration
  stacks.
- **The backend pulls; the consumer implements.** Before an operation, if the backend's current
  set is missing or within a freshness margin of expiry, it awaits `provider.get(...)` — once, in
  one place, inside the library. What the provider *does* (call DiracX's token issuer, download a
  proxy, read a refreshed file) is consumer code. This keeps the mechanism/policy split of
  IC-ADR-001 §9 and deletes the SiteDirector/WMSUtilities duplication by construction.
- Provider calls are per-backend, not per-job: one credential authenticates the *channel/CE*, not
  each submission.
- Failures raise the typed auth branch of `InterCEdeError` as whole-operation failures (IC-ADR-001
  §2 partial-failure rule: auth failure is never a per-id outcome).

### 4. Materialisation (Tier C)

Backends that drive CLIs or need files get internal helpers, never a public contract: secure temp
files (0600, private dir), env injection (`X509_USER_PROXY`, HTCondor `_CONDOR_*`/SciTokens env),
scoped to the operation and cleaned up deterministically (the backend's context-manager lifecycle
from IC-ADR-001 §2 is the natural cleanup boundary). Credential values never appear in logs or
exception messages.

### 5. Delegation is backend mechanics (Tier C)

ARC proxy delegation (CSR → sign with the provider-supplied `X509Proxy` → upload; renew before
expiry; reuse across submissions where valid) is `ARCBackend`-internal. The provider supplies the
proxy; everything downstream — delegation ids, renewal, `AlwaysIncludeProxy`-style pairing with a
token — is invisible to the consumer.

### 6. Boundaries

- **SSH keys are transport configuration**, not rotating credentials: long-lived, file/agent-based,
  no audience. They stay in `SSHTransport` config; revisit only if key rotation becomes a real
  requirement.
- **Payload credentials are pilot-side.** Renewing the proxy *of the running payload* (DIRAC's
  `_monitorProxy`, `GENERIC_PILOT` branch) belongs to the severed pilot/runner domain, not here.
- **Issuance is the consumer's.** interCEde never holds refresh tokens, client secrets, or CA
  material; it asks a provider and uses what it gets.
- **Cloud provider auth** (Libcloud driver keys, the `cloud.auth` ini) is `CloudBackend` internal
  config; whether it adopts the provider model is deferred with the Cloud backend itself.

### 7. Usage sketches (informative)

**(a) The consumer implements the provider once; every backend pulls from it.** A DiracX
submission task wires its token/proxy machinery into one object and never polices freshness
again (this is the code that today exists twice, in `SiteDirector._setCredentials` and
`WMSUtilities.setPilotCredentials`):

```python
class DiracXPilotCredentials:                       # consumer-side; satisfies CredentialProvider
    async def get(self, req: CredentialRequirements) -> CredentialSet:
        creds = []
        if BearerToken in req.kinds:                # mint scoped to what the backend declared
            tok = await token_service.mint(audience=req.audience, scopes=req.scopes)
            creds.append(BearerToken(tok.value, tok.expires_at))
        if X509Proxy in req.kinds:                  # gProxyManager lineage, behind the provider
            pem = await proxy_store.download(pilot_dn, pilot_group, lifetime=86400)
            creds.append(X509Proxy(pem, expires_at=proxy_expiry(pem)))
        return CredentialSet(creds)

backend = registry.backend(
    {"type": "htcondor-ce", "endpoint": "ce01.example.org:9619", ...},
    credentials=DiracXPilotCredentials(),
)
async with backend:                                 # lifecycle from IC-ADR-001 §2
    sub = await backend.submit(spec, count=50)
    # before the operation the backend compared its cached CredentialSet against
    # backend.credential_requirements and awaited provider.get(...) only if stale
```

**(b) What the HTCondorCE backend declares, and what it does with the set (Tier C).** The
requirements make the Motivation-7 pairing explicit and typed; the materialisation reproduces
DIRAC's mechanics without the caller knowing any of it:

```python
backend.credential_requirements == CredentialRequirements(
    kinds=frozenset({BearerToken, X509Proxy}),      # all-of: token for the channel,
    audience="ce01.example.org:9619",               # proxy for site-side VOMS/APEL accounting
)
# internally, per operation:
#   BearerToken -> 0600 temp file; _CONDOR_SEC_CLIENT_AUTHENTICATION_METHODS=SCITOKENS,
#                  _CONDOR_SCITOKENS_FILE=<file>
#   X509Proxy   -> 0600 temp file; X509_USER_PROXY=<file>, referenced by the submit
#                  description's `use_x509userproxy = true`
# both cleaned up at the operation/lifecycle boundary; values never logged
```

**(c) Integration stacks and standalone use — static files, no issuance machinery.** The
IC-ADR-002 stacks mint ephemeral credentials into the shared volume; the test harness (or any
non-DiracX user) wraps them statically:

```python
provider = StaticCredentials(CredentialSet([
    BearerToken(Path("credentials/htcondor/idtoken").read_text().strip(), expires_at=None),
]))
backend = registry.backend({"type": "htcondor-ce", ...}, credentials=provider)
```

## Rationale

- **Provider-pull over mutating setters.** Setters put hidden state on cached objects and force
  every caller to police freshness (two DIRAC copies prove the cost). A pull model puts the
  *check* in one library-side place while leaving the *source and policy* in consumer code — the
  same mechanism/policy line IC-ADR-001 draws for throttling.
- **Declared requirements over tag sniffing.** `Tag: Token` conflates scheduling metadata with an
  auth capability and is invisible to type checkers and tooling. A typed declaration is
  enumerable, testable, and lets the conformance suite assert that a backend's declared kinds are
  the ones it actually uses.
- **`CredentialSet` over single credential.** ARC's token+proxy pairing is a first-party
  requirement, not an edge case; modelling it from day one avoids a v2 of the provider protocol.
- **Immutability.** Frozen values make "refresh" a *replacement*, never an in-place mutation —
  cache-safe and race-free under concurrent bulk operations.

## Rejected Ideas

- **DIRAC-style `set_credential()` mutators.** Hidden state on cached backends; freshness policy
  smeared across callers; the incumbent model this ADR exists to replace.
- **Backend-driven issuance/refresh** (backend holds a refresh token or talks to the IdP).
  Couples the library to DiracX/IdP specifics, embeds secrets in the library, and moves policy
  inside — the opposite of the IC-ADR-001 §9 mechanism/policy split.
- **Credentials in the `SubmissionSpec`** (name provisional — IC-ADR-001 Open Issues). Auth is
  per-backend/channel, not per-job; putting it on
  the spec would force every task (status, fetch, kill) to re-thread it and would leak payload vs
  backend credential confusion back in.
- **A single `Credential` per backend (no set).** Breaks on ARC's token+proxy pairing.
- **Keeping the `Tag: Token[:vo]` opt-in.** Stringly-typed, CS-coupled, invisible to types and
  tooling.
- **An interCEde-owned credential store/daemon.** interCEde is stateless (IC-ADR-001 §1); caching
  beyond the in-memory current set is the consumer's business.

## Open Issues

- **Freshness margin.** Fixed library default vs per-backend/per-provider configuration; ARC
  delegation renewal wants a larger margin than a bearer-token header.
- **Requirements granularity.** Whether `CredentialRequirements` stays per-backend or needs
  per-operation variance (e.g. a backend whose *fetch* endpoint needs a different scope than
  *submit*). Start per-backend; split only on evidence.
- **Proxy representation.** Raw PEM bytes (current spec) vs a `cryptography` object; and whether
  delegation needs key material the consumer must supply alongside the chain (DIRAC signs the
  delegation CSR with the proxy's own key — implies the provider hands over key+chain, which
  `X509Proxy.pem` as "full chain" must be explicit about).
- **Multi-VO backends.** One backend instance serving several VOs would need per-VO credential
  sets; today DIRAC instantiates per-queue/VO CEs, and interCEde's per-backend provider assumes
  the same. Confirm with DiracX's multi-VO design.
- **DiracX alignment.** The provider implementation on the DiracX side (token service, scopes,
  pilot-credential flows) — tracked with the DiracX transition ADRs, not here.
- **Proxy-for-accounting lifetime.** HTCondorCE's proxy requirement is a WLCG-transition artifact
  (sites attribute usage via the proxy's VOMS attributes, the APEL pipeline). When sites account
  on token claims instead, the backend's declared requirements shrink to token-only — and because
  requirements are *data*, that is a backend-version/configuration change, not an API break.
  Track the WLCG token-transition timeline before hard-coding the pairing as permanent.
- **Conformance coverage.** IC-ADR-002 already plans token-vs-proxy configuration axes for the
  ARC stack; extend the conformance suite with a "requirements honesty" check (backend declares X,
  suite verifies it authenticates with exactly X).
