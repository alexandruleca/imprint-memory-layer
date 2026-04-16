import { Spinner } from "@/components/loaders";

export function ThinkingDots() {
  return (
    <div className="max-w-[80%] rounded-lg p-3 bg-card border border-border flex items-center gap-1.5">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="block w-2 h-2 rounded-full bg-muted-foreground"
          style={{
            animation: "thinking-dot 1.4s ease-in-out infinite",
            animationDelay: `${i * 0.2}s`,
          }}
        />
      ))}
    </div>
  );
}

export function ToolWorkingIndicator({ toolName }: { toolName: string }) {
  const label =
    toolName === "search" ? "Searching memory..." :
    toolName === "kg_query" ? "Querying knowledge graph..." :
    toolName === "status" ? "Checking status..." :
    toolName === "wake_up" ? "Loading context..." :
    `Running ${toolName}...`;

  return (
    <div className="max-w-[80%] rounded-lg px-3 py-2 bg-card border border-border flex items-center gap-2 text-xs text-muted-foreground">
      <Spinner className="w-3.5 h-3.5" />
      <span>{label}</span>
    </div>
  );
}

export function TypingCursor() {
  return (
    <span
      className="inline-block w-0.5 h-4 bg-foreground align-text-bottom ml-0.5"
      style={{ animation: "blink-cursor 1s infinite" }}
    />
  );
}
