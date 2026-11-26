"""Permission matrix tests for the Phase 1 lab DocTypes (BR-1.1).

MATRIX is the single source for the grants in requirements.md Section 4.
The tests iterate it, so no assert is written per cell (AC-1).
A letter listed for a role is allowed (AC-2). Every other letter is denied (AC-3).
A role with no entry for a DocType holds no permission on it.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import random_string

from healthcare.healthcare.doctype.observation.observation import set_observation_status
from healthcare.permissions import observation_has_permission

# Matrix column key -> Frappe role. NR is a user with no role beyond All and Guest.
ROLE_KEYS = {
	"SM": "System Manager",
	"HA": "Healthcare Administrator",
	"LU": "Laboratory User",
	"LTA": "LabTest Approver",
	"PH": "Physician",
	"NU": "Nursing User",
	"PT": "Patient",
	"LM": "Lab Manager",
	"NR": None,
}

# Matrix letter -> Frappe permission type. P is print.
PTYPES = {
	"R": "read",
	"C": "create",
	"W": "write",
	"D": "delete",
	"S": "submit",
	"X": "cancel",
	"A": "amend",
	"P": "print",
}
ALL_LETTERS = "".join(PTYPES)

# Phase 1 DocTypes from Section 2. AC-5 fails if any of them has no matrix row.
# Sales Invoice is not listed: ERPNext grants read to All, so the no-role check (AC-4) does not
# apply to it. Its lab-role refusals stay in R-INV (D-10).
PHASE1_DOCTYPES = (
	"Lab Test",
	"Lab Test Template",
	"Lab Test Sample",
	"Sample Collection",
	"Specimen",
	"Observation",
	"Diagnostic Report",
	"Patient",
	"Sample Type",
)

# Each row: "allow" maps a matrix key to granted letters; a role not listed holds nothing.
# A row with "deny" lists the refused letters per role and skips the allow check.
# Sales Invoice is ERPNext standard: only the refusals in D-10 are checked here.
# Observation HA is RP: the lab-scoped hook refuses HA writes on lab results (Q-2 = B).
# R-AUD is pending until the BR-1.4 audit DocType exists (AC-18).
MATRIX = [
	{
		"ref": "R-LT",
		"doctype": "Lab Test",
		"allow": {"SM": "RCWDSXAP", "LU": "RCWP", "LTA": "RWSXAP", "PH": "RP", "LM": "RP"},
	},
	{
		"ref": "R-TT",
		"doctype": "Lab Test Template",
		"allow": {"SM": "RCWDP", "HA": "RCWDP", "LU": "RP", "LTA": "RP", "LM": "RCWP"},
	},
	{
		"ref": "R-LTS",
		"doctype": "Lab Test Sample",
		"allow": {"SM": "RCWDP", "HA": "RCWP", "LU": "RP", "LTA": "RP", "LM": "RCWP"},
	},
	{
		"ref": "R-SC",
		"doctype": "Sample Collection",
		"allow": {"SM": "RCWDSXAP", "LU": "RCWSP", "LTA": "RP", "LM": "RP"},
	},
	{
		"ref": "R-SP",
		"doctype": "Specimen",
		"allow": {"SM": "RCWDP", "LU": "RCWP", "LTA": "RWP", "LM": "RCWP"},
	},
	{
		"ref": "R-OB",
		"doctype": "Observation",
		"allow": {"SM": "RCWDSXAP", "HA": "RP", "LU": "RCWP", "LTA": "RWSP", "PH": "RP"},
	},
	{
		"ref": "R-DR",
		"doctype": "Diagnostic Report",
		"allow": {"SM": "RCWDP", "LU": "RCP", "LTA": "RP", "PH": "RP", "LM": "RP"},
	},
	{
		"ref": "R-PT",
		"doctype": "Patient",
		"allow": {
			"SM": "RCWDP",
			"HA": "RCWP",
			"LU": "RP",
			"LTA": "RP",
			"PH": "RCWP",
			"NU": "RWP",
			"LM": "RP",
		},
	},
	{"ref": "R-ST", "doctype": "Sample Type", "allow": {"SM": "RCWDP"}},
	{
		"ref": "R-INV",
		"doctype": "Sales Invoice",
		"allow": {},
		"deny": {"LU": "CS", "PH": "CS", "NU": "CS", "PT": "CS"},
	},
	{"ref": "R-AUD", "doctype": "Audit Trail (BR-1.4)", "status": "pending"},
]

LAB_LINK_TARGETS = {
	"Lab Test",
	"Lab Test Template",
	"Lab Test Sample",
	"Lab Test Report",
	"Lab Prescription",
	"Sample Collection",
	"Sample Type",
	"Specimen",
	"Observation",
	"Observation Template",
	"Diagnostic Report",
}
LAB_WORKSPACE = "Laboratory"
# Shared workspaces must carry no lab link (AC-16). Practitioner is the Physician workspace.
SHARED_WORKSPACES = ("Healthcare", "Setup", "Reception", "Diagnostics", "Practitioner")


def _denied_letters(row, key):
	"""Return the letters `key` must be refused on `row`.

	Explicit "deny" wins. Otherwise every letter the matrix does not grant is denied.
	"""
	if "deny" in row:
		return row["deny"].get(key, "")
	granted = row["allow"].get(key, "")
	return "".join(letter for letter in ALL_LETTERS if letter not in granted)


def _live_rows():
	"""Return the MATRIX rows to check. Pending rows (R-AUD) are skipped."""
	return [row for row in MATRIX if row.get("status") != "pending"]


def _make_user(key, role):
	"""Create a user holding only `role`. NR (role None) holds no role.

	The email is random so reruns on the same site do not clash.
	"""
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": f"lims-w1-{key.lower()}-{random_string(8).lower()}@example.com",
			"first_name": f"LIMS W1 {key}",
			"roles": [{"role": role}] if role else [],
			"new_password": random_string(20),
		}
	)
	user.flags.no_welcome_mail = True
	user.insert(ignore_permissions=True)
	return user.email


def _insert(values):
	"""Insert a fixture as Administrator. Mandatory checks are skipped; only the fields the tests read are set."""
	return frappe.get_doc(values).insert(ignore_permissions=True, ignore_mandatory=True)


def _make_lab_test(patient, template, with_result=False):
	"""Insert a draft Lab Test. `with_result` adds one descriptive result row (AC-14 needs one)."""
	values = {"doctype": "Lab Test", "patient": patient, "patient_sex": "Female", "template": template}
	if with_result:
		values["descriptive_test_items"] = [
			{"lab_test_particulars": "Result", "result_value": "Positive", "require_result_value": 1}
		]
	return _insert(values).name


def _make_observation(patient, template, company, category):
	"""Insert a draft Observation in the given `observation_category`."""
	return _insert(
		{
			"doctype": "Observation",
			"patient": patient,
			"observation_template": template,
			"company": company,
			"observation_category": category,
		}
	).name


def _make_fixtures():
	"""Return one draft record per Phase 1 DocType, keyed by DocType name.

	Sales Invoice is absent: it is checked at DocType level, without a document.
	"""
	# Built inline: importing the other test modules runs ERPNext master-data setup at import time.
	# Patient creates a Customer, so pick a leaf customer group (Selling Settings default may be a group).
	customer_group = frappe.get_all("Customer Group", filters={"is_group": 0}, pluck="name", limit=1)[0]
	patient = _insert(
		{
			"doctype": "Patient",
			"first_name": "_LIMS W1 Patient",
			"sex": "Female",
			"customer_group": customer_group,
		}
	).name
	# The template category wins on save, so vitals need their own Vital Signs template.
	template = _insert(
		{
			"doctype": "Observation Template",
			"observation": "_LIMS W1 Observation",
			"abbr": "W1LAB",
			"observation_category": "Laboratory",
		}
	).name
	vitals_template = _insert(
		{
			"doctype": "Observation Template",
			"observation": "_LIMS W1 Vitals",
			"abbr": "W1VIT",
			"observation_category": "Vital Signs",
		}
	).name
	company = frappe.get_all("Company", pluck="name", limit=1)[0]
	# Template after_insert creates an Item unless one is linked, so link a fixture Item.
	item = _insert(
		{"doctype": "Item", "item_code": "_LIMS W1 Test Item", "item_group": "All Item Groups"}
	).name
	lab_template = _insert(
		{
			"doctype": "Lab Test Template",
			"lab_test_name": "_LIMS W1 Test",
			"lab_test_group": "All Item Groups",
			"item": item,
			"is_billable": 0,
		}
	).name
	return {
		"Patient": patient,
		"Lab Test": _make_lab_test(patient, lab_template, with_result=True),
		"Lab Test Template": lab_template,
		"Lab Test Sample": _insert({"doctype": "Lab Test Sample", "sample": "_LIMS W1 Sample"}).name,
		"Sample Type": _insert({"doctype": "Sample Type", "sample_type": "_LIMS W1 Sample Type"}).name,
		"Sample Collection": _insert({"doctype": "Sample Collection", "patient": patient}).name,
		"Specimen": _insert({"doctype": "Specimen", "patient": patient}).name,
		"Diagnostic Report": _insert({"doctype": "Diagnostic Report", "patient": patient}).name,
		"Observation": _make_observation(patient, template, company, "Laboratory"),
		"Observation (vitals)": _make_observation(patient, vitals_template, company, "Vital Signs"),
	}


class TestLabPermissionMatrix(IntegrationTestCase):
	"""Runs MATRIX against real users and fixture records."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.emails = {key: _make_user(key, role) for key, role in ROLE_KEYS.items()}
		cls.fixtures = _make_fixtures()

	def _can(self, key, doctype, ptype, doc=None):
		"""Return True if the user for `key` holds `ptype` on `doctype` (and `doc` when given)."""
		return bool(frappe.has_permission(doctype, ptype, doc=doc, user=self.emails[key]))

	def _doc_for(self, doctype):
		"""Return the fixture name for `doctype`, or None for doctype-level checks."""
		return self.fixtures.get(doctype)

	def _run_as(self, key):
		"""Switch the session user to the user for `key`. Tests reset it on cleanup."""
		frappe.set_user(self.emails[key])
		self.addCleanup(frappe.set_user, "Administrator")

	def test_matrix_covers_phase1_doctypes(self):
		"""AC-5: every Phase 1 DocType has a matrix row. A new DocType needs one."""
		rows = {row["doctype"] for row in _live_rows()}
		self.assertEqual(sorted(set(PHASE1_DOCTYPES) - rows), [])

	def test_allowed_cells(self):
		"""AC-2: each letter a role holds in the matrix is granted on the fixture."""
		for row in _live_rows():
			for key, letters in row["allow"].items():
				for letter in letters:
					with self.subTest(ref=row["ref"], role=key, ptype=PTYPES[letter]):
						doc = self._doc_for(row["doctype"])
						self.assertTrue(self._can(key, row["doctype"], PTYPES[letter], doc))

	def test_denied_cells(self):
		"""AC-3: each letter not granted (or listed in deny) is refused with the same user."""
		for row in _live_rows():
			for key in ROLE_KEYS:
				for letter in _denied_letters(row, key):
					with self.subTest(ref=row["ref"], role=key, ptype=PTYPES[letter]):
						doc = self._doc_for(row["doctype"])
						self.assertFalse(self._can(key, row["doctype"], PTYPES[letter], doc))

	def test_no_role_user_refused_all_reads(self):
		"""AC-4: a user with no role is refused read on every Phase 1 DocType (Sales Invoice excluded, D-10)."""
		for doctype in PHASE1_DOCTYPES:
			with self.subTest(doctype=doctype):
				self.assertFalse(self._can("NR", doctype, "read", self._doc_for(doctype)))

	def test_lab_test_entry_and_approval(self):
		"""AC-14: Laboratory User saves results but cannot submit; LabTest Approver submits (D-1)."""
		name = _make_lab_test(self.fixtures["Patient"], self.fixtures["Lab Test Template"], with_result=True)

		self._run_as("LU")
		lab_test = frappe.get_doc("Lab Test", name)
		lab_test.save()
		with self.assertRaises(frappe.PermissionError):
			lab_test.submit()

		self._run_as("LTA")
		frappe.get_doc("Lab Test", name).submit()
		self.assertEqual(frappe.db.get_value("Lab Test", name, "docstatus"), 1)

	def test_submitted_lab_test_amend_and_cancel(self):
		"""AC-15: Laboratory User cannot cancel or amend a submitted Lab Test; LabTest Approver can.

		Frappe has no server-side amend call. The desk gates amend on the amend ptype, so this
		test checks that ptype with has_permission.
		"""
		name = _make_lab_test(self.fixtures["Patient"], self.fixtures["Lab Test Template"], with_result=True)
		self._run_as("LTA")
		frappe.get_doc("Lab Test", name).submit()

		self._run_as("LU")
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("Lab Test", name).cancel()
		self.assertFalse(self._can("LU", "Lab Test", "amend", name))

		self.assertTrue(self._can("LTA", "Lab Test", "amend", name))

	def test_observation_hook_lab_vs_vitals(self):
		"""The Observation hook refuses lab writes for Healthcare Administrator but not vitals.

		Vitals return True from the hook (no objection), so the HA grant still applies to them.
		"""
		lab = frappe.get_doc("Observation", self.fixtures["Observation"])
		vital = frappe.get_doc("Observation", self.fixtures["Observation (vitals)"])
		ha = self.emails["HA"]

		self.assertFalse(observation_has_permission(lab, "write", user=ha))
		self.assertTrue(observation_has_permission(vital, "write", user=ha))
		self.assertFalse(self._can("HA", "Observation", "write", lab.name))
		self.assertTrue(self._can("HA", "Observation", "write", vital.name))
		self.assertTrue(self._can("HA", "Observation", "create", vital.name))

	def test_observation_delete_lab_vs_vitals(self):
		"""D-3 and Q-2 = B: HA cannot delete a lab Observation (hook) but keeps its vitals delete grant."""
		lab = self.fixtures["Observation"]
		vital = self.fixtures["Observation (vitals)"]

		lab_doc = frappe.get_doc("Observation", lab)
		self.assertFalse(observation_has_permission(lab_doc, "delete", user=self.emails["HA"]))
		self.assertFalse(self._can("HA", "Observation", "delete", lab))
		self.assertTrue(self._can("HA", "Observation", "delete", vital))

	def _make_submitted_lab_observation(self):
		"""Return a submitted lab Observation with a result, the state the approval step works on."""
		template = frappe.db.get_value("Observation", self.fixtures["Observation"], "observation_template")
		name = _insert(
			{
				"doctype": "Observation",
				"patient": self.fixtures["Patient"],
				"observation_template": template,
				"company": frappe.get_all("Company", pluck="name", limit=1)[0],
				"observation_category": "Laboratory",
				"result_text": "Positive",
			}
		).name
		frappe.get_doc("Observation", name).submit()
		return name

	def test_lab_observation_reject(self):
		"""Reject on a lab Observation: LabTest Approver can, Laboratory User cannot (D-7).

		The reject cancels the observation and inserts one fresh draft copy. The test checks
		those outcomes, not the calls the controller makes.
		"""
		name = self._make_submitted_lab_observation()
		patient = self.fixtures["Patient"]
		template = frappe.db.get_value("Observation", name, "observation_template")

		self._run_as("LU")
		with self.assertRaises(frappe.PermissionError):
			set_observation_status(name, "Rejected", reason="Wrong sample")
		self.assertEqual(frappe.db.get_value("Observation", name, "docstatus"), 1)

		self._run_as("LTA")
		drafts_before = frappe.db.count(
			"Observation", {"patient": patient, "observation_template": template, "docstatus": 0}
		)
		set_observation_status(name, "Rejected", reason="Wrong sample")
		self.assertEqual(frappe.db.get_value("Observation", name, "docstatus"), 2)
		self.assertEqual(frappe.db.get_value("Observation", name, "status"), "Rejected")
		drafts_after = frappe.db.count(
			"Observation", {"patient": patient, "observation_template": template, "docstatus": 0}
		)
		self.assertEqual(drafts_after - drafts_before, 1)

	def test_docshare_does_not_bypass_lab_observation_matrix(self):
		"""A DocShare write, submit or share right does not let a refused role act on a lab Observation.

		Physician holds no write on lab Observation (D-2) and Laboratory User holds no submit or
		share (R-OB). Each is shared the right, then refused by the matrix. Refused means the
		action raises PermissionError or the check returns False.
		"""
		name = _make_observation(
			self.fixtures["Patient"],
			frappe.db.get_value("Observation", self.fixtures["Observation"], "observation_template"),
			frappe.get_all("Company", pluck="name", limit=1)[0],
			"Laboratory",
		)
		frappe.share.add("Observation", name, self.emails["PH"], read=1, write=1)
		frappe.share.add("Observation", name, self.emails["LU"], read=1, submit=1, share=1)

		self._run_as("PH")
		with self.assertRaises(frappe.PermissionError):
			doc = frappe.get_doc("Observation", name)
			doc.result_text = "Shared write attempt"
			doc.save()

		self._run_as("LU")
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("Observation", name).submit()
		self.assertEqual(frappe.db.get_value("Observation", name, "docstatus"), 0)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("Observation", name).check_permission("share")

	def test_lab_manager_role_exists(self):
		"""Phase 1: the Lab Manager role exists after migrate (Q-1 = A)."""
		self.assertTrue(frappe.db.exists("Role", ROLE_KEYS["LM"]))

	def test_lab_manager_can_read_item(self):
		"""BR-1.10 (A5): Lab Manager reads Item so the consumable link picker works. No write, no delete."""
		lm = self.emails["LM"]
		self.assertTrue(frappe.has_permission("Item", "read", user=lm))
		self.assertFalse(frappe.has_permission("Item", "write", user=lm))
		self.assertFalse(frappe.has_permission("Item", "delete", user=lm))

	def test_lab_workspace_visibility(self):
		"""AC-16: the Laboratory workspace is visible to the lab roles and System Manager (amended AC-16).

		Shared workspaces (Healthcare, Setup, Reception, Diagnostics, Practitioner) carry no lab link.
		"""
		lab_roles = {row.role for row in frappe.get_doc("Workspace", LAB_WORKSPACE).roles}
		self.assertEqual(lab_roles, {"System Manager", "Laboratory User", "LabTest Approver", "Lab Manager"})

		for name in SHARED_WORKSPACES:
			with self.subTest(workspace=name):
				links = {row.link_to for row in frappe.get_doc("Workspace", name).links}
				self.assertFalse(links & LAB_LINK_TARGETS)

		# Sample Type and Lab Test Report lost their shared-workspace links, so the Laboratory workspace must carry them.
		lab_links = {row.link_to: row.link_type for row in frappe.get_doc("Workspace", LAB_WORKSPACE).links}
		self.assertTrue({"Sample Type", "Lab Test Report"} <= set(lab_links))
		# Lab Test Report is a Script Report; typing it as a DocType gives a dead link.
		self.assertEqual(lab_links["Lab Test Report"], "Report")

	def test_audit_trail_row_pending(self):
		"""AC-18: R-AUD is a pending row. It skips and does not fail the run until BR-1.4 creates the DocType."""
		self.skipTest("R-AUD pending: audit DocType (BR-1.4) not created yet")
