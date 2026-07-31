import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Package, Search, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { api } from '../api/client';
import type { InventorySpool, MaterialSlotAssignment } from '../api/client';
import { useToast } from '../contexts/ToastContext';
import { getSwatchStyle } from '../utils/colors';
import { filterSpoolsByQuery } from '../utils/inventorySearch';
import { Button } from './Button';

interface AssignMaterialSlotModalProps {
  printerId: number;
  materialSystemId: string;
  slotId: string;
  slotLabel: string;
  spoolmanEnabled: boolean;
  onClose: () => void;
}

function remainingWeight(spool: InventorySpool): number {
  return Math.max(0, Math.round((spool.label_weight || 0) - (spool.weight_used || 0)));
}

export function AssignMaterialSlotModal({
  printerId,
  materialSystemId,
  slotId,
  slotLabel,
  spoolmanEnabled,
  onClose,
}: AssignMaterialSlotModalProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const { data: assignments } = useQuery({
    queryKey: ['material-slot-assignments'],
    queryFn: () => api.getMaterialSlotAssignments(),
  });
  const { data: internalSpools, isLoading: internalLoading } = useQuery({
    queryKey: ['inventory-spools', 'material-slot-modal'],
    queryFn: () => api.getSpools(false),
    enabled: !spoolmanEnabled,
  });
  const { data: spoolmanSpools, isLoading: spoolmanLoading } = useQuery({
    queryKey: ['spoolman-inventory-spools', 'material-slot-modal'],
    queryFn: () => api.getSpoolmanInventorySpools(false),
    enabled: spoolmanEnabled,
  });

  const source: MaterialSlotAssignment['source'] = spoolmanEnabled ? 'spoolman' : 'internal';
  const spools = spoolmanEnabled ? spoolmanSpools : internalSpools;
  const assignedIds = useMemo(() => {
    const result = new Set<number>();
    for (const assignment of assignments || []) {
      if (
        assignment.printer_id === printerId
        && assignment.material_system_id === materialSystemId
        && assignment.slot_id === slotId
      ) {
        continue;
      }
      const id = source === 'spoolman' ? assignment.spoolman_spool_id : assignment.spool_id;
      if (assignment.source === source && id != null) result.add(id);
    }
    return result;
  }, [assignments, materialSystemId, printerId, slotId, source]);

  const availableSpools = useMemo(
    () => filterSpoolsByQuery(
      (spools || []).filter((spool) => !spool.archived_at && !assignedIds.has(spool.id)),
      search,
    ),
    [assignedIds, search, spools],
  );

  const mutation = useMutation({
    mutationFn: (spoolId: number) => api.assignMaterialSlot({
      printer_id: printerId,
      material_system_id: materialSystemId,
      slot_id: slotId,
      source,
      ...(source === 'spoolman' ? { spoolman_spool_id: spoolId } : { spool_id: spoolId }),
    }),
    onSuccess: (assignment) => {
      queryClient.setQueryData<MaterialSlotAssignment[]>(['material-slot-assignments'], (old) => [
        ...(old || []).filter((item) => !(
          item.printer_id === printerId
          && item.material_system_id === materialSystemId
          && item.slot_id === slotId
        )),
        assignment,
      ]);
      queryClient.invalidateQueries({ queryKey: ['material-slot-assignments'] });
      showToast(t('inventory.assignSuccess'), 'success');
      onClose();
    },
    onError: (error: Error) => showToast(`${t('inventory.assignFailed')}: ${error.message}`, 'error'),
  });

  const isLoading = internalLoading || spoolmanLoading;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div
        className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-bambu-dark-tertiary bg-bambu-dark-secondary shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-bambu-dark-tertiary px-4 py-3">
          <div>
            <h2 className="font-semibold text-white">{t('inventory.assignSpool')}</h2>
            <p className="text-xs text-bambu-gray">{slotLabel}</p>
          </div>
          <button onClick={onClose} className="rounded p-1 text-bambu-gray transition-colors hover:text-white">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-bambu-gray" />
            <input
              autoFocus
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={t('inventory.searchSpools')}
              className="w-full rounded-lg border border-bambu-dark-tertiary bg-bambu-dark py-2 pl-9 pr-3 text-sm text-white placeholder:text-bambu-gray focus:border-bambu-green focus:outline-none"
            />
          </div>

          {isLoading ? (
            <div className="flex justify-center py-10">
              <Loader2 className="h-6 w-6 animate-spin text-bambu-green" />
            </div>
          ) : availableSpools.length === 0 ? (
            <p className="py-10 text-center text-sm text-bambu-gray">{t('inventory.noAvailableSpools')}</p>
          ) : (
            <div className="grid max-h-[50vh] grid-cols-2 gap-2 overflow-y-auto sm:grid-cols-3">
              {availableSpools.map((spool) => (
                <button
                  key={spool.id}
                  onClick={() => setSelectedId(spool.id)}
                  className={`rounded-lg border p-2.5 text-left transition-colors ${
                    selectedId === spool.id
                      ? 'border-bambu-green bg-bambu-green/20'
                      : 'border-bambu-dark-tertiary bg-bambu-dark hover:border-bambu-gray'
                  }`}
                >
                  <p className="truncate text-sm font-medium text-white">
                    {spool.brand ? `${spool.brand} ` : ''}{spool.material}{spool.subtype ? ` ${spool.subtype}` : ''}
                  </p>
                  <div className="mt-1 flex items-center gap-1.5">
                    {spool.rgba && (
                      <span
                        className="h-3 w-3 shrink-0 rounded-full border border-black/20"
                        style={getSwatchStyle(spool.rgba)}
                      />
                    )}
                    <span className="truncate text-xs text-bambu-gray">{spool.color_name || ''}</span>
                  </div>
                  {spool.label_weight > 0 && (
                    <p className="mt-1 text-xs text-bambu-gray">
                      {remainingWeight(spool)} / {spool.label_weight}g
                    </p>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-bambu-dark-tertiary p-4">
          <Button variant="secondary" onClick={onClose}>{t('common.cancel')}</Button>
          <Button
            disabled={selectedId == null || mutation.isPending}
            onClick={() => selectedId != null && mutation.mutate(selectedId)}
          >
            {mutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Package className="h-4 w-4" />}
            {mutation.isPending ? t('inventory.assigning') : t('inventory.assignSpool')}
          </Button>
        </div>
      </div>
    </div>
  );
}
