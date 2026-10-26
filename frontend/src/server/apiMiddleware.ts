import type { IncomingMessage, ServerResponse } from 'http';
import type {
  AuthorBadgeDTO,
  BuildingRole,
  DemoUserView,
  DisplayMode,
  EmergencyView,
  HistoryEntry,
  MasterMeterRow,
  MatrixApartment,
  MembershipView,
  MeterUnit,
  PeriodDTO,
  PeriodStatus,
  PostCategory,
  PostType,
  ReadingSource,
  ReadingSummary,
  ResourceType,
  SubmissionStatus,
  TargetScope,
} from '../api/schema';

const BUILDING_ID = '018f0000-0000-7000-8000-000000000001';

interface SeedUser extends DemoUserView {
  full_name: string;
  initials: string;
}

const DEMO_USERS: SeedUser[] = [
  {
    user_id: '018f0000-0000-7000-8000-000000000102',
    label: 'Owner Apt 14 (Kati Kask)',
    full_name: 'Kati Kask',
    initials: 'K. K.',
    building_role: 'USER',
    apartment_number: '14',
    relation_type: 'OWNER',
  },
  {
    user_id: '018f0000-0000-7000-8000-000000000101',
    label: 'Board Chairman (Peeter Tamm)',
    full_name: 'Peeter Tamm',
    initials: 'P. T.',
    building_role: 'CHAIRMAN',
    apartment_number: '01',
    relation_type: 'OWNER',
  },
  {
    user_id: '018f0000-0000-7000-8000-000000000103',
    label: 'Accountant (Marika Rebane)',
    full_name: 'Marika Rebane',
    initials: 'M. R.',
    building_role: 'ACCOUNTANT',
    apartment_number: null,
    relation_type: null,
  },
];

function computePreviewBadge(
  user: SeedUser,
  apartmentNumber: string | null,
  mode: DisplayMode
): AuthorBadgeDTO {
  const roleBadge =
    user.building_role === 'CHAIRMAN' || user.building_role === 'BOARD'
      ? 'BOARD'
      : user.building_role === 'ACCOUNTANT'
        ? 'ACCOUNTANT'
        : 'OWNER';

  if (mode === 'FULL_NAME') {
    return {
      display_label: apartmentNumber
        ? `${user.full_name} (Apt ${apartmentNumber})`
        : user.full_name,
      role_badge: roleBadge,
    };
  }
  if (mode === 'INITIALS') {
    return {
      display_label: apartmentNumber
        ? `${user.initials} (Apt ${apartmentNumber})`
        : user.initials,
      role_badge: roleBadge,
    };
  }
  return {
    display_label: apartmentNumber ? `Apt ${apartmentNumber}` : user.full_name,
    role_badge: roleBadge,
  };
}

const membershipsByUser: Record<string, MembershipView[]> = {
  '018f0000-0000-7000-8000-000000000102': [
    {
      membership_id: '018f0000-0000-7000-8000-000000000214',
      apartment_id: '018f0000-0000-7000-8000-000000000314',
      apartment_number: '14',
      entrance: 1,
      floor: 4,
      relation_type: 'OWNER',
      can_submit_readings: true,
      can_view_bills: true,
      can_vote: true,
      privacy_display_mode: 'FULL_NAME',
      preview_badge: {
        display_label: 'Kati Kask (Apt 14)',
        role_badge: 'OWNER',
      },
    },
  ],
  '018f0000-0000-7000-8000-000000000101': [
    {
      membership_id: '018f0000-0000-7000-8000-000000000201',
      apartment_id: '018f0000-0000-7000-8000-000000000301',
      apartment_number: '01',
      entrance: 1,
      floor: 1,
      relation_type: 'OWNER',
      can_submit_readings: true,
      can_view_bills: true,
      can_vote: true,
      privacy_display_mode: 'FULL_NAME',
      preview_badge: {
        display_label: 'Peeter Tamm (Apt 01)',
        role_badge: 'BOARD',
      },
    },
  ],
  '018f0000-0000-7000-8000-000000000103': [],
};

function toBigInt3(val: string): bigint {
  const [intPart, fracPart = ''] = val.split('.');
  const padded = (fracPart + '000').slice(0, 3);
  return BigInt(intPart) * 1000n + BigInt(padded);
}

function fromBigInt3(scaled: bigint): string {
  const negative = scaled < 0n;
  const abs = negative ? -scaled : scaled;
  const intPart = abs / 1000n;
  const fracPart = (abs % 1000n).toString().padStart(3, '0');
  return `${negative ? '-' : ''}${intPart.toString()}.${fracPart}`;
}

function addDecimal(a: string, b: string): string {
  return fromBigInt3(toBigInt3(a) + toBigInt3(b));
}

function subDecimal(a: string, b: string): string {
  return fromBigInt3(toBigInt3(a) - toBigInt3(b));
}

const periodsMap: Record<string, PeriodDTO> = {
  '2026-10': {
    period_code: '2026-10',
    status: 'OPEN_FOR_RESIDENTS',
    due_date: '2026-11-04',
  },
  '2026-09': {
    period_code: '2026-09',
    status: 'LOCKED',
    due_date: '2026-10-04',
  },
  '2026-08': {
    period_code: '2026-08',
    status: 'LOCKED',
    due_date: '2026-09-04',
  },
  '2026-07': {
    period_code: '2026-07',
    status: 'LOCKED',
    due_date: '2026-08-04',
  },
};

interface InternalMeter {
  meter_id: string;
  resource_type: ResourceType;
  serial_number: string;
  location_label: string;
  unit: MeterUnit;
  previous_value: string;
  historical_avg: string;
  reading: ReadingSummary | null;
}

interface InternalApartment {
  apartment_id: string;
  apartment_number: string;
  entrance: number;
  floor: number;
  can_submit_readings: boolean;
  meters: InternalMeter[];
}

