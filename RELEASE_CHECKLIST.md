# Local release checklist

Nothing in this checklist authorises publication. Do not push, tag, create a GitHub release, upload to HACS, or disclose security material until the repository owner gives explicit approval.

## 1. Preserve and review

- [ ] Work on a dedicated local branch and confirm unrelated user changes are still present.
- [ ] Review `git diff --check`, the complete diff, and the untracked-file list.
- [ ] Confirm no PCAP, APK, token, credential, complete device identifier, exact household IP, email address or customer data is included.
- [ ] Confirm logs, diagnostics, examples and screenshots use irreversible redaction.

## 2. Reproducible validation

- [ ] Current environment installs only from `requirements-test.txt`.
- [ ] Minimum Home Assistant 2024.6.4 environment installs only from `requirements-test-minimum.txt`.
- [ ] `python -m ruff check .` passes.
- [ ] `python -m ruff format --check .` passes.
- [ ] `python -m pytest -q` passes in both environments.
- [ ] `python -m compileall -q custom_components tests` passes.
- [ ] All JSON files parse and all workflow YAML files load.
- [ ] Hassfest and HACS validation pass on the exact candidate commit.
- [ ] CodeQL and dependency review have no unresolved relevant finding.

## 3. Owner-controlled device test

- [ ] With no allowlist and no enrollment, verify that no TCP port is opened and a Repair is shown.
- [ ] Open enrollment and verify that it expires after five minutes.
- [ ] Verify that an unknown owned test client is recorded only as a candidate and creates no entity.
- [ ] Approve the expected candidate and verify that a wrong identifier and wrong bound IP remain rejected.
- [ ] Verify read-only telemetry before command approval.
- [ ] Approve the observed firmware profile, send one harmless owned-device command, and confirm the resulting state.
- [ ] Verify duplicate and burst commands are bounded.
- [ ] Verify diagnostics contain no full identifier, exact peer IP, raw frame or credential.
- [ ] Confirm firewall policy allows only the fan network to TCP 11000 and blocks direct fan WAN access.

## 4. Candidate packaging

- [ ] Update the manifest version, README badge and release notes together.
- [ ] Build the candidate from a clean, reviewed local commit without generated caches.
- [ ] Record SHA-256 hashes for the source archive and integration files.
- [ ] Install that exact archive into a disposable Home Assistant test instance and repeat the smoke test.
- [ ] Prepare rollback instructions and retain the previously working integration directory or backup.

## 5. Publication gate — explicit approval required

- [ ] Repository owner has reviewed the final diff, test evidence and release notes.
- [ ] Repository owner has explicitly approved GitHub publication.
- [ ] Create a signed annotated tag only after approval.
- [ ] Publish hashes and concise security-impact notes without exploit details or household data.
- [ ] Monitor private security reports and rollback signals after release.
