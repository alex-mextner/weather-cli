Task: <!-- canonical task URL returned by task-cli; use Parent task: or Related task: when appropriate -->

<!--
  This template pairs with the "PR Checklist" gate (ci/pr-checklist/). Unchecked `- [ ]`
  boxes in the "Acceptance criteria" section BLOCK the merge until ticked. Copy this file
  to .github/pull_request_template.md and adapt the sections to your project.
  When .rig/pull-request-policy.json is enrolled, create the PR through
  python3 <agent-tools>/ci/ship/pr_metadata.py create -C <repo> --task <canonical-URL>
  --head <pushed-branch> --base <target-branch> --title <summary> --body-file <this-file>.
  <agent-tools> is the catalog selected by rig's agent_tools_source; see its
  ci/ship/pr-metadata.md. The command applies the template and verifies provider readback.
-->

## Motivation / what & why

<!-- What this changes and why it's worth doing. If user-facing, state the user value;
     if infrastructural (docs / CI / refactor), say so. -->

## Acceptance criteria

<!-- Explicit acceptance criteria as a task-list. Every box must be [x] before merge —
     an unchecked `- [ ]` fails the "PR Checklist" gate. -->

- [ ]
- [ ]

## Screenshots / proof (before -> after)

<!-- For any user-visible change, attach before/after images — the visible difference IS
     the proof. If a before/after is N/A (docs / CI / pure backend), write "N/A" and, if
     useful, attach a diagram instead. See ci/screenshots/ for an automated image gate. -->

## Notes for reviewers

<!-- Optional: anything a reviewer should know — risky areas, follow-ups, out-of-scope. -->