const masterMetersByPeriod: Record<string, MasterMeterRow[]> = {
  '2026-10': [
    {
      meter_id: '018f0000-0000-7000-8000-000000000991',
      resource_type: 'COLD_WATER',
      location_label: 'Basement Main Cold Water Inlet',
      unit: 'm3',
      previous_value: '4120.000',
      status: 'SUBMITTED',
      reading: {
        reading_id: '018f0000-0000-7000-8000-000000000981',
        value: '4148.500',
        consumption_delta: '28.500',
        source: 'ADMIN_OVERRIDE',
        is_anomaly_confirmed: false,
      },
    },
    {
      meter_id: '018f0000-0000-7000-8000-000000000992',
      resource_type: 'HOT_WATER',
      location_label: 'Basement Heat Exchanger Hot Water',
      unit: 'm3',
      previous_value: '2050.000',
      status: 'SUBMITTED',
      reading: {
        reading_id: '018f0000-0000-7000-8000-000000000982',
        value: '2065.400',
        consumption_delta: '15.400',
        source: 'ADMIN_OVERRIDE',
        is_anomaly_confirmed: false,
      },
    },
  ],
};

const apartmentsByPeriod: Record<string, InternalApartment[]> = {
  '2026-10': [
    {
      apartment_id: '018f0000-0000-7000-8000-000000000301',
      apartment_number: '01',
      entrance: 1,
      floor: 1,
      can_submit_readings: true,
      meters: [
        {
          meter_id: '018f0000-0000-7000-8000-000000000501',
          resource_type: 'COLD_WATER',
          serial_number: 'CW-0101-K',
          location_label: 'Kitchen Cold Water',
          unit: 'm3',
          previous_value: '110.200',
          historical_avg: '2.100',
          reading: {
            reading_id: '018f0000-0000-7000-8000-000000000801',
            value: '112.400',
            consumption_delta: '2.200',
            source: 'RESIDENT',
            is_anomaly_confirmed: false,
          },
        },
        {
          meter_id: '018f0000-0000-7000-8000-000000000502',
          resource_type: 'HOT_WATER',
          serial_number: 'HW-0101-B',
          location_label: 'Bathroom Hot Water',
          unit: 'm3',
          previous_value: '78.400',
          historical_avg: '1.800',
          reading: {
            reading_id: '018f0000-0000-7000-8000-000000000802',
            value: '80.250',
            consumption_delta: '1.850',
            source: 'RESIDENT',
            is_anomaly_confirmed: false,
          },
        },
      ],
    },
    {
      apartment_id: '018f0000-0000-7000-8000-000000000302',
      apartment_number: '02',
      entrance: 1,
      floor: 1,
      can_submit_readings: true,
      meters: [
        {
          meter_id: '018f0000-0000-7000-8000-000000000503',
          resource_type: 'COLD_WATER',
          serial_number: 'CW-0201-B',
          location_label: 'Bathroom Cold Water',
          unit: 'm3',
          previous_value: '184.000',
          historical_avg: '3.900',
          reading: {
            reading_id: '018f0000-0000-7000-8000-000000000803',
            value: '187.850',
            consumption_delta: '3.850',
            source: 'RESIDENT',
            is_anomaly_confirmed: false,
          },
        },
        {
          meter_id: '018f0000-0000-7000-8000-000000000504',
          resource_type: 'HOT_WATER',
          serial_number: 'HW-0201-B',
          location_label: 'Bathroom Hot Water',
          unit: 'm3',
          previous_value: '95.500',
          historical_avg: '2.400',
          reading: {
            reading_id: '018f0000-0000-7000-8000-000000000804',
            value: '97.950',
            consumption_delta: '2.450',
            source: 'RESIDENT',
            is_anomaly_confirmed: false,
          },
        },
      ],
    },
    {
      apartment_id: '018f0000-0000-7000-8000-000000000305',
      apartment_number: '05',
      entrance: 1,
      floor: 2,
      can_submit_readings: true,
      meters: [
        {
          meter_id: '018f0000-0000-7000-8000-000000000505',
          resource_type: 'COLD_WATER',
          serial_number: 'CW-0501-B',
          location_label: 'Bathroom Cold Water',
          unit: 'm3',
          previous_value: '302.150',
          historical_avg: '4.500',
          reading: null,
        },
        {
          meter_id: '018f0000-0000-7000-8000-000000000506',
          resource_type: 'HOT_WATER',
          serial_number: 'HW-0501-B',
          location_label: 'Bathroom Hot Water',
          unit: 'm3',
          previous_value: '140.200',
          historical_avg: '3.100',
          reading: null,
        },
      ],
    },
    {
      apartment_id: '018f0000-0000-7000-8000-000000000309',
      apartment_number: '09',
      entrance: 2,
      floor: 3,
      can_submit_readings: true,
      meters: [
        {
          meter_id: '018f0000-0000-7000-8000-000000000509',
          resource_type: 'COLD_WATER',
          serial_number: 'CW-0901-B',
          location_label: 'Bathroom Cold Water',
          unit: 'm3',
          previous_value: '165.000',
          historical_avg: '3.600',
          reading: {
            reading_id: '018f0000-0000-7000-8000-000000000809',
            value: '179.400',
            consumption_delta: '14.400',
            source: 'RESIDENT',
            is_anomaly_confirmed: true,
          },
        },
        {
          meter_id: '018f0000-0000-7000-8000-000000000510',
          resource_type: 'HOT_WATER',
          serial_number: 'HW-0901-B',
          location_label: 'Bathroom Hot Water',
          unit: 'm3',
          previous_value: '88.300',
          historical_avg: '2.200',
          reading: {
            reading_id: '018f0000-0000-7000-8000-000000000810',
            value: '90.500',
            consumption_delta: '2.200',
            source: 'RESIDENT',
            is_anomaly_confirmed: false,
          },
        },
      ],
    },
    {
      apartment_id: '018f0000-0000-7000-8000-000000000314',
      apartment_number: '14',
      entrance: 2,
      floor: 4,
      can_submit_readings: true,
      meters: [
        {
          meter_id: '018f0000-0000-7000-8000-000000000514',
          resource_type: 'COLD_WATER',
          serial_number: 'CW-1429-K',
          location_label: 'Kitchen Cold Water',
          unit: 'm3',
          previous_value: '84.100',
          historical_avg: '2.100',
          reading: {
            reading_id: '018f0000-0000-7000-8000-000000000901',
            value: '86.200',
            consumption_delta: '2.100',
            source: 'RESIDENT',
            is_anomaly_confirmed: false,
          },
        },
        {
          meter_id: '018f0000-0000-7000-8000-000000000515',
          resource_type: 'HOT_WATER',
          serial_number: 'HW-1429-K',
          location_label: 'Kitchen Hot Water',
          unit: 'm3',
          previous_value: '52.400',
          historical_avg: '1.300',
          reading: {
            reading_id: '018f0000-0000-7000-8000-000000000902',
            value: '53.750',
            consumption_delta: '1.350',
            source: 'RESIDENT',
            is_anomaly_confirmed: false,
          },
        },
        {
          meter_id: '018f0000-0000-7000-8000-000000000516',
          resource_type: 'COLD_WATER',
          serial_number: 'CW-1429-B',
          location_label: 'Bathroom Cold Water',
          unit: 'm3',
          previous_value: '142.350',
          historical_avg: '3.200',
          reading: null,
        },
        {
          meter_id: '018f0000-0000-7000-8000-000000000517',
          resource_type: 'HOT_WATER',
          serial_number: 'HW-1429-B',
          location_label: 'Bathroom Hot Water',
          unit: 'm3',
          previous_value: '88.120',
          historical_avg: '2.100',
          reading: null,
        },
        {
          meter_id: '018f0000-0000-7000-8000-000000000518',
          resource_type: 'ELECTRICITY',
          serial_number: 'EL-1429-EV',
          location_label: 'Basement Storage & EV Charger',
          unit: 'kWh',
          previous_value: '1240.000',
          historical_avg: '65.000',
          reading: {
            reading_id: '018f0000-0000-7000-8000-000000000905',
            value: '1304.500',
            consumption_delta: '64.500',
            source: 'RESIDENT',
            is_anomaly_confirmed: false,
          },
        },
      ],
    },
  ],
};

