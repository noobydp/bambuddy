import { useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, CheckCircle2, Loader2, Package, Upload } from 'lucide-react';
import { api, type ImportResponse } from '../api/client';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { Button } from './Button';
import { Card, CardContent, CardHeader } from './Card';

export function SlicerBundlesPanel() {
  const { hasPermission } = useAuth();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [result, setResult] = useState<ImportResponse | null>(null);

  const importMutation = useMutation({
    mutationFn: async (file: File) => {
      const formData = new FormData();
      formData.append('file', file);
      return api.importLocalPresets(formData);
    },
    onSuccess: (response) => {
      setResult(response);
      queryClient.invalidateQueries({ queryKey: ['localPresets'] });
      queryClient.invalidateQueries({ queryKey: ['slicerPresets'] });
      if (response.imported > 0) {
        showToast(`Imported ${response.imported} slicer preset${response.imported === 1 ? '' : 's'}.`);
      } else if (response.errors.length > 0) {
        showToast(response.errors[0], 'error');
      } else {
        showToast('No new presets were found in that bundle.', 'warning');
      }
    },
    onError: (error: Error) => {
      setResult(null);
      showToast(error.message, 'error');
    },
  });

  return (
    <Card>
      <CardHeader>
        <h3 className="text-base font-semibold text-white flex items-center gap-2">
          <Package className="w-4 h-4 text-bambu-gray" />
          Self-contained Slicer Bundles
        </h3>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-bambu-gray">
          Import an OrcaSlicer or Bambu Studio preset bundle as a reliable fallback when a
          custom printer is unavailable from cloud sync. Bambuddy validates every parent
          preset in the inheritance chain before accepting a printer profile.
        </p>
        <p className="text-xs text-bambu-gray">
          Keep all custom parents in the same bundle. For TinyT and Trident this includes
          the complete <span className="font-mono text-white">MyToolChanger</span> chain.
        </p>

        <input
          ref={fileInputRef}
          type="file"
          className="hidden"
          accept=".zip,.bbscfg,.json"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) importMutation.mutate(file);
            event.target.value = '';
          }}
        />
        {hasPermission('settings:update') && (
          <Button
            onClick={() => fileInputRef.current?.click()}
            disabled={importMutation.isPending}
          >
            {importMutation.isPending ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Upload className="w-4 h-4" />
            )}
            Import preset bundle
          </Button>
        )}

        {result && (
          <div className="rounded-lg border border-bambu-dark-tertiary bg-bambu-dark p-3 text-sm">
            <div className="flex items-center gap-2 text-white">
              {result.errors.length === 0 ? (
                <CheckCircle2 className="w-4 h-4 text-green-400" />
              ) : (
                <AlertCircle className="w-4 h-4 text-yellow-400" />
              )}
              {result.imported} imported · {result.skipped} skipped
            </div>
            {result.errors.length > 0 && (
              <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-yellow-300">
                {result.errors.slice(0, 5).map((error) => (
                  <li key={error}>{error}</li>
                ))}
              </ul>
            )}
          </div>
        )}

        <p className="text-xs text-bambu-gray">
          Preset lookup order: imported bundle, Orca Cloud, Bambu Cloud, then the slicer
          sidecar&apos;s standard profiles.
        </p>
      </CardContent>
    </Card>
  );
}
