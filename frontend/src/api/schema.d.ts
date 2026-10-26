/**
 * Generated from docs/openapi.json (OpenAPI 3.1.0 - Apartment Association Platform v0.1.0)
 * Updated with PR #31: GET /api/v1/property/building, GET /api/v1/meters/periods,
 * and average_delta / previous_delta on DashboardMeter.
 */

export type BuildingRole = 'CHAIRMAN' | 'BOARD' | 'ACCOUNTANT' | 'USER'
export type DisplayMode = 'APARTMENT_ONLY' | 'INITIALS' | 'FULL_NAME'
export type RelationType = 'OWNER' | 'TENANT' | 'RESIDENT'
export type RoleBadge = 'OWNER' | 'RESIDENT' | 'BOARD' | 'ACCOUNTANT'
export type MeterUnit = 'm3' | 'kWh' | 'MWh'
export type PeriodStatus = 'OPEN_FOR_RESIDENTS' | 'REVIEW_BY_BOARD' | 'LOCKED'
export type ReadingSource = 'RESIDENT' | 'ADMIN_OVERRIDE' | 'AUTO_ESTIMATED' | 'IOT_WEBHOOK'
export type ResourceType = 'COLD_WATER' | 'HOT_WATER' | 'ELECTRICITY' | 'HEAT' | 'GAS'
export type SubmissionStatus = 'SUBMITTED' | 'MISSING' | 'AUTO_ESTIMATED' | 'ANOMALY_CONFIRMED'
export type PostType = 'EMERGENCY' | 'OFFICIAL_NOTICE' | 'FORUM_TOPIC'
export type PostCategory = 'GENERAL' | 'WATER_SHUTOFF' | 'MAINTENANCE' | 'MARKETPLACE'
export type TargetScope = 'ALL_BUILDING' | 'ENTRANCE' | 'APARTMENT_LIST'

export interface AuthorBadgeDTO {
  display_label: string
  role_badge: RoleBadge
}

export interface ValidationErrorItem {
  loc: Array<string | number>
  message: string
  type: string
}

export interface ErrorResponse {
  error_code: string
  message: string
  errors?: ValidationErrorItem[] | null
  trace_id?: string | null
  [key: string]: unknown
}

export interface DemoUserView {
  user_id: string
  label: string
  building_role: BuildingRole
  apartment_number: string | null
  relation_type: RelationType | null
}

export interface BuildingResponse {
  building_id?: string
  id?: string
  name: string
  registry_code: string | null
  address: string
  district?: string | null
  apartments_count?: number
  total_apartments?: number
  entrances_count?: number
  total_entrances?: number
  heated_area_m2?: string
  total_area_m2?: string
}

export interface MembershipView {
  membership_id: string
  apartment_id: string
  apartment_number: string
  entrance: number
  floor: number
  relation_type: RelationType
  can_submit_readings: boolean
  can_view_bills: boolean
  can_vote: boolean
  privacy_display_mode: DisplayMode
  preview_badge: AuthorBadgeDTO
}

export interface MeResponse {
  user_id: string
  building_id: string
  building_role: BuildingRole
  privacy_display_mode: DisplayMode
  memberships: MembershipView[]
}

export interface PrivacyUpdateRequest {
  privacy_display_mode: DisplayMode
}

export interface PrivacyUpdateResponse {
  membership_id: string
  privacy_display_mode: DisplayMode
  preview_badge: AuthorBadgeDTO
}

export interface PeriodDTO {
  period_code: string
  status: PeriodStatus
  due_date: string
}

export interface ReadingSummary {
  reading_id: string
  value: string
  consumption_delta: string
  source: ReadingSource
  is_anomaly_confirmed: boolean
}

export interface DashboardMeter {
  meter_id: string
  resource_type: ResourceType
  serial_number: string
  location_label: string
  unit: MeterUnit
  previous_value: string
  previous_delta?: string | null
  average_delta?: string | null
  reading: ReadingSummary | null
}