// Populate historical periods 2026-09, 2026-08, 2026-07
for (const histCode of ['2026-09', '2026-08', '2026-07']) {
  apartmentsByPeriod[histCode] = JSON.parse(JSON.stringify(apartmentsByPeriod['2026-10'])).map(
    (apt: InternalApartment) => ({
      ...apt,
      can_submit_readings: false,
      meters: apt.meters.map((m) => ({
        ...m,
        previous_value: subDecimal(m.previous_value, m.historical_avg),
        reading: {
          reading_id: `${m.meter_id}-${histCode}`,
          value: m.previous_value,
          consumption_delta: m.historical_avg,
          source: 'RESIDENT',
          is_anomaly_confirmed: false,
        },
      })),
    })
  );
  masterMetersByPeriod[histCode] = JSON.parse(JSON.stringify(masterMetersByPeriod['2026-10']));
}

const historyStore: Record<string, { meter_id: string; period_code: string; entries: HistoryEntry[] }> = {
  '018f0000-0000-7000-8000-000000000901': {
    meter_id: '018f0000-0000-7000-8000-000000000514',
    period_code: '2026-10',
    entries: [
      {
        changed_at: '2026-10-25T14:20:00Z',
        old_value: null,
        new_value: '86.200',
        source: 'RESIDENT',
        reason: null,
        changed_by: {
          display_label: 'Kati Kask (Apt 14)',
          role_badge: 'OWNER',
        },
      },
    ],
  },
};

interface InternalComment {
  id: string;
  post_id: string;
  body: string;
  author_user_id: string;
  author_apartment_id: string | null;
  created_at: string;
}

interface InternalPost {
  id: string;
  post_type: PostType;
  category: PostCategory;
  title: string;
  body: string;
  author_user_id: string;
  author_apartment_id: string | null;
  target_scope: TargetScope;
  target_entrance: number | null;
  target_apartment_ids: string[] | null;
  is_active: boolean;
  comments_enabled: boolean;
  created_at: string;
  acknowledged_by: Record<string, string>;
  comments: InternalComment[];
}

const postsStore: InternalPost[] = [
  {
    id: '018f0000-0000-7000-8000-000000000701',
    post_type: 'EMERGENCY',
    category: 'WATER_SHUTOFF',
    title: 'Basement Heating Valve Pressure Drop (Stairwell B)',
    body: 'Pressure sensor in Stairwell B heat exchanger dropped from 2.4 bar to 1.6 bar. Technician is on site inspecting the expansion vessel.',
    author_user_id: '018f0000-0000-7000-8000-000000000101',
    author_apartment_id: '018f0000-0000-7000-8000-000000000301',
    target_scope: 'ALL_BUILDING',
    target_entrance: null,
    target_apartment_ids: null,
    is_active: true,
    comments_enabled: true,
    created_at: '2026-10-25T08:15:00Z',
    acknowledged_by: {},
    comments: [],
  },
  {
    id: '018f0000-0000-7000-8000-000000000601',
    post_type: 'OFFICIAL_NOTICE',
    category: 'GENERAL',
    title: 'October 2026 Utility Meter Submission & Courtyard Maintenance',
    body: 'Dear residents of Sõpruse pst 12,\n\nPlease submit all October 2026 cold and hot water readings by November 4 at 23:59. Unsubmitted meters will be automatically estimated from your 3-month rolling average.\n\nAdditionally, autumn courtyard leaf clearing and underground garage drain flushing will take place on Saturday, November 7 from 09:00 to 14:00.',
    author_user_id: '018f0000-0000-7000-8000-000000000101',
    author_apartment_id: '018f0000-0000-7000-8000-000000000301',
    target_scope: 'ALL_BUILDING',
    target_entrance: null,
    target_apartment_ids: null,
    is_active: true,
    comments_enabled: true,
    created_at: '2026-10-23T11:00:00Z',
    acknowledged_by: {},
    comments: [
      {
        id: '018f0000-0000-7000-8000-000000000651',
        post_id: '018f0000-0000-7000-8000-000000000601',
        body: 'Thank you Peeter! Should cars be moved out of the courtyard parking spots before 08:45 on Saturday?',
        author_user_id: '018f0000-0000-7000-8000-000000000102',
        author_apartment_id: '018f0000-0000-7000-8000-000000000314',
        created_at: '2026-10-23T13:20:00Z',
      },
      {
        id: '018f0000-0000-7000-8000-000000000652',
        post_id: '018f0000-0000-7000-8000-000000000601',
        body: 'Yes, please clear the courtyard bays by 08:45 so the maintenance vehicle can access all corners.',
        author_user_id: '018f0000-0000-7000-8000-000000000101',
        author_apartment_id: '018f0000-0000-7000-8000-000000000301',
        created_at: '2026-10-23T14:05:00Z',
      },
    ],
  },
  {
    id: '018f0000-0000-7000-8000-000000000602',
    post_type: 'FORUM_TOPIC',
    category: 'MAINTENANCE',
    title: 'Bicycle Storage Rack Expansion in Ground Floor Room #2',
    body: 'Several families on the 3rd and 4th floors proposed installing vertical wall-mounted bike hooks in the secondary storage room. Total quote for 12 heavy-duty hooks is 480 EUR from the maintenance fund.',
    author_user_id: '018f0000-0000-7000-8000-000000000102',
    author_apartment_id: '018f0000-0000-7000-8000-000000000314',
    target_scope: 'ALL_BUILDING',
    target_entrance: null,
    target_apartment_ids: null,
    is_active: true,
    comments_enabled: true,
    created_at: '2026-10-20T17:30:00Z',
    acknowledged_by: {},
    comments: [
      {
        id: '018f0000-0000-7000-8000-000000000653',
        post_id: '018f0000-0000-7000-8000-000000000602',
        body: 'We have sufficient allocation in the Q4 minor improvements budget line to cover 480 EUR without affecting the reserve fund.',
        author_user_id: '018f0000-0000-7000-8000-000000000103',
        author_apartment_id: null,
        created_at: '2026-10-21T09:15:00Z',
      },
    ],
  },
  {
    id: '018f0000-0000-7000-8000-000000000603',
    post_type: 'OFFICIAL_NOTICE',
    category: 'MAINTENANCE',
    title: 'District Heating Substation Winter Calibration Completed',
    body: 'Utilitas Tallinn completed the scheduled diagnostic check of our primary heating substation. Efficiency is within 99.1% of nominal specification and night-time setback curve has been calibrated.',
    author_user_id: '018f0000-0000-7000-8000-000000000101',
    author_apartment_id: '018f0000-0000-7000-8000-000000000301',
    target_scope: 'ALL_BUILDING',
    target_entrance: null,
    target_apartment_ids: null,
    is_active: true,
    comments_enabled: true,
    created_at: '2026-10-15T10:00:00Z',
    acknowledged_by: {},
    comments: [],
  },
];

