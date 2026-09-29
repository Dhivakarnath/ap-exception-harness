import { useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import {
  UploadProgressOverlay,
  type UploadPhase,
} from "@/components/UploadProgressOverlay";
import { UploadTenantSelector } from "@/components/UploadTenantSelector";
import type { DemoTenantId } from "@/lib/tenants";
import { getDeepTracePreference } from "@/lib/deepTrace";
import { uploadRun } from "@/transport/api";

const ACCEPTED_TYPES = "application/pdf,image/png,image/jpeg,image/tiff";

/**
 * Upload control for live document runs. Hands the new run id back so the
 * workspace selects and streams it like any other trigger.
 */
export function Toolbar({
  onStarted,
  onError,
}: {
  onStarted: (runId: string) => void;
  onError: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [uploadTenant, setUploadTenant] = useState<DemoTenantId>("retail-demo");
  const [allowDuplicates, setAllowDuplicates] = useState(false);
  const [uploadFileName, setUploadFileName] = useState<string | null>(null);
  const [uploadPhase, setUploadPhase] = useState<UploadPhase>("uploading");
  const [uploadPercent, setUploadPercent] = useState<number | null>(0);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const onFileChosen = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-choosing the same file next time
    if (!file) return;
    setBusy(true);
    setUploadFileName(file.name);
    setUploadPhase("uploading");
    setUploadPercent(0);
    try {
      const { run_id } = await uploadRun(file, {
        tenant: uploadTenant,
        allowDuplicate: allowDuplicates,
        deepTrace: getDeepTracePreference(),
        onUploadProgress: setUploadPercent,
        onPhase: (phase) => {
          setUploadPhase(phase === "uploading" ? "uploading" : "processing");
          if (phase === "processing") setUploadPercent(null);
        },
      });
      setUploadPhase("starting");
      onStarted(run_id);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Failed to upload the document");
    } finally {
      setBusy(false);
      setUploadFileName(null);
      setUploadPercent(null);
    }
  };

  return (
    <>
      {uploadFileName ? (
        <UploadProgressOverlay
          fileName={uploadFileName}
          phase={uploadPhase}
          uploadPercent={uploadPercent}
        />
      ) : null}
    <div className="flex flex-wrap items-center gap-3">
      <Button disabled={busy} onClick={() => fileInputRef.current?.click()}>
        Upload an invoice…
      </Button>
      <UploadTenantSelector value={uploadTenant} onChange={setUploadTenant} />
      <input
        ref={fileInputRef}
        type="file"
        accept={ACCEPTED_TYPES}
        className="hidden"
        aria-label="Invoice file"
        tabIndex={-1}
        onChange={(e) => void onFileChosen(e)}
      />
      <label
        className="inline-flex cursor-pointer items-center gap-1.5 rounded-md border border-transparent px-2 py-1.5 text-xs text-ink-muted transition-colors hover:border-border hover:bg-surface-2/60 hover:text-ink"
        title="When enabled, uploading the same file again starts a new run instead of being rejected."
      >
        <input
          type="checkbox"
          checked={allowDuplicates}
          onChange={(e) => setAllowDuplicates(e.target.checked)}
          className="size-3.5 accent-brand"
        />
        allow duplicate uploads
      </label>
    </div>
    </>
  );
}
