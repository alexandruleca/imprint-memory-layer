import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner as SpinnerIcon } from "@/components/ui/spinner";

/** The Home page's shape while its first summary loads. */
export function DashboardSkeleton() {
  return (
    <div className="space-y-6" role="status" aria-label="Loading dashboard">
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <Card className="gap-6 px-6 py-5">
          <div className="flex flex-col gap-6 md:flex-row md:items-end md:justify-between">
            <div className="space-y-2">
              <Skeleton className="h-4 w-20" />
              <Skeleton className="h-12 w-44" />
              <Skeleton className="h-4 w-36" />
            </div>
            <Skeleton className="h-24 w-full md:max-w-sm" />
          </div>
        </Card>
        <div className="grid grid-cols-2 gap-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Card key={i} className="gap-2 px-4 py-3.5">
              <Skeleton className="h-4 w-20" />
              <Skeleton className="h-7 w-16" />
            </Card>
          ))}
        </div>
      </div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <Card key={i}>
            <CardHeader>
              <Skeleton className="h-4 w-20" />
            </CardHeader>
            <CardContent className="space-y-2.5">
              {Array.from({ length: 5 }).map((_, j) => (
                <div key={j} className="flex items-center gap-3">
                  <Skeleton className="h-3 w-24" />
                  <Skeleton className="h-2 flex-1" />
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