function resolveAuthorBadge(userId: string): AuthorBadgeDTO {
  const user = DEMO_USERS.find((u) => u.user_id === userId) || DEMO_USERS[0];
  const mems = membershipsByUser[userId] || [];
  const primary = mems[0] || null;
  const mode = primary?.privacy_display_mode || 'FULL_NAME';
  return computePreviewBadge(user, primary?.apartment_number || null, mode);
}

function getActingUser(req: IncomingMessage): SeedUser {
  const headerId = req.headers['x-demo-user-id'] as string | undefined;
  return DEMO_USERS.find((u) => u.user_id === headerId) || DEMO_USERS[0];
}

function sendJson(res: ServerResponse, status: number, data: unknown) {
  const traceId = `018f-${Math.random().toString(16).slice(2, 10)}`;
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('X-Trace-Id', traceId);
  res.end(JSON.stringify(data));
}

function sendError(
  res: ServerResponse,
  status: number,
  errorCode: string,
  message: string,
  extra: Record<string, unknown> = {}
) {
  const traceId = `018f-${Math.random().toString(16).slice(2, 10)}`;
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('X-Trace-Id', traceId);
  res.end(
    JSON.stringify({
      error_code: errorCode,
      message,
      trace_id: traceId,
      ...extra,
    })
  );
}

function readBody(req: IncomingMessage): Promise<any> {
  return new Promise((resolve) => {
    let body = '';
    req.on('data', (chunk) => {
      body += chunk;
    });
    req.on('end', () => {
      if (!body) return resolve({});
      try {
        resolve(JSON.parse(body));
      } catch {
        resolve({});
      }
    });
  });
}

