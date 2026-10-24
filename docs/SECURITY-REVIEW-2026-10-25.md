# Three-set security review — 0.4.1

Three independent Codex reviewers examined separate, complementary scopes. This is
three review sets, not three complete penetration tests of the entire product.

| Set | Scope | Adjudicated result |
| --- | --- | --- |
| 1 | Encrypted storage, OS ownership/ACLs, authentication, E2EE state and approval boundaries | No demonstrated new vulnerability in the bounded review; synthetic ownership and executable-resolution probes passed. |
| 2 | MCP authorization, delegation, pause/revocation, operation ledger, media and untrusted model input | No demonstrated new vulnerability in the bounded review; additional synthetic HTTP/audience/revocation probes accompany regression verification. |
| 3 | Runtime installation, subprocesses, supervision and dependency/packaging metadata | One confirmed High CWE-426 issue; fixed across the matching application callers and independently reproduced before and after. |

## Confirmed issue and repair

An attacker able to place a same-named executable in the owner's working directory
could have Windows select it when the owner invoked a legitimate operation. The
attacker does not need prior execution as the owner: the trusted invocation supplies
that execution opportunity. A harmless copied OS binary returned a distinctive exit
code through the old scheduler-command path. An SDK bridge probe also demonstrated
selection of a planted Node executable. Neither probe contacted LINE, opened an
account, changed a scheduled task or ran a malicious payload.

The repair resolves `schtasks.exe` and `taskkill.exe` from the OS-reported system
directory. uv, Node and Codex are resolved only from absolute owner-configured PATH
directories, then invoked by absolute path. The SDK receives the explicit Node path;
Codex bootstrap and inference share one resolved executable. Missing dependencies
fail closed. The same scheduler probe now reaches the real OS utility, which rejects
the invalid test arguments instead of executing the planted binary.

The build backend is pinned to the previously used hatchling 1.32.4. This is supply
chain reproducibility hardening, not a claim that an unpinned backend was compromised.

## Release and scope

The fixed entry, account receiver supervisor and documented management entry must
also be updated; switching only the backend is insufficient. See
[the Windows upgrade instructions](WINDOWS.md). Keep the account database, keys,
delegation and operation ledger through the update. Old immutable releases are
recovery evidence, not the default security rollback target.

Validation results and operating-system limitations are recorded in [STATUS.md](STATUS.md)
and the associated pull request. No demonstrated Critical/High finding remained in
the reviewed patch. This statement is bounded to the examined application paths.
Absolute owner-selected executable locations remain trusted configuration. The
protocol dependency's cryptography and full source, LINE infrastructure, sustained
adversarial load, a fresh Windows user, OS reboot/logoff and a fresh macOS real-account
session were not audited here. Model isolation flags were checked synthetically;
this review did not execute hostile prompts against a real model CLI. A conditional
POSIX pre-existing-file permission concern had no demonstrated attacker reachability
under the default private account/runtime directories and was not called a confirmed
vulnerability. These limits are not reported as passed tests.
