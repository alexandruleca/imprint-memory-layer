import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

export function DashboardSkeleton() {
  return (
    <div className="p-8 space-y-8">
      <Skeleton className="h-9 w-48" />
      <div className="grid grid-cols-4 gap-5">
        {Array.from({ length: 4 }).map((_, i) => (
          <Card key={i} className="relative overflow-hidden">
            <div className="absolute left-0 top-0 bottom-0 w-1 bg-muted-foreground/20" />
            <CardHeader className="pb-2 pl-5">
              <Skeleton className="h-4 w-24" />
            </CardHeader>
            <CardContent className="pl-5">
              <Skeleton className="h-9 w-20" />
            </CardContent>
          </Card>
        ))}
      </div>
      <div className="grid grid-cols-2 gap-6">
        {Array.from({ length: 4 }).map((_, i) => (
          <Card key={i}>
            <CardHeader>
              <Skeleton className="h-4 w-20" />
            </CardHeader>
            <CardContent className="space-y-2">
              {Array.from({ length: 5 }).map((_, j) => (
                <div key={j} className="flex items-center gap-2">
                  <Skeleton className="h-3 w-20" />
                  <Skeleton className="h-4 flex-1" />
                  <Skeleton className="h-3 w-10" />
                </div>
              ))}
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}

export function ListSkeleton({ count = 8 }: { count?: number }) {
  return (
    <div className="space-y-1.5 pr-4">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="flex items-center justify-between p-2 rounded">
          <div className="flex items-center gap-2">
            <Skeleton className="w-3 h-3 rounded-full" />
            <Skeleton className="h-4 w-28" />
          </div>
          <Skeleton className="h-5 w-12 rounded-full" />
        </div>
      ))}
    </div>
  );
}

export function CardGridSkeleton({ count = 6 }: { count?: number }) {
  return (
    <div className="space-y-2 pr-4">
      {Array.from({ length: count }).map((_, i) => (
        <Card key={i}>
          <CardContent className="p-3 space-y-2">
            <Skeleton className="h-4 w-3/4" />
            <div className="flex gap-1">
              <Skeleton className="h-5 w-16 rounded-full" />
              <Skeleton className="h-5 w-14 rounded-full" />
              <Skeleton className="h-5 w-12 rounded-full" />
            </div>
            <Skeleton className="h-3 w-full" />
            <Skeleton className="h-3 w-2/3" />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

export function Spinner({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg
      className={`animate-spin ${className}`}
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
    >
      <circle
        className="opacity-25"
        cx="12" cy="12" r="10"
        stroke="currentColor" strokeWidth="4"
      />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
      />
    </svg>
  );
}

export function GraphSkeleton() {
  return (
    <div className="flex-1 bg-[#0a0a1a] flex items-center justify-center">
      <div className="space-y-6 flex flex-col items-center">
        <div className="relative">
          {[0, 1, 2, 3, 4].map((i) => (
            <Skeleton
              key={i}
              className="absolute rounded-full bg-muted-foreground/10"
              style={{
                width: 30 + i * 15,
                height: 30 + i * 15,
                top: Math.sin(i * 1.2) * 60 - 40,
                left: Math.cos(i * 1.2) * 80 - 40,
              }}
            />
          ))}
        </div>
        <Spinner className="w-6 h-6 text-muted-foreground mt-24" />
        <p className="text-xs text-muted-foreground">Loading graph data...</p>
      </div>
    </div>
  );
}

export function ChatSessionsSkeleton({ count = 5 }: { count?: number }) {
  return (
    <div className="p-2 space-y-1">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="p-2 rounded">
          <Skeleton className="h-3.5 w-full" />
        </div>
      ))}
    </div>
  );
}

export function SettingsSkeleton() {
  return (
    <div className="p-8 space-y-6">
      <Skeleton className="h-8 w-32" />
      <Card>
        <CardHeader><Skeleton className="h-4 w-24" /></CardHeader>
        <CardContent>
          <div className="flex gap-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-8 w-20 rounded" />
            ))}
          </div>
        </CardContent>
      </Card>
      {Array.from({ length: 3 }).map((_, i) => (
        <Card key={i}>
          <CardHeader><Skeleton className="h-4 w-20" /></CardHeader>
          <CardContent className="space-y-3">
            {Array.from({ length: 4 }).map((_, j) => (
              <div key={j} className="flex items-center gap-4 py-1">
                <Skeleton className="h-8 w-56" />
                <Skeleton className="h-6 flex-1" />
                <Skeleton className="h-5 w-14 rounded-full" />
              </div>
            ))}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
