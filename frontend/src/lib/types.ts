// Shapes returned by the backend API.

export type DueStatus = "paid" | "partially_paid" | "pending_verification" | "unpaid" | "overdue" | "waived";
export type PaymentStatus = "pending" | "confirmed" | "rejected" | "reversed";

export interface RentTerm {
  id: number;
  amount_paise: number;
  effective_period: string;
  note: string | null;
  created_by: string | null;
}

export interface Tenancy {
  id: number;
  tenant_id: number;
  tenant_name: string;
  flat_id: number;
  flat_label: string;
  building_id: number;
  building_name: string;
  start_date: string;
  end_date: string | null;
  rent_due_day: number;
  billing_start_period: string;
  billing_policy: string | null;
  notes: string | null;
  status: "active" | "ended" | "upcoming";
  current_rent_paise: number | null;
  rent_terms: RentTerm[];
}

export interface Due {
  id: number;
  tenancy_id: number;
  tenant_id: number;
  tenant_name: string;
  flat_id: number;
  flat_label: string;
  building_id: number;
  building_name: string;
  period: string;
  kind: "rent" | "opening_balance";
  description: string | null;
  amount_paise: number;
  due_date: string;
  confirmed_paise: number;
  pending_paise: number;
  outstanding_paise: number;
  available_paise: number;
  status: DueStatus;
  is_overdue: boolean;
  amount_overridden: boolean;
  override_reason: string | null;
  waived_reason: string | null;
}

export interface Allocation {
  rent_due_id: number;
  period: string;
  kind: string;
  amount_paise: number;
  due_date: string;
  is_late: boolean;
}

export interface Payment {
  id: number;
  tenancy_id: number;
  tenant_name: string;
  flat_id: number;
  flat_label: string;
  building_name: string;
  received_date: string;
  amount_paise: number;
  method: "cash" | "online";
  reference: string | null;
  sender_name: string | null;
  status: PaymentStatus;
  status_reason: string | null;
  notes: string | null;
  excess_resolution: string | null;
  excess_note: string | null;
  unallocated_paise: number;
  needs_excess_decision: boolean;
  bank_transaction_id: number | null;
  corrects_payment_id: number | null;
  recorded_by: string | null;
  created_at: string;
  confirmed_by: string | null;
  confirmed_at: string | null;
  voided_by: string | null;
  voided_at: string | null;
  allocations: Allocation[];
}

export interface FlatCard {
  id: number;
  label: string;
  floor_index: number;
  floor_label: string;
  position: number;
  notes: string | null;
  occupancy: "tenant_occupied" | "vacant" | "owner_occupied";
  tenancy: Tenancy | null;
  period_status: DueStatus | null;
  period_due_paise: number;
  period_confirmed_paise: number;
  period_outstanding_paise: number;
  is_overdue: boolean;
}

export interface Building {
  id: number;
  code: string;
  name: string;
  color: string;
  flats: FlatCard[];
}

export interface Tenant {
  id: number;
  full_name: string;
  phone: string | null;
  email: string | null;
  preferred_payment_method: "cash" | "online" | null;
  notes: string | null;
  tenancies: Tenancy[];
  aliases: { id: number; alias: string; kind: string; source: string; is_active: boolean }[];
  status: "active" | "former" | "no_tenancy";
}

export interface DocumentInfo {
  id: number;
  original_name: string;
  content_type: string;
  size_bytes: number;
  category: string;
  uploaded_at: string;
  uploaded_by: string | null;
  is_image: boolean;
  is_pdf: boolean;
  links: { entity_type: string; entity_id: number }[];
}

export interface CashMovement {
  id: number;
  movement_date: string;
  kind: string;
  kind_label: string;
  amount_paise: number;
  from_account: string;
  to_account: string;
  description: string | null;
  status: "active" | "reversed";
  reversed_reason: string | null;
  recorded_by: string | null;
  sources: { rent_payment_id: number; amount_paise: number }[];
}

export interface Expense {
  id: number;
  expense_date: string;
  period: string;
  description: string;
  category_id: number;
  category: string;
  total_paise: number;
  caretaker_paid_paise: number;
  family_paid_paise: number;
  family_payment_method: string | null;
  flat_id: number | null;
  vendor: string | null;
  status: "active" | "reversed";
  reversed_reason: string | null;
  recorded_by: string | null;
}

export interface Discrepancy {
  type: string;
  message: string;
  amount_paise: number;
}

export interface SalaryPayment {
  id: number;
  salary_month: string;
  amount_paise: number;
  payment_date: string;
  method: string;
  bank_reference: string | null;
  status: "pending" | "confirmed" | "cancelled";
  status_reason: string | null;
  notes: string | null;
  recorded_by: string | null;
  confirmed_by: string | null;
}

export interface SalaryMonth {
  period: string;
  expected_paise: number;
  confirmed_paise: number;
  pending_paise: number;
  outstanding_paise: number;
  overpaid_paise: number;
  status: "paid" | "partially_paid" | "pending_verification" | "unpaid";
  payments: SalaryPayment[];
}

export interface Candidate {
  tenancy_id: number;
  tenant_id: number;
  tenant_name: string;
  flat_id: number;
  flat_label: string;
  building_name: string;
  identity_signal: number;
  amount_signal: number;
  reasons: string[];
  outstanding_paise: number;
  proposed_allocations: { rent_due_id: number; period: string; kind: string; amount_paise: number; available_paise: number }[];
  unallocated_paise: number;
}

export interface Suggestion {
  category: "strong" | "possible" | "ambiguous" | "no_match" | "potential_duplicate";
  sender: string | null;
  candidates: Candidate[];
  duplicate_payments: {
    payment_id: number; tenancy_id: number; tenant_name: string; flat_label: string; received_date: string;
    amount_paise: number; status: string; reference: string | null; linked_to_bank_transaction: number | null;
    can_link: boolean;
  }[];
}

export interface BankTxn {
  id: number;
  txn_date: string;
  value_date: string | null;
  narration: string;
  reference: string | null;
  debit_paise: number;
  credit_paise: number;
  balance_paise: number | null;
  review_status: "unreviewed" | "partially_allocated" | "allocated" | "not_rent" | "debit";
  review_note: string | null;
  remainder_not_rent: boolean;
  collision_flag: boolean;
  allocated_paise: number;
  remaining_paise: number;
  sender: string | null;
  payments: { id: number; tenancy_id: number; tenant_name: string; flat_label: string; amount_paise: number;
    status: PaymentStatus; periods: string[]; excess_resolution: string | null }[];
  import_ids: number[];
  suggestion?: Suggestion;
}
