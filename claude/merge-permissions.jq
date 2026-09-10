# Merge our guardrails into a user's settings.json.
#
# Two rules learned the hard way:
#  1. ORDER IS THE USER'S. An earlier version used `unique`, which alphabetically
#     re-sorted the whole list — a user's "Read", "Grep", "Glob" silently moved from
#     the top to the bottom of a longer list and read as deletion. Never re-sort;
#     keep their entries where they put them and append only what is new.
#  2. RETIRE OUR OWN DEAD RULES. A pure union can never drop anything, so rules for
#     commands we no longer ship (caos, rig, cai) accumulated forever. We remove
#     rules from $retired — strings only this tool ever wrote — and nothing else.
def dedup: reduce .[] as $x ([]; if index([$x]) then . else . + [$x] end);

def merge_list($existing; $ours; $dead):
  (($existing // []) - $dead | dedup) as $keep
  | $keep + (($ours // []) - $keep);

  .permissions = (.permissions // {})
| .permissions.defaultMode = (.permissions.defaultMode // $add[0].permissions.defaultMode)
| .permissions.allow = merge_list(.permissions.allow; $add[0].permissions.allow; $retired[0].allow)
| .permissions.deny  = merge_list(.permissions.deny;  $add[0].permissions.deny;  $retired[0].deny)
