# Lab Runbook — first deployment in the composer stack

End-to-end test of the four jobs against a nautobot-composer lab
(Nautobot 3.x). Everything here is lab-only; nothing touches production.

## 1. Install into the composer image

In the composer repo:

1. Add the library to the image (requirements-3.x.txt or a bind mount for
   iteration):

   ```
   nautobot-design-template-factory @ git+https://github.com/bforejt/nautobot-design-template-factory.git
   ```

   (or `pip install -e /source/nautobot-design-template-factory` in a dev
   override — the library has no Nautobot dependency of its own.)

2. Rebuild and start: `docker compose build && docker compose up -d`.

## 2. One-time Nautobot setup

1. **Git repository data source** → this repo, provides *jobs*. Sync, then
   enable the four jobs (Capture / Deploy / Verify / Teardown).
2. **Custom field** `provisioned_from` (type: text), assigned to exactly the
   content types the deploy job stamps (components/through-rows cascade and
   are not stamped): dcim | location, rack group, rack, power panel, power
   feed, device, cable; ipam | VLAN group, VLAN, prefix, IP address.
   Deploy preflight verifies both existence and this coverage.
3. (Optional but recommended) a dedicated job queue for deploy/teardown.

## 3. Build the golden fixture site

```bash
docker compose exec nautobot nautobot-server nbshell --plain < fixtures/build_fixture_site.py
```

Creates `DAL01` with the hostile cases (non-site-coded rack, group-less
VLANs, removed template interface, LAG, role-flagged IP, OOB namespace,
`{{ malicious }}` literals, power cabling).

## 4. The loop

1. **Capture Site Template** — location `DAL01`, site code `DAL01`. Review
   the lint report; download `site-spec-dal01.json` + the draft param map.
   (For the first pass, the blessed equivalent is already committed at
   `templates/branch-small/`.)
2. **Deploy Standard Site** — template `branch-small`, site code `AUS01`,
   site name `Austin Branch`, parent = `South Central` (the Region the
   fixture creates), supernets `{"supernet_1": "10.20.0.0/16"}`.
   **Dry run first** (default): review `resolved-plan-aus01.json`, then
   re-run with dry run unchecked.
   Note: the committed template carries an EMPTY DeviceType fingerprint
   (preflight skips the drift check). After your first capture run, replace
   `templates/branch-small/` with the real capture output — gate 4 depends
   on it, and a captured spec is the only fully-verifiable template.
3. **Verify Deployed Site** — location `Austin Branch`, template
   `branch-small`, site code `AUS01`, same supernets. Pass = 0 differences.
4. **Deploy a second site** (`HOU01`, different supernet) — both must coexist
   (the debate's gate #3), then **re-run Capture against DAL01** and diff the
   spec against `templates/branch-small/spec.json` (gate #4: source
   untouched).
5. **Teardown Deployed Site** — stamp `branch-small@1/AUS01`, dry run first,
   then confirm + real run. Site should vanish completely; re-run Verify to
   confirm it fails cleanly.

## 5. What we're measuring (spike gates, adapted)

| Gate | Pass bar |
|---|---|
| 1. Scale | deploy completes inside job time limits at fixture scale; note per-family timings from the execution report for extrapolation |
| 2. Mechanisms | every hostile case round-trips: removals replayed, LAG links, role flags, OOB namespace IP, power + cross-family cabling |
| 3. Coexistence | two sites from one template, zero collisions |
| 4. Source integrity | re-capture of DAL01 diffs clean against the committed template spec |

Failures land as issues against the executor/walker — this is exactly what
the lab pass is for.
