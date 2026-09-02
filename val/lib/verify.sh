#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

val_verify() {
  local browser_result="$1" checklist="$2" output="$3" temporary checklist_temporary
  [ -f "$browser_result" ] || { val_die 3 "browser result missing: $browser_result"; return 3; }
  if [ ! -f "$checklist" ]; then
    mkdir -p "$(dirname "$checklist")"
    printf '%s\n' '[]' > "$checklist"
  fi
  temporary="$(mktemp "$(dirname "$output")/.results.XXXXXX")"
  jq -n --slurpfile browser "$browser_result" --slurpfile criteria "$checklist" '
    def severity($id):
      if $id == "overflow" then "P0"
      elif $id == "tap-target" then "P2"
      else "P1" end;
    [ $browser[0].shots[] as $shot
      | $shot.checks[]
      | select(.status != "pass")
      | {
          criterionId: ("default:" + .id),
          checkId: .id,
          status,
          severity: severity(.id),
          shot: $shot.path,
          route: $shot.route,
          viewport: $shot.viewport,
          theme: $shot.theme,
          detail,
          file: null,
          line: null
        }
    ] as $browserFindings
    | [ $criteria[0][] as $criterion
        | ([ $browser[0].shots[].checks[]
            | select(.id == ($criterion.checkId // "") or (.id | startswith(($criterion.checkId // "") + "-"))) ]) as $checks
        | $criterion + {status:
            (if $criterion.active == false then $criterion.status
            elif $criterion.method == "manual" then "manual"
            elif any($checks[]; .status == "blocked") then "fail"
            elif any($checks[]; .status == "fail") then "fail"
            elif any($checks[]; .status == "manual") then "manual"
            elif ($checks | length) > 0 and all($checks[]; .status == "pass") then "pass"
            else $criterion.status end)}
      ] as $resolvedCriteria
    | [ $resolvedCriteria[]
        | select(.active != false and (.method == "manual" or (.status == "manual" and (.checkId // "") == "")))
        | . as $criterion
        | ([ $browser[0].shots[] | select(.route == $criterion.target) ][0] // $browser[0].shots[0] // null) as $evidence
        | {criterionId:.id,checkId:(.checkId // "manual"),status:"manual",severity:"P2",shot:($evidence.path // ""),route:($evidence.route // .target),viewport:($evidence.viewport // [0,0]),theme:($evidence.theme // "n/a"),detail:.assert,file:null,line:null}
      ] as $manualFindings
    | ($browserFindings + $manualFindings) as $findings
    | {
        protocolVersion: 1,
        shots: $browser[0].shots,
        findings: $findings,
        criteria: $resolvedCriteria,
        summary: {
          pass: ([ $resolvedCriteria[] | select(.active != false and .status == "pass") ] | length),
          fail: ([ $resolvedCriteria[] | select(.active != false and .status == "fail") ] | length),
          manual: ([ $resolvedCriteria[] | select(.active != false and .status == "manual") ] | length),
          blocked: ([ $browserFindings[] | select(.status == "blocked") ] | length)
        }
      }
  ' > "$temporary"
  mv "$temporary" "$output"
  checklist_temporary="$(mktemp "$(dirname "$checklist")/.checklist.XXXXXX")"
  jq '.criteria' "$output" > "$checklist_temporary"
  mv "$checklist_temporary" "$checklist"
}
