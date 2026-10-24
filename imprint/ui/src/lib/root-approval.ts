/** A root the server refused a job for: POST /api/commands answers an ingest
 * or refresh outside the workspace's roots with 409 `root_not_approved`. */
export type RootRequest = {
  workspace: string;
  root: string;
  /** Other workspaces sharing this root; approving it confirms the sharing. */
  overlaps: string[];
};

export type Pending = RootRequest & { id: number; settle: (approved: boolean) => void };

// Requests wait in line for the one RootApprovalDialog the layout mounts.
let queue: Pending[] = [];
let nextId = 0;
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

/** Ask the user to approve `request.root`. Resolves true once the dialog has
 * added the root, false when the user declines. */
export function requestRootApproval(request: RootRequest): Promise<boolean> {
  return new Promise((settle) => {
    queue = [...queue, { ...request, id: nextId++, settle }];
    emit();
  });
}

export function subscribeRootApproval(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function currentRootApproval(): Pending | null {
  return queue[0] ?? null;
}

export function settleRootApproval(id: number, approved: boolean) {
  const request = queue.find((pending) => pending.id === id);
  if (!request) return;
  queue = queue.filter((pending) => pending !== request);
  request.settle(approved);
  emit();
}
