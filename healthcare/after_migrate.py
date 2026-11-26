import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute_migrate():
    custom_fields = {

        "Notification" : [
			{
				"fieldname": "is_recurring_appointment",
				"label": "Is Recurring Appointments Notifications ?",
				"fieldtype": "Check",
				"insert_after": "channel",
			},
		],
		"Patient Appointment" : [
			{
				"fieldname": "recurring_appointments",
				"label": "Recurring Appointments",
				"fieldtype": "Check",
				"hidden" : 1
			}
		],
		"Sales Invoice": [
			{
				"fieldname": "total_insurance_coverage_amount",
				"label": "Total Insurance Coverage Amount",
				"fieldtype": "Currency",
				"insert_after": "total",
				"read_only": True,
				"no_copy": True,
				"in_standard_filter": 1,
			},
			{
				"fieldname": "patient_payable_amount",
				"label": "Patient Payable Amount",
				"fieldtype": "Currency",
				"insert_after": "total_insurance_coverage_amount",
				"read_only": True,
				"no_copy": True,
			},
		],
		"Sales Invoice Item": [
			{
				"fieldname": "healthcare_insurance_section",
				"fieldtype": "Section Break",
				"insert_after": "is_free_item",
			},
			{
				"fieldname": "coverage_rate",
				"label": "Insurance Coverage Approved Rate",
				"fieldtype": "Currency",
				"insert_after": "healthcare_insurance_section",
				"read_only": True,
				"no_copy": True,
			},
			{
				"fieldname": "coverage_qty",
				"label": "Insurance Coverage Approved Qty",
				"fieldtype": "Float",
				"insert_after": "coverage_rate",
				"read_only": True,
				"no_copy": True,
			},
			{
				"fieldname": "coverage_percentage",
				"label": "Insurance Coverage %",
				"fieldtype": "Percent",
				"insert_after": "coverage_qty",
				"read_only": True,
				"no_copy": True,
			},
			{
				"fieldname": "insurance_coverage_amount",
				"label": "Insurance Coverage Amount",
				"fieldtype": "Currency",
				"insert_after": "coverage_percentage",
				"read_only": True,
				"no_copy": True,
			},
			{
				"fieldname": "healthcare_insurance_col_break",
				"fieldtype": "Column Break",
				"insert_after": "insurance_coverage_amount",
			},
			{
				"fieldname": "patient_insurance_policy",
				"label": "Patient Insurance Policy Number",
				"fieldtype": "Data",
				"read_only": True,
				"insert_after": "healthcare_insurance_col_break",
			},
			{
				"fieldname": "insurance_coverage",
				"label": "Patient Insurance Coverage",
				"fieldtype": "Link",
				"read_only": True,
				"insert_after": "patient_insurance_policy",
				"options": "Patient Insurance Coverage",
				"no_copy": True,
			},
			{
				"fieldname": "insurance_payor",
				"label": "Insurance Payor",
				"fieldtype": "Link",
				"read_only": True,
				"insert_after": "insurance_coverage",
				"options": "Insurance Payor",
				"no_copy": True,
			},
		],
		"Journal Entry": [
			{
				"fieldname": "insurance_coverage",
				"label": "For Insurance Coverage",
				"fieldtype": "Link",
				"options": "Patient Insurance Coverage",
				"insert_after": "due_date",
				"read_only": True,
				"no_copy": True,
			}
		],
		"Payment Entry Reference": [
			{
				"fieldname": "insurance_claim",
				"label": "Insurance Claim",
				"fieldtype": "Link",
				"options": "Insurance Claim",
				"insert_after": "reference_name",
				"read_only": True,
				"no_copy": True,
			},
			{
				"fieldname": "insurance_claim_coverage",
				"label": "Insurance Claim Coverage",
				"fieldtype": "Link",
				"options": "Insurance Claim Coverage",
				"insert_after": "insurance_claim",
				"read_only": True,
				"no_copy": True,
			},
		],
    }

    create_custom_fields(custom_fields)
    ensure_lab_manager_role()
    ensure_lab_manager_item_read()

    print("Custom fields for Healthcare Insurance and Referral Practitioner Integration created successfully")


def ensure_lab_manager_role():
    """Create the "Lab Manager" role if it is missing. Runs on every `bench migrate`.

    healthcare/setup.py does not create this role, so migrate is the place that creates it,
    on fresh installs and on existing sites alike. It is idempotent: an existing role is left
    unchanged, so re-running migrate is safe.
    """
    if frappe.db.exists("Role", "Lab Manager"):
        return
    frappe.get_doc({"doctype": "Role", "role_name": "Lab Manager", "desk_access": 1}).insert(
        ignore_permissions=True
    )


def ensure_lab_manager_item_read():
    """Grant the "Lab Manager" role read-only access to Item. Runs on every `bench migrate`.

    The consumables table on Lab Test Template links to Item. Without read on Item, the link
    picker cannot search stock items for a Lab Manager. Only read is granted: no write, no delete.

    Frappe uses Custom DocPerm rows instead of standard DocPerm once any exist for a DocType. So
    before the first Custom DocPerm row is written, the standard Item rows are copied across. Otherwise
    every other role would lose its Item access. The copy runs only while no Custom DocPerm rows
    exist for Item apart from Lab Manager's, so later migrates leave it alone.
    """
    custom_rows = frappe.get_all("Custom DocPerm", filters={"parent": "Item"}, pluck="role")
    if set(custom_rows) - {"Lab Manager"}:
        _ensure_lab_manager_item_row()
        return
    _copy_standard_item_perms(exclude_roles=set(custom_rows))
    _ensure_lab_manager_item_row()


def _copy_standard_item_perms(exclude_roles):
    """Copy every standard DocPerm row on Item into Custom DocPerm, skipping roles already present."""
    meta_fields = {"name", "owner", "creation", "modified", "modified_by", "docstatus", "idx"}
    for perm in frappe.get_all("DocPerm", filters={"parent": "Item"}, fields="*"):
        if perm.role in exclude_roles:
            continue
        row = {key: value for key, value in perm.items() if key not in meta_fields}
        frappe.get_doc({**row, "doctype": "Custom DocPerm"}).insert(ignore_permissions=True)


def _ensure_lab_manager_item_row():
    if frappe.db.exists("Custom DocPerm", {"parent": "Item", "role": "Lab Manager", "permlevel": 0}):
        return
    frappe.get_doc(
        {"doctype": "Custom DocPerm", "parent": "Item", "role": "Lab Manager", "permlevel": 0, "read": 1}
    ).insert(ignore_permissions=True)
