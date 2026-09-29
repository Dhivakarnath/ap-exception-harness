import { Link } from "react-router-dom";
import { PageBody } from "@/components/layout/PageHeader";

/** A quiet 404 for unknown routes — a way back, not a dead end. */
export function NotFoundPage() {
  return (
    <PageBody>
      <div className="grid place-items-center py-24 text-center">
        <div className="max-w-sm">
          <p className="font-mono text-sm text-ink-subtle">404</p>
          <h1 className="mt-2 text-lg font-semibold text-ink">Page not found</h1>
          <p className="mt-1 text-sm text-ink-muted">
            That route doesn&apos;t exist in the platform.
          </p>
          <Link
            to="/"
            className="mt-4 inline-flex rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-brand-contrast hover:brightness-110"
          >
            Back to runs
          </Link>
        </div>
      </div>
    </PageBody>
  );
}
