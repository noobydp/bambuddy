/**
 * Discovery — custom-subnet picker (#1564).
 *
 * Reporters with a printer behind a router on a different L3 segment
 * (e.g. Bambuddy on 192.168.1.0/24, printer on 10.1.1.0/24) couldn't
 * scan that subnet because:
 *   - SSDP multicast doesn't cross routers
 *   - The Docker-mode subnet input was the only path that accepted a
 *     CIDR, and it was hidden in native mode
 *
 * The fix surfaces the subnet picker in native mode too and adds a
 * "Custom..." option that reveals a CIDR text input. Picking it routes
 * through startSubnetScan(cidr) instead of startDiscovery().
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { AddPrinterModal } from '../../pages/PrintersPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

describe('AddPrinterModal — custom subnet (#1564)', () => {
  let scanCalls: { subnet: string; timeout: number }[];
  let ssdpStarted: boolean;

  beforeEach(() => {
    scanCalls = [];
    ssdpStarted = false;
    // localStorage is a vi.fn() spy in this test env (see setup.ts), so
    // clear call history rather than removeItem-ing the absent value.
    vi.mocked(localStorage.setItem).mockClear();
    vi.mocked(localStorage.getItem).mockClear();
    server.use(
      // Native install with one detected subnet.
      http.get('/api/v1/discovery/info', () =>
        HttpResponse.json({
          is_docker: false,
          ssdp_running: false,
          scan_running: false,
          subnets: ['192.168.1.0/24'],
        }),
      ),
      http.post('/api/v1/discovery/start', () => {
        ssdpStarted = true;
        return HttpResponse.json({ running: true });
      }),
      http.post('/api/v1/discovery/stop', () =>
        HttpResponse.json({ running: false }),
      ),
      http.post('/api/v1/discovery/scan', async ({ request }) => {
        const body = (await request.json()) as { subnet: string; timeout: number };
        scanCalls.push(body);
        return HttpResponse.json({ running: true, scanned: 0, total: 254 });
      }),
      http.post('/api/v1/discovery/scan/stop', () =>
        HttpResponse.json({ running: false, scanned: 0, total: 0 }),
      ),
      http.get('/api/v1/discovery/scan/status', () =>
        HttpResponse.json({ running: false, scanned: 254, total: 254 }),
      ),
      http.get('/api/v1/discovery/printers', () => HttpResponse.json([])),
    );
  });

  it('renders the subnet picker even on a native (non-Docker) install', async () => {
    render(
      <AddPrinterModal
        onClose={() => {}}
        onAdd={() => {}}
        existingSerials={[]}
      />,
    );

    // The picker is now ungated; the detected subnet shows in the
    // dropdown alongside the "Custom..." sentinel.
    await waitFor(() => {
      expect(
        screen.getByRole('option', { name: '192.168.1.0/24' }),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByRole('option', { name: /custom subnet/i }),
    ).toBeInTheDocument();
  });

  it('routes a custom CIDR through startSubnetScan, not SSDP', async () => {
    const user = userEvent.setup();
    render(
      <AddPrinterModal
        onClose={() => {}}
        onAdd={() => {}}
        existingSerials={[]}
      />,
    );

    // Wait for discoveryApi.getInfo() to populate the dropdown.
    await waitFor(() => {
      expect(
        screen.getByRole('option', { name: '192.168.1.0/24' }),
      ).toBeInTheDocument();
    });

    // Pick the "Custom..." sentinel. Scope by display value because the
    // modal also has a model <select>.
    const select = screen.getByDisplayValue('192.168.1.0/24') as HTMLSelectElement;
    await user.selectOptions(select, '__custom__');

    // The CIDR text input appears (aria-labelled "Custom subnet (CIDR)").
    const cidrInput = await screen.findByLabelText(/custom subnet \(cidr\)/i);
    await user.clear(cidrInput);
    await user.type(cidrInput, '10.1.1.0/24');

    // Click the scan button — labelled "Scan Subnet..." now, not
    // "Discover Printers on Network", because the user picked custom.
    const scanButton = screen.getByRole('button', { name: /scan subnet/i });
    await user.click(scanButton);

    await waitFor(() => {
      expect(scanCalls.length).toBe(1);
    });
    expect(scanCalls[0].subnet).toBe('10.1.1.0/24');
    // SSDP must not start when a custom CIDR is in play — multicast
    // can't reach the foreign subnet anyway.
    expect(ssdpStarted).toBe(false);
    // And we persist the choice so the user doesn't retype next time.
    expect(localStorage.setItem).toHaveBeenCalledWith(
      'bambuddy.discovery.customSubnet',
      '10.1.1.0/24',
    );
  });

  it('actively scans the detected subnet for every provider', async () => {
    const user = userEvent.setup();
    render(
      <AddPrinterModal
        onClose={() => {}}
        onAdd={() => {}}
        existingSerials={[]}
      />,
    );

    await waitFor(() => {
      expect(
        screen.getByRole('option', { name: '192.168.1.0/24' }),
      ).toBeInTheDocument();
    });

    // Don't change the selection — the detected subnet is actively scanned,
    // because Klipper and FlashForge do not advertise through Bambu SSDP.
    const scanButton = screen.getByRole('button', {
      name: /scan subnet/i,
    });
    await user.click(scanButton);

    await waitFor(() => {
      expect(scanCalls.length).toBe(1);
    });
    expect(scanCalls[0].subnet).toBe('192.168.1.0/24');
    expect(ssdpStarted).toBe(false);
  });

  it('selects a discovered Moonraker printer with optional credentials omitted', async () => {
    const user = userEvent.setup();
    const onAdd = vi.fn();
    server.use(
      http.get('/api/v1/discovery/scan/status', () =>
        HttpResponse.json({ running: false, scanned: 254, total: 254 }),
      ),
      http.get('/api/v1/discovery/printers', () =>
        HttpResponse.json([
          {
            serial: 'KLIPPER-TESTDEVICE',
            name: 'test-klipper',
            ip_address: '192.0.2.30',
            model: 'Klipper',
            provider: 'klipper',
            connection_port: 7125,
            discovered_at: '2026-01-01T00:00:00Z',
          },
        ]),
      ),
    );

    render(
      <AddPrinterModal
        onClose={() => {}}
        onAdd={onAdd}
        existingSerials={[]}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole('option', { name: '192.168.1.0/24' })).toBeInTheDocument();
    });
    await user.click(screen.getByRole('button', { name: /scan subnet/i }));
    await user.click(await screen.findByText('test-klipper'));
    await user.click(screen.getByRole('button', { name: /^add printer$/i }));

    expect(onAdd).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'test-klipper',
        ip_address: '192.0.2.30',
        provider: 'klipper',
        model: 'Klipper',
        connection_port: 7125,
        serial_number: undefined,
        access_code: undefined,
      }),
    );
  });
});
