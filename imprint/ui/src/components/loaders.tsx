import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner as SpinnerIcon } from "@/components/ui/spinner";

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

/** The spinner beside text that already says what is loading, so it stays
 * out of the accessibility tree instead of announcing "Loading" again. */
export function Spinner({ className }: { className?: string }) {
  return <SpinnerIcon aria-hidden className={className} />;
}

export function GraphSkeleton() {
  return (
    <div className="flex-1 bg-background flex items-center justify-center">
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

export function SettingsSkeleton() {
  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 sm:p-8" aria-busy="true" aria-label="Loading settings">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-2">
          <Skeleton className="h-8 w-32" />
          <Skeleton className="h-4 w-80 max-w-full" />
        </div>
        <Skeleton className="h-8 w-full sm:w-72" />
      </div>
      <div className="grid gap-6 lg:grid-cols-[11rem_minmax(0,1fr)]">
        <div className="hidden space-y-2 lg:block">
          {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-7 w-full" />)}
        </div>
        <div className="space-y-6">
          {Array.from({ length: 3 }).map((_, i) => (
            <Card key={i} className="gap-0 pb-0">
              <CardHeader className="flex items-center gap-3">
                <Skeleton className="size-8 rounded-lg" />
                <div className="space-y-1.5">
                  <Skeleton className="h-4 w-28" />
                  <Skeleton className="h-3 w-48" />
                </div>
              </CardHeader>
              <CardContent className="mt-4 divide-y divide-border border-t border-border px-0">
                {Array.from({ length: 3 }).map((_, j) => (
                  <div key={j} className="flex items-center justify-between gap-6 px-4 py-4 sm:px-6">
                    <div className="space-y-1.5">
                      <Skeleton className="h-4 w-32" />
                      <Skeleton className="h-3 w-56 max-w-full" />
                    </div>
                    <Skeleton className="hidden h-8 w-80 sm:block" />
                  </div>
                ))}
              </CardContent>
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}
