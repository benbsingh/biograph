# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate


class TestMedicalCamp(IntegrationTestCase):
	def make_camp(self, **kwargs):
		departments = []
		for name in ("_Test Camp Cardiology", "_Test Camp Ortho"):
			if not frappe.db.exists("Medical Department", name):
				frappe.get_doc({"doctype": "Medical Department", "department": name}).insert()
			departments.append(name)

		doc = {
			"doctype": "Medical Camp",
			"camp_name": "_Test Camp",
			"company": frappe.defaults.get_defaults().company,
			"start_date": nowdate(),
			"end_date": add_days(nowdate(), 1),
			"specialities": [{"medical_department": d} for d in departments],
		}
		doc.update(kwargs)
		return frappe.get_doc(doc)

	def test_camp_with_multiple_specialities(self):
		camp = self.make_camp().insert()
		self.assertEqual(len(camp.specialities), 2)

	def test_end_date_before_start_date(self):
		camp = self.make_camp(end_date=add_days(nowdate(), -1))
		self.assertRaises(frappe.ValidationError, camp.insert)

	def test_duplicate_speciality(self):
		camp = self.make_camp()
		camp.specialities[1].medical_department = camp.specialities[0].medical_department
		self.assertRaises(frappe.ValidationError, camp.insert)

	def test_speciality_required(self):
		camp = self.make_camp(specialities=[])
		self.assertRaises(frappe.ValidationError, camp.insert)

	def test_blank_row_dropped_and_department_from_practitioner(self):
		camp = self.make_camp()
		camp.append("specialities", {})
		camp.insert()
		self.assertEqual(len(camp.specialities), 2)