export interface DashboardApartment {
  apartment_id: string
  apartment_number: string
  can_submit_readings: boolean
  meters: DashboardMeter[]
}

export interface DashboardResponse {
  period: PeriodDTO | null
  apartments: DashboardApartment[]
}

export interface ReadingSubmitRequest {
  meter_id: string
  period_code: string
  value: string
  confirmation_level?: number
  reason?: string | null
}

export interface ReadingResponse {
  reading_id: string
  meter_id: string
  period_code: string
  value: string
  consumption_delta: string
  source: ReadingSource
  is_anomaly_confirmed: boolean
}

export interface HistoryEntry {
  changed_at: string
  old_value: string | null
  new_value: string
  source: ReadingSource
  reason: string | null
  changed_by: AuthorBadgeDTO | null
}

export interface ReadingHistoryResponse {
  reading_id: string
  meter_id: string
  period_code: string
  entries: HistoryEntry[]
}

export interface ResourceBalance {
  resource_type: ResourceType
  unit: MeterUnit
  master_total: string | null
  apartments_sum: string
  unallocated_loss: string | null
}

export interface MasterMeterRow {
  meter_id: string
  resource_type: ResourceType
  location_label: string
  unit: MeterUnit
  previous_value: string
  status: SubmissionStatus
  reading: ReadingSummary | null
}

export interface MatrixMeter {
  meter_id: string
  resource_type: ResourceType
  location_label: string
  unit: MeterUnit
  status: SubmissionStatus
  reading: ReadingSummary | null
}

export interface MatrixApartment {
  apartment_id: string
  apartment_number: string
  entrance: number
  floor: number
  status: SubmissionStatus
  meters: MatrixMeter[]
}

export interface MatrixResponse {
  period: PeriodDTO
  balances: ResourceBalance[]
  master_meters: MasterMeterRow[]
  apartments: MatrixApartment[]
}

export interface EstimatedReading {
  reading_id: string
  meter_id: string
  apartment_id: string
  value: string
  consumption_delta: string
}

export interface EstimateResponse {
  period_code: string
  estimated_count: number
  estimated: EstimatedReading[]
}

export interface PeriodStatusUpdateRequest {
  status: PeriodStatus
}

export interface PeriodStatusUpdateResponse {
  period_code: string
  previous_status: PeriodStatus
  status: PeriodStatus
}

export interface EmergencyView {
  id: string
  post_type: PostType
  category: PostCategory
  title: string
  body: string
  author_badge: AuthorBadgeDTO
  is_mine: boolean
  target_scope: TargetScope
  target_entrance: number | null
  target_apartment_ids: string[] | null
  is_active: boolean
  comments_enabled: boolean
  comments_count: number
  created_at: string
  acknowledged_at: string | null
}

export interface AcknowledgeResponse {
  post_id: string
  acknowledged_at: string
}

export interface ResolveResponse {
  post_id: string
  is_active: boolean
}

export interface PostView {
  id: string
  post_type: PostType
  category: PostCategory
  title: string
  body: string
  author_badge: AuthorBadgeDTO
  is_mine: boolean
  target_scope: TargetScope
  target_entrance: number | null
  target_apartment_ids: string[] | null
  is_active: boolean
  comments_enabled: boolean
  comments_count: number
  created_at: string
}

export interface CommentView {
  id: string
  post_id: string
  body: string
  author_badge: AuthorBadgeDTO
  is_mine: boolean
  created_at: string
}

export interface PostDetail extends PostView {
  comments: CommentView[]
}

export interface PostCreateRequest {
  post_type: PostType
  category?: PostCategory
  title: string
  body: string
  target_scope?: TargetScope
  target_entrance?: number | null
  target_apartment_ids?: string[] | null
  comments_enabled?: boolean
  author_apartment_id?: string | null
}

export interface CommentCreateRequest {
  body: string
  author_apartment_id?: string | null
}