export function createApiMiddleware() {
  return async (req: IncomingMessage, res: ServerResponse, next: () => void) => {
    const rawUrl = req.url || '';
    const urlObj = new URL(rawUrl, 'http://localhost:3000');
    const pathname = urlObj.pathname;
    const method = req.method || 'GET';

    if (pathname === '/health' && method === 'GET') {
      return sendJson(res, 200, { status: 'ok', service: 'apartment-association-backend' });
    }
    if (pathname === '/health/ready' && method === 'GET') {
      return sendJson(res, 200, { status: 'ready', checks: { database: 'ok' } });
    }

    if (!pathname.startsWith('/api/v1/')) {
      return next();
    }

    // 1. GET /api/v1/demo/users
    if (pathname === '/api/v1/demo/users' && method === 'GET') {
      const views: DemoUserView[] = DEMO_USERS.map((u) => ({
        user_id: u.user_id,
        label: u.label,
        building_role: u.building_role,
        apartment_number: u.apartment_number,
        relation_type: u.relation_type,
      }));
      return sendJson(res, 200, views);
    }

    const user = getActingUser(req);
    const isBoardOrAccountant = ['CHAIRMAN', 'BOARD', 'ACCOUNTANT'].includes(user.building_role);
    const isBoard = ['CHAIRMAN', 'BOARD'].includes(user.building_role);

    // 2. GET /api/v1/property/me
    if (pathname === '/api/v1/property/me' && method === 'GET') {
      const userMems = membershipsByUser[user.user_id] || [];
      return sendJson(res, 200, {
        user_id: user.user_id,
        building_id: BUILDING_ID,
        building_role: user.building_role,
        privacy_display_mode: userMems[0]?.privacy_display_mode || 'FULL_NAME',
        memberships: userMems,
      });
    }

    // 2b. GET /api/v1/property/building (Added in PR #31)
    if (pathname === '/api/v1/property/building' && method === 'GET') {
      return sendJson(res, 200, {
        building_id: BUILDING_ID,
        name: 'KÜ Sõpruse 12',
        registry_code: '80123456',
        address: 'Sõpruse pst 12, Kristiine, Tallinn',
        district: 'Kristiine',
        apartments_count: 5,
        total_apartments: 5,
        entrances_count: 2,
        total_entrances: 2,
        heated_area_m2: '329.20',
      });
    }

    // 3. PATCH /api/v1/property/memberships/{membership_id}/privacy
    const privacyMatch = pathname.match(/^\/api\/v1\/property\/memberships\/([^/]+)\/privacy$/);
    if (privacyMatch && method === 'PATCH') {
      const membershipId = privacyMatch[1];
      const body = await readBody(req);
      const userMems = membershipsByUser[user.user_id] || [];
      const target = userMems.find((m) => m.membership_id === membershipId);
      if (!target) {
        return sendError(res, 403, 'FORBIDDEN', 'Membership does not belong to current user.');
      }
      const newMode: DisplayMode = body.privacy_display_mode || 'FULL_NAME';
      target.privacy_display_mode = newMode;
      target.preview_badge = computePreviewBadge(user, target.apartment_number, newMode);
      return sendJson(res, 200, {
        membership_id: target.membership_id,
        privacy_display_mode: target.privacy_display_mode,
        preview_badge: target.preview_badge,
      });
    }

    // 3b. GET /api/v1/meters/periods (Added in PR #31: all periods, newest first)
    if (pathname === '/api/v1/meters/periods' && method === 'GET') {
      const sortedPeriods = Object.values(periodsMap).sort((a, b) =>
        b.period_code.localeCompare(a.period_code)
      );
      return sendJson(res, 200, sortedPeriods);
    }

    // 4. GET /api/v1/meters/my-dashboard
    if (pathname === '/api/v1/meters/my-dashboard' && method === 'GET') {
      const periodCode = urlObj.searchParams.get('period_code') || '2026-10';
      const period = periodsMap[periodCode] || periodsMap['2026-10'];
      const periodApts = apartmentsByPeriod[periodCode] || apartmentsByPeriod['2026-10'];
      const userMems = membershipsByUser[user.user_id] || [];
      const userAptIds = new Set(userMems.map((m) => m.apartment_id));

      const apartments = periodApts
        .filter((a) => userAptIds.has(a.apartment_id))
        .map((a) => ({
          apartment_id: a.apartment_id,
          apartment_number: a.apartment_number,
          can_submit_readings: period.status === 'OPEN_FOR_RESIDENTS' && a.can_submit_readings,
          meters: a.meters.map((m) => ({
            meter_id: m.meter_id,
            resource_type: m.resource_type,
            serial_number: m.serial_number,
            location_label: m.location_label,
            unit: m.unit,
            previous_value: m.previous_value,
            previous_delta: m.historical_avg,
            average_delta: m.historical_avg,
            reading: m.reading,
          })),
        }));

      return sendJson(res, 200, {
        period,
        apartments,
      });
    }

    // 5. POST /api/v1/meters/readings
    if (pathname === '/api/v1/meters/readings' && method === 'POST') {
      const body = await readBody(req);
      const {
        meter_id,
        period_code = '2026-10',
        value,
        confirmation_level = 0,
        reason = null,
      } = body;

      const period = periodsMap[period_code] || periodsMap['2026-10'];
      if (period.status === 'LOCKED') {
        return sendError(res, 409, 'PERIOD_LOCKED', 'This billing period is locked.');
      }
      if (period.status !== 'OPEN_FOR_RESIDENTS' && !isBoardOrAccountant) {
        return sendError(res, 409, 'PERIOD_CLOSED', 'Period is closed for residents.');
      }

      const periodApts = apartmentsByPeriod[period_code] || apartmentsByPeriod['2026-10'];
      const masterMeters = masterMetersByPeriod[period_code] || masterMetersByPeriod['2026-10'];

      let foundMeter: InternalMeter | null = null;
      let foundApt: InternalApartment | null = null;
      let foundMaster: MasterMeterRow | null = null;

      for (const apt of periodApts) {
        const m = apt.meters.find((item) => item.meter_id === meter_id);
        if (m) {
          foundMeter = m;
          foundApt = apt;
          break;
        }
      }

      if (!foundMeter) {
        foundMaster = masterMeters.find((m) => m.meter_id === meter_id) || null;
      }

      if (!foundMeter && !foundMaster) {
        return sendError(res, 404, 'METER_NOT_FOUND', 'Meter not found.');
      }

      if (typeof value !== 'string' || !/^[0-9]{1,9}(\.[0-9]{1,3})?$/.test(value)) {
        return sendError(res, 422, 'VALIDATION_ERROR', 'Invalid decimal meter reading.');
      }

      const normalizedVal = fromBigInt3(toBigInt3(value));
      const previousValue = foundMeter ? foundMeter.previous_value : foundMaster!.previous_value;
      const prevScaled = toBigInt3(previousValue);
      const currScaled = toBigInt3(normalizedVal);

      if (currScaled < prevScaled) {
        return sendError(
          res,
          422,
          'READING_BELOW_PREVIOUS',
          `Value (${normalizedVal}) cannot be lower than previous reading (${previousValue}).`,
          { previous_value: previousValue }
        );
      }

      // Check if Board/Accountant is modifying an existing reading -> require reason
      const existingReading = foundMeter ? foundMeter.reading : foundMaster!.reading;
      const userMems = membershipsByUser[user.user_id] || [];
      const isOwnUnit = foundApt && userMems.some((m) => m.apartment_id === foundApt!.apartment_id);

      if (
        existingReading &&
        isBoardOrAccountant &&
        !isOwnUnit &&
        (!reason || !String(reason).trim())
      ) {
        return sendError(
          res,
          422,
          'REASON_REQUIRED',
          'A reason is required when overriding an existing meter reading.'
        );
      }

      const deltaScaled = currScaled - prevScaled;
      const deltaStr = fromBigInt3(deltaScaled);
      const avgStr = foundMeter ? foundMeter.historical_avg : '25.000';
      const avgScaled = toBigInt3(avgStr);
      const thresholdScaled = avgScaled * 3n > 10000n ? avgScaled * 3n : 10000n;
      const isAnomaly = foundMeter ? deltaScaled > thresholdScaled : false;

      if (isAnomaly && confirmation_level === 0) {
        return sendError(
          res,
          409,
          'ANOMALY_CONFIRMATION_STEP_1',
          `Unusually high consumption (${deltaStr} ${foundMeter!.unit}). Please verify the reading on your meter.`,
          {
            submitted_value: normalizedVal,
            previous_value: previousValue,
            consumption_delta: deltaStr,
            average_delta: avgStr,
            max_reasonable_delta: fromBigInt3(thresholdScaled),
            required_confirmation_level: 1,
          }
        );
      }

      if (isAnomaly && confirmation_level === 1) {
        return sendError(
          res,
          409,
          'ANOMALY_CONFIRMATION_STEP_2',
          `Final confirmation required: ${deltaStr} ${foundMeter!.unit} will be billed to your apartment.`,
          {
            submitted_value: normalizedVal,
            previous_value: previousValue,
            consumption_delta: deltaStr,
            historical_average: avgStr,
            required_confirmation_level: 2,
          }
        );
      }

      const oldVal = existingReading?.value || null;
      const readingId =
        existingReading?.reading_id ||
        `018f0000-0000-7000-8000-${Math.floor(100000000000 + Math.random() * 899999999999)}`;
      const source: ReadingSource = isOwnUnit ? 'RESIDENT' : 'ADMIN_OVERRIDE';

      const newReading: ReadingSummary = {
        reading_id: readingId,
        value: normalizedVal,
        consumption_delta: deltaStr,
        source,
        is_anomaly_confirmed: isAnomaly,
      };

      if (foundMeter) {
        foundMeter.reading = newReading;
      } else if (foundMaster) {
        foundMaster.reading = newReading;
        foundMaster.status = 'SUBMITTED';
      }

      if (!historyStore[readingId]) {
        historyStore[readingId] = {
          meter_id,
          period_code,
          entries: [],
        };
      }
      historyStore[readingId].entries.unshift({
        changed_at: new Date().toISOString(),
        old_value: oldVal,
        new_value: normalizedVal,
        source,
        reason: reason || null,
        changed_by: resolveAuthorBadge(user.user_id),
      });

      return sendJson(res, 200, {
        reading_id: readingId,
        meter_id,
        period_code,
        value: normalizedVal,
        consumption_delta: deltaStr,
        source,
        is_anomaly_confirmed: isAnomaly,
      });
    }

    // 6. GET /api/v1/meters/readings/{reading_id}/history
    const historyMatch = pathname.match(/^\/api\/v1\/meters\/readings\/([^/]+)\/history$/);
    if (historyMatch && method === 'GET') {
      const readingId = historyMatch[1];
      const hist = historyStore[readingId] || {
        meter_id: '018f0000-0000-7000-8000-000000000514',
        period_code: '2026-10',
        entries: [
          {
            changed_at: '2026-10-25T14:20:00Z',
            old_value: null,
            new_value: '86.200',
            source: 'RESIDENT',
            reason: null,
            changed_by: {
              display_label: 'Apt 14',
              role_badge: 'OWNER',
            },
          },
        ],
      };
      return sendJson(res, 200, {
        reading_id: readingId,
        meter_id: hist.meter_id,
        period_code: hist.period_code,
        entries: hist.entries,
      });
    }

    // 7. GET /api/v1/meters/periods/{period_code}/matrix
    const matrixMatch = pathname.match(/^\/api\/v1\/meters\/periods\/([^/]+)\/matrix$/);
    if (matrixMatch && method === 'GET') {
      if (!isBoardOrAccountant) {
        return sendError(res, 403, 'FORBIDDEN', 'Board or Accountant role required.');
      }
      const periodCode = matrixMatch[1];
      const period = periodsMap[periodCode] || periodsMap['2026-10'];
      const periodApts = apartmentsByPeriod[periodCode] || apartmentsByPeriod['2026-10'];
      const masterMeters = masterMetersByPeriod[periodCode] || masterMetersByPeriod['2026-10'];

      let coldSum = 0n;
      let hotSum = 0n;

      const apartments: MatrixApartment[] = periodApts.map((a) => {
        const hasMissing = a.meters.some((m) => !m.reading);
        const hasAnomaly = a.meters.some((m) => m.reading?.is_anomaly_confirmed);
        const hasEstimated = a.meters.some((m) => m.reading?.source === 'AUTO_ESTIMATED');

        let aptStatus: SubmissionStatus = 'SUBMITTED';
        if (hasMissing) aptStatus = 'MISSING';
        else if (hasAnomaly) aptStatus = 'ANOMALY_CONFIRMED';
        else if (hasEstimated) aptStatus = 'AUTO_ESTIMATED';

        const meters = a.meters.map((m) => {
          if (m.reading) {
            if (m.resource_type === 'COLD_WATER') coldSum += toBigInt3(m.reading.consumption_delta);
            if (m.resource_type === 'HOT_WATER') hotSum += toBigInt3(m.reading.consumption_delta);
          }
          let mStatus: SubmissionStatus = 'MISSING';
          if (m.reading) {
            if (m.reading.is_anomaly_confirmed) mStatus = 'ANOMALY_CONFIRMED';
            else if (m.reading.source === 'AUTO_ESTIMATED') mStatus = 'AUTO_ESTIMATED';
            else mStatus = 'SUBMITTED';
          }
          return {
            meter_id: m.meter_id,
            resource_type: m.resource_type,
            location_label: m.location_label,
            unit: m.unit,
            previous_value: m.previous_value,
            status: mStatus,
            reading: m.reading,
          };
        });

        return {
          apartment_id: a.apartment_id,
          apartment_number: a.apartment_number,
          entrance: a.entrance,
          floor: a.floor,
          status: aptStatus,
          meters,
        };
      });

      const coldMaster =
        masterMeters.find((m) => m.resource_type === 'COLD_WATER')?.reading?.consumption_delta ||
        '28.500';
      const hotMaster =
        masterMeters.find((m) => m.resource_type === 'HOT_WATER')?.reading?.consumption_delta ||
        '15.400';

      const coldAptStr = fromBigInt3(coldSum);
      const hotAptStr = fromBigInt3(hotSum);

      return sendJson(res, 200, {
        period,
        balances: [
          {
            resource_type: 'COLD_WATER',
            unit: 'm3',
            master_total: coldMaster,
            apartments_sum: coldAptStr,
            unallocated_loss: subDecimal(coldMaster, coldAptStr),
          },
          {
            resource_type: 'HOT_WATER',
            unit: 'm3',
            master_total: hotMaster,
            apartments_sum: hotAptStr,
            unallocated_loss: subDecimal(hotMaster, hotAptStr),
          },
        ],
        master_meters: masterMeters,
        apartments,
      });
    }

    // 8. POST /api/v1/meters/periods/{period_code}/estimate-missing
    const estimateMatch = pathname.match(/^\/api\/v1\/meters\/periods\/([^/]+)\/estimate-missing$/);
    if (estimateMatch && method === 'POST') {
      if (!isBoardOrAccountant) {
        return sendError(res, 403, 'FORBIDDEN', 'Board or Accountant role required.');
      }
      const periodCode = estimateMatch[1];
      const period = periodsMap[periodCode] || periodsMap['2026-10'];
      if (period.status === 'LOCKED') {
        return sendError(res, 409, 'PERIOD_LOCKED', 'Cannot estimate readings on a locked period.');
      }
      const periodApts = apartmentsByPeriod[periodCode] || apartmentsByPeriod['2026-10'];
      const estimated: any[] = [];

      for (const apt of periodApts) {
        for (const m of apt.meters) {
          if (!m.reading) {
            const estVal = addDecimal(m.previous_value, m.historical_avg);
            const readingId = `018f0000-0000-7000-8000-${Math.floor(100000000000 + Math.random() * 899999999999)}`;
            m.reading = {
              reading_id: readingId,
              value: estVal,
              consumption_delta: m.historical_avg,
              source: 'AUTO_ESTIMATED',
              is_anomaly_confirmed: false,
            };
            estimated.push({
              reading_id: readingId,
              meter_id: m.meter_id,
              apartment_id: apt.apartment_id,
              value: estVal,
              consumption_delta: m.historical_avg,
            });
          }
        }
      }

      return sendJson(res, 200, {
        period_code: periodCode,
        estimated_count: estimated.length,
        estimated,
      });
    }

    // 9. PATCH /api/v1/meters/periods/{period_code}/status
    const statusMatch = pathname.match(/^\/api\/v1\/meters\/periods\/([^/]+)\/status$/);
    if (statusMatch && method === 'PATCH') {
      if (!isBoardOrAccountant) {
        return sendError(res, 403, 'FORBIDDEN', 'Board or Accountant role required.');
      }
      const periodCode = statusMatch[1];
      const period = periodsMap[periodCode] || periodsMap['2026-10'];
      const body = await readBody(req);
      const prevStatus = period.status;
      if (['OPEN_FOR_RESIDENTS', 'REVIEW_BY_BOARD', 'LOCKED'].includes(body.status)) {
        period.status = body.status as PeriodStatus;
      }
      return sendJson(res, 200, {
        period_code: periodCode,
        previous_status: prevStatus,
        status: period.status,
      });
    }

    // 10. GET /api/v1/meters/periods/{period_code}/export-csv
    const csvMatch = pathname.match(/^\/api\/v1\/meters\/periods\/([^/]+)\/export-csv$/);
    if (csvMatch && method === 'GET') {
      if (!isBoardOrAccountant) {
        return sendError(res, 403, 'FORBIDDEN', 'Board or Accountant role required.');
      }
      const periodCode = csvMatch[1];
      const periodApts = apartmentsByPeriod[periodCode] || apartmentsByPeriod['2026-10'];
      const lines = [
        'period_code,apartment_number,serial_number,resource_type,previous_value,current_value,consumption_delta,unit,source',
      ];
      for (const apt of periodApts) {
        for (const m of apt.meters) {
          lines.push(
            [
              periodCode,
              apt.apartment_number,
              m.serial_number,
              m.resource_type,
              m.previous_value,
              m.reading?.value ?? '',
              m.reading?.consumption_delta ?? '',
              m.unit,
              m.reading?.source ?? 'MISSING',
            ].join(',')
          );
        }
      }
      const traceId = `018f-${Math.random().toString(16).slice(2, 10)}`;
      res.statusCode = 200;
      res.setHeader('Content-Type', 'text/csv; charset=utf-8');
      res.setHeader('Content-Disposition', `attachment; filename="readings_${periodCode}.csv"`);
      res.setHeader('X-Trace-Id', traceId);
      return res.end(lines.join('\n'));
    }

    // 11. GET /api/v1/communication/emergencies/active
    if (pathname === '/api/v1/communication/emergencies/active' && method === 'GET') {
      const activeList: EmergencyView[] = postsStore
        .filter((p) => p.post_type === 'EMERGENCY' && p.is_active)
        .map((p) => ({
          id: p.id,
          post_type: p.post_type,
          category: p.category,
          title: p.title,
          body: p.body,
          author_badge: resolveAuthorBadge(p.author_user_id),
          is_mine: p.author_user_id === user.user_id,
          target_scope: p.target_scope,
          target_entrance: p.target_entrance,
          target_apartment_ids: p.target_apartment_ids,
          is_active: p.is_active,
          comments_enabled: p.comments_enabled,
          comments_count: p.comments.length,
          created_at: p.created_at,
          acknowledged_at: p.acknowledged_by[user.user_id] || null,
        }));
      return sendJson(res, 200, activeList);
    }

    // 12. POST /api/v1/communication/emergencies/{id}/acknowledge
    const ackMatch = pathname.match(/^\/api\/v1\/communication\/emergencies\/([^/]+)\/acknowledge$/);
    if (ackMatch && method === 'POST') {
      const post = postsStore.find((p) => p.id === ackMatch[1]);
      if (!post) return sendError(res, 404, 'POST_NOT_FOUND', 'Emergency alert not found.');
      const nowIso = new Date().toISOString();
      post.acknowledged_by[user.user_id] = nowIso;
      return sendJson(res, 200, {
        post_id: post.id,
        acknowledged_at: nowIso,
      });
    }

    // 13. POST /api/v1/communication/emergencies/{id}/resolve
    const resolveMatch = pathname.match(/^\/api\/v1\/communication\/emergencies\/([^/]+)\/resolve$/);
    if (resolveMatch && method === 'POST') {
      if (!isBoard) {
        return sendError(res, 403, 'FORBIDDEN', 'Only Board members can resolve emergencies.');
      }
      const post = postsStore.find((p) => p.id === resolveMatch[1]);
      if (!post) return sendError(res, 404, 'POST_NOT_FOUND', 'Emergency alert not found.');
      post.is_active = false;
      return sendJson(res, 200, {
        post_id: post.id,
        is_active: false,
      });
    }

    // 14. GET & POST /api/v1/communication/posts
    if (pathname === '/api/v1/communication/posts' && method === 'GET') {
      const postType = urlObj.searchParams.get('post_type');
      const category = urlObj.searchParams.get('category');
      const limit = Number(urlObj.searchParams.get('limit') || '20');
      const offset = Number(urlObj.searchParams.get('offset') || '0');

      let filtered = postsStore.filter((p) => p.post_type !== 'EMERGENCY');
      if (postType) {
        filtered = postsStore.filter((p) => p.post_type === postType);
      }
      if (category) {
        filtered = filtered.filter((p) => p.category === category);
      }

      const paged = filtered.slice(offset, offset + limit).map((p) => ({
        id: p.id,
        post_type: p.post_type,
        category: p.category,
        title: p.title,
        body: p.body,
        author_badge: resolveAuthorBadge(p.author_user_id),
        is_mine: p.author_user_id === user.user_id,
        target_scope: p.target_scope,
        target_entrance: p.target_entrance,
        target_apartment_ids: p.target_apartment_ids,
        is_active: p.is_active,
        comments_enabled: p.comments_enabled,
        comments_count: p.comments.length,
        created_at: p.created_at,
      }));
      return sendJson(res, 200, paged);
    }

    if (pathname === '/api/v1/communication/posts' && method === 'POST') {
      const body = await readBody(req);
      const postType: PostType = body.post_type || 'FORUM_TOPIC';
      if ((postType === 'OFFICIAL_NOTICE' || postType === 'EMERGENCY') && !isBoard) {
        return sendError(res, 403, 'FORBIDDEN', 'Only Board members can publish notices or emergencies.');
      }
      const created: InternalPost = {
        id: `018f0000-0000-7000-8000-${Math.floor(100000000000 + Math.random() * 899999999999)}`,
        post_type: postType,
        category: body.category || 'GENERAL',
        title: body.title || 'Untitled',
        body: body.body || '',
        author_user_id: user.user_id,
        author_apartment_id: body.author_apartment_id || null,
        target_scope: body.target_scope || 'ALL_BUILDING',
        target_entrance: body.target_entrance || null,
        target_apartment_ids: body.target_apartment_ids || null,
        is_active: true,
        comments_enabled: body.comments_enabled !== false,
        created_at: new Date().toISOString(),
        acknowledged_by: {},
        comments: [],
      };
      postsStore.unshift(created);
      return sendJson(res, 201, {
        id: created.id,
        post_type: created.post_type,
        category: created.category,
        title: created.title,
        body: created.body,
        author_badge: resolveAuthorBadge(created.author_user_id),
        is_mine: true,
        target_scope: created.target_scope,
        target_entrance: created.target_entrance,
        target_apartment_ids: created.target_apartment_ids,
        is_active: created.is_active,
        comments_enabled: created.comments_enabled,
        comments_count: 0,
        created_at: created.created_at,
      });
    }

    // 15. GET & DELETE /api/v1/communication/posts/{post_id}
    const postDetailMatch = pathname.match(/^\/api\/v1\/communication\/posts\/([^/]+)$/);
    if (postDetailMatch && method === 'GET') {
      const post = postsStore.find((p) => p.id === postDetailMatch[1]);
      if (!post) return sendError(res, 404, 'POST_NOT_FOUND', 'Post not found.');
      return sendJson(res, 200, {
        id: post.id,
        post_type: post.post_type,
        category: post.category,
        title: post.title,
        body: post.body,
        author_badge: resolveAuthorBadge(post.author_user_id),
        is_mine: post.author_user_id === user.user_id,
        target_scope: post.target_scope,
        target_entrance: post.target_entrance,
        target_apartment_ids: post.target_apartment_ids,
        is_active: post.is_active,
        comments_enabled: post.comments_enabled,
        comments_count: post.comments.length,
        created_at: post.created_at,
        comments: post.comments.map((c) => ({
          id: c.id,
          post_id: post.id,
          body: c.body,
          author_badge: resolveAuthorBadge(c.author_user_id),
          is_mine: c.author_user_id === user.user_id,
          created_at: c.created_at,
        })),
      });
    }

    if (postDetailMatch && method === 'DELETE') {
      if (user.building_role !== 'CHAIRMAN') {
        return sendError(res, 403, 'FORBIDDEN', 'Only Chairman can delete forum topics.');
      }
      const idx = postsStore.findIndex((p) => p.id === postDetailMatch[1]);
      if (idx === -1) return sendError(res, 404, 'POST_NOT_FOUND', 'Post not found.');
      if (postsStore[idx].post_type !== 'FORUM_TOPIC') {
        return sendError(res, 409, 'POST_NOT_DELETABLE', 'Official notices cannot be deleted.');
      }
      postsStore.splice(idx, 1);
      res.statusCode = 204;
      return res.end();
    }

    // 16. POST /api/v1/communication/posts/{post_id}/comments
    const commentMatch = pathname.match(/^\/api\/v1\/communication\/posts\/([^/]+)\/comments$/);
    if (commentMatch && method === 'POST') {
      const post = postsStore.find((p) => p.id === commentMatch[1]);
      if (!post) return sendError(res, 404, 'POST_NOT_FOUND', 'Post not found.');
      if (!post.comments_enabled) {
        return sendError(res, 409, 'COMMENTS_DISABLED', 'Comments are disabled on this post.');
      }
      const body = await readBody(req);
      const comment: InternalComment = {
        id: `018f0000-0000-7000-8000-${Math.floor(100000000000 + Math.random() * 899999999999)}`,
        post_id: post.id,
        body: body.body || '',
        author_user_id: user.user_id,
        author_apartment_id: body.author_apartment_id || null,
        created_at: new Date().toISOString(),
      };
      post.comments.push(comment);
      return sendJson(res, 201, {
        id: comment.id,
        post_id: post.id,
        body: comment.body,
        author_badge: resolveAuthorBadge(comment.author_user_id),
        is_mine: true,
        created_at: comment.created_at,
      });
    }

    return sendError(res, 404, 'NOT_FOUND', `Endpoint ${method} ${pathname} not found.`);
  };
}
