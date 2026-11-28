# Copyright (c) 2023, healthcare and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import random_string

from healthcare.healthcare.doctype.specimen.specimen import accession_specimen

# Fixtures are built inline in _make_lab_test, so skip the framework-generated records that pull in ERPNext.
IGNORE_TEST_RECORD_DEPENDENCIES = [
	"Patient",
	"Lab Test",
	"Lab Test Sample",
	"Medical Department",
	"User",
	"Gender",
]


def _insert(values):
	"""Insert a fixture as Administrator, skipping mandatory checks the tests do not read."""
	return frappe.get_doc(values).insert(ignore_permissions=True, ignore_mandatory=True)


def _make_lab_test():
	"""Return a Lab Test whose department comes from its template (fetch_from)."""
	suffix = random_string(6)
	customer_group = frappe.get_all("Customer Group", filters={"is_group": 0}, pluck="name", limit=1)[0]
	department = _insert({"doctype": "Medical Department", "department": f"_Accession Dept {suffix}"}).name
	item = _insert(
		{"doctype": "Item", "item_code": f"_Accession Item {suffix}", "item_group": "All Item Groups"}
	).name
	template = _insert(
		{
			"doctype": "Lab Test Template",
			"lab_test_name": f"_Accession Test {suffix}",
			"lab_test_group": "All Item Groups",
			"department": department,
			"item": item,
			"is_billable": 0,
		}
	).name
	patient = _insert(
		{
			"doctype": "Patient",
			"first_name": f"_Accession Patient {suffix}",
			"sex": "Female",
			"customer_group": customer_group,
		}
	).name
	return _insert(
		{"doctype": "Lab Test", "patient": patient, "patient_sex": "Female", "template": template}
	).name


class TestSpecimen(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.lab_test = _make_lab_test()
		cls.department = frappe.db.get_value("Lab Test", cls.lab_test, "department")

	def _specimen(self, with_test=True):
		patient = frappe.db.get_value("Lab Test", self.lab_test, "patient")
		values = {"doctype": "Specimen", "patient": patient}
		if with_test:
			values["lab_test"] = self.lab_test
		return _insert(values)

	def test_accept_routes_to_department_and_test_and_audits(self):
		specimen = self._specimen()

		result = accession_specimen(specimen.name, "Accept")

		specimen.reload()
		self.assertEqual(specimen.accession_status, "Accepted")
		self.assertEqual(specimen.department, self.department)
		self.assertEqual(specimen.accessioned_by, frappe.session.user)
		self.assertEqual(result["accession_status"], "Accepted")

		entry = frappe.get_last_doc("LIMS Audit Entry", filters={"reference_name": specimen.name})
		self.assertEqual(entry.action, "Specimen Accepted")
		self.assertEqual(entry.reference_doctype, "Specimen")
		self.assertFalse(entry.reason)

	def test_reject_requires_a_reason(self):
		specimen = self._specimen()

		with self.assertRaises(frappe.ValidationError):
			accession_specimen(specimen.name, "Reject", reason="   ")

		specimen.reload()
		self.assertEqual(specimen.accession_status, "Pending")

	def test_reject_records_reason_and_audit_row(self):
		specimen = self._specimen()

		accession_specimen(specimen.name, "Reject", reason="Haemolysed sample")

		specimen.reload()
		self.assertEqual(specimen.accession_status, "Rejected")
		self.assertEqual(specimen.rejection_reason, "Haemolysed sample")
		entry = frappe.get_last_doc("LIMS Audit Entry", filters={"reference_name": specimen.name})
		self.assertEqual(entry.action, "Specimen Rejected")
		self.assertEqual(entry.reason, "Haemolysed sample")

	def test_second_decision_is_refused(self):
		specimen = self._specimen()
		accession_specimen(specimen.name, "Accept")

		with self.assertRaises(frappe.ValidationError):
			accession_specimen(specimen.name, "Reject", reason="Changed my mind")

		specimen.reload()
		self.assertEqual(specimen.accession_status, "Accepted")

	def test_status_cannot_change_by_editing_the_specimen(self):
		specimen = self._specimen()

		specimen.accession_status = "Accepted"
		with self.assertRaises(frappe.ValidationError):
			specimen.save()

	def test_specimen_without_lab_test_is_not_routed(self):
		specimen = self._specimen(with_test=False)

		with self.assertRaises(frappe.ValidationError):
			accession_specimen(specimen.name, "Accept")

		specimen.reload()
		self.assertEqual(specimen.accession_status, "Pending")

	def test_user_without_write_permission_is_refused(self):
		specimen = self._specimen()
		email = f"accession-norole-{random_string(8).lower()}@example.com"
		user = frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": "Accession NoRole", "send_welcome_email": 0}
		).insert(ignore_permissions=True)
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user(user.email)

		with self.assertRaises(frappe.PermissionError):
			accession_specimen(specimen.name, "Accept")
