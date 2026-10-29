#!/usr/bin/env bash
# Disposable Apple silicon runner supervisor (ADR 0115). Runs as a login
# LaunchAgent. When a job for RUNNER_MATCH_LABEL is queued, it clones the golden
# VM, registers a just-in-time runner inside it that accepts exactly one job,
# waits for that job to end and deletes the VM. The GitHub token stays on the
# host; only the single-use JIT configuration enters the guest, over stdin.
set -uo pipefail
# shellcheck source=common.sh
. "$(dirname "$0")/common.sh"

POLL_SECONDS="${IMPRINT_VM_POLL_SECONDS:-30}"
# A VM whose runner has not picked up a job by then is discarded.
IDLE_SECONDS="${IMPRINT_VM_IDLE_SECONDS:-600}"
JOB_SECONDS="${IMPRINT_VM_JOB_SECONDS:-10800}"

queued_jobs() {
  local status id
  for status in queued in_progress; do
    gh api "repos/$REPO/actions/runs?status=$status&per_page=100" --jq '.workflow_runs[].id' || return 1
  done | while read -r id; do
    gh api "repos/$REPO/actions/runs/$id/jobs?filter=latest&per_page=100" \
      --jq "[.jobs[] | select(.status == \"queued\" and (.labels | index(\"$RUNNER_MATCH_LABEL\")))] | length"
  done | awk '{ n += $1 } END { print n + 0 }'
}

# VMs and offline runner registrations left by a crash or a reboot mid-job.
remove_leftovers() {
  local vm id
  "$TART" list --source local --quiet 2>/dev/null | grep "^$VM_PREFIX-" | while read -r vm; do
    log "deleting leftover VM $vm"
    "$TART" stop "$vm" >/dev/null 2>&1
    "$TART" delete "$vm"
  done
  gh api "repos/$REPO/actions/runners?per_page=100" \
    --jq ".runners[] | select(.status == \"offline\" and (.name | startswith(\"$RUNNER_NAME_PREFIX-\"))) | .id" |
    while read -r id; do
      log "removing offline runner registration $id"
      gh api -X DELETE "repos/$REPO/actions/runners/$id" >/dev/null
    done
}

run_one_job() {
  local stamp vm name jit runner_id vm_pid exec_pid started busy=false label
  local -a label_args=()
  stamp=$(date +%Y%m%d-%H%M%S)
  vm="$VM_PREFIX-$stamp"
  name="$RUNNER_NAME_PREFIX-$stamp"
  IFS=, read -ra labels <<<"$RUNNER_LABELS"
  for label in "${labels[@]}"; do label_args+=(-f "labels[]=$label"); done

  jit=$(gh api -X POST "repos/$REPO/actions/runners/generate-jitconfig" \
    -f name="$name" -F runner_group_id=1 -f work_folder=_work "${label_args[@]}") || return 1
  runner_id=$(jq -r .runner.id <<<"$jit")

  log "starting $vm for runner $name ($runner_id)"
  if ! "$TART" clone "$GOLDEN_VM" "$vm"; then
    gh api -X DELETE "repos/$REPO/actions/runners/$runner_id" >/dev/null 2>&1
    return 1
  fi
  "$TART" run --no-graphics --no-audio "$vm" >/dev/null 2>&1 &
  vm_pid=$!

  if wait_for_guest "$vm" 300; then
    jq -r .encoded_jit_config <<<"$jit" |
      "$TART" exec -i "$vm" /bin/bash -c \
        "cfg=\$(cat); cd '$GUEST_RUNNER_DIR' && exec ./run.sh --jitconfig \"\$cfg\"" &
    exec_pid=$!
    unset jit
    # Keep the laptop from idle-sleeping mid-job; closing the lid still sleeps it.
    caffeinate -i -w "$exec_pid" &
    started=$SECONDS
    while kill -0 "$exec_pid" 2>/dev/null; do
      sleep 15
      if [[ $busy != true ]]; then
        busy=$(gh api "repos/$REPO/actions/runners/$runner_id" --jq .busy 2>/dev/null || echo false)
        if [[ $busy != true ]] && ((SECONDS - started > IDLE_SECONDS)); then
          log "$name received no job in ${IDLE_SECONDS}s; discarding it"
          break
        fi
      fi
      if ((SECONDS - started > JOB_SECONDS)); then
        log "$name exceeded ${JOB_SECONDS}s; discarding it"
        break
      fi
    done
    kill "$exec_pid" 2>/dev/null
    wait "$exec_pid" 2>/dev/null
    log "runner $name finished after $((SECONDS - started))s"
  fi

  "$TART" stop "$vm" --timeout 30 >/dev/null 2>&1
  kill "$vm_pid" 2>/dev/null
  wait "$vm_pid" 2>/dev/null
  "$TART" delete "$vm" || log "could not delete $vm"
  # An ephemeral runner removes itself after its job; this covers the others.
  gh api -X DELETE "repos/$REPO/actions/runners/$runner_id" >/dev/null 2>&1
  return 0
}

"$TART" list --source local --quiet | grep -qx "$GOLDEN_VM" || {
  log "golden VM $GOLDEN_VM is missing; run prepare-image.sh"
  exit 1
}
GH_TOKEN=$(github_token) && export GH_TOKEN
remove_leftovers
log "watching $REPO for $RUNNER_MATCH_LABEL jobs"
while true; do
  if GH_TOKEN=$(github_token); then
    export GH_TOKEN
    if count=$(queued_jobs) && ((count > 0)); then
      log "$count queued job(s)"
      run_one_job || log "could not start a runner VM"
      continue
    fi
  else
    log "no GitHub token available"
  fi
  sleep "$POLL_SECONDS"
done
