import type { DeviceIdentity, PathContext } from "@/lib/types";

export function DeviceProvenance({ origin, paths }: { origin?: DeviceIdentity; paths?: PathContext }) {
  const advice = paths?.path_status === "outside-working-directory"
    ? "Open the project in your coding client's working directory before using these file references."
    : paths?.path_status === "local-file-missing" ? "Check the local checkout and reindex this source."
    : "Map the project to a local checkout to resolve file references.";
  return <div className="text-xs text-muted-foreground space-y-1 break-all">
    <p>Recorded on: {origin?.name || "Unknown origin (legacy)"}{origin?.id ? ` [${origin.id}]` : ""}</p>
    {paths && <>
      <p>This device: {paths.current_device.name} · working directory: <code>{paths.working_directory}</code></p>
      {paths.original_source_path && <p>Original path (reference): <code>{paths.original_source_path}</code></p>}
      {paths.local_source_path ? <p>Local path (verify before editing): <code>{paths.local_source_path}</code></p>
        : paths.original_source_path && <p>Path status: {paths.path_status.replaceAll("-", " ")}. Shared content remains available. {advice}</p>}
    </>}
  </div>;
}
