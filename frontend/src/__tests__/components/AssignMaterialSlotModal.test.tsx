import { beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { screen, waitFor } from '@testing-library/react';

import { api } from '../../api/client';
import { AssignMaterialSlotModal } from '../../components/AssignMaterialSlotModal';
import { render } from '../utils';

vi.mock('../../api/client', () => ({
  api: {
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
    getSettings: vi.fn().mockResolvedValue({}),
    getMaterialSlotAssignments: vi.fn(),
    getSpools: vi.fn(),
    getSpoolmanInventorySpools: vi.fn(),
    assignMaterialSlot: vi.fn(),
  },
}));

const spool = {
  id: 42,
  material: 'PLA',
  subtype: 'Basic',
  brand: 'Polymaker',
  color_name: 'Red',
  rgba: 'FF0000FF',
  label_weight: 1000,
  weight_used: 125,
  archived_at: null,
};

describe('AssignMaterialSlotModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getMaterialSlotAssignments).mockResolvedValue([]);
    vi.mocked(api.getSpools).mockResolvedValue([spool] as never);
    vi.mocked(api.assignMaterialSlot).mockResolvedValue({
      id: 1,
      printer_id: 7,
      material_system_id: 'toolheads',
      slot_id: 'extruder1',
      source: 'internal',
      spool_id: 42,
      spoolman_spool_id: null,
      assigned_at: '2026-07-31T00:00:00Z',
      spool: spool as never,
    });
  });

  it('assigns an internal spool to the stable extruder slot without a printer action', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <AssignMaterialSlotModal
        printerId={7}
        materialSystemId="toolheads"
        slotId="extruder1"
        slotLabel="T1"
        spoolmanEnabled={false}
        onClose={onClose}
      />,
    );

    await user.click(await screen.findByRole('button', { name: /Polymaker PLA Basic/i }));
    await user.click(screen.getByRole('button', { name: 'Assign Spool' }));

    await waitFor(() => {
      expect(api.assignMaterialSlot).toHaveBeenCalledWith({
        printer_id: 7,
        material_system_id: 'toolheads',
        slot_id: 'extruder1',
        source: 'internal',
        spool_id: 42,
      });
      expect(onClose).toHaveBeenCalled();
    });
  });
});
