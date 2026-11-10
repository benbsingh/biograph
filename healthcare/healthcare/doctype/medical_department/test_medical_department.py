# Copyright (c) 2015, ESS LLP and Contributors
# See license.txt


import frappe
from frappe.tests import IntegrationTestCase


class TestMedicalDepartment(IntegrationTestCase):
	def test_creation_trims_department_and_generated_name(self):
		for padding in (" ", "\t", "\n", " \t\n"):
			with self.subTest(padding=repr(padding)):
				department = f"Department {frappe.generate_hash(length=10)}"
				doc = frappe.get_doc(
					{"doctype": "Medical Department", "department": f"{padding}{department}{padding}"}
				).insert()
				doc.reload()
				self.assertEqual(doc.department, department)
				self.assertEqual(doc.name, department)

	def test_update_trims_department_without_renaming(self):
		doc = frappe.get_doc(
			{"doctype": "Medical Department", "department": f"Department {frappe.generate_hash(length=10)}"}
		).insert()
		original_name = doc.name
		for padding in (" ", "\t", "\n", " \t\n"):
			with self.subTest(padding=repr(padding)):
				department = f"Department {frappe.generate_hash(length=10)}"
				doc.department = f"{padding}{department}{padding}"
				doc.save()
				doc.reload()
				self.assertEqual(doc.department, department)
				self.assertEqual(doc.name, original_name)

	def test_creation_rejects_blank_department(self):
		for department in (None, "", " ", "\t", "\n"):
			with self.subTest(department=repr(department)):
				name = f"Blank department {frappe.generate_hash(length=10)}"
				doc = frappe.get_doc(
					{"doctype": "Medical Department", "name": name, "department": department}
				)
				with self.assertRaises(frappe.ValidationError):
					doc.insert()
				self.assertFalse(frappe.db.exists("Medical Department", name))

	def test_update_rejects_blank_department_without_persisting(self):
		department = f"Department {frappe.generate_hash(length=10)}"
		doc = frappe.get_doc({"doctype": "Medical Department", "department": department}).insert()
		for blank in (None, "", " ", "\t", "\n"):
			with self.subTest(department=repr(blank)):
				doc = frappe.get_doc("Medical Department", doc.name)
				doc.department = blank
				with self.assertRaises(frappe.ValidationError):
					doc.save()
				self.assertEqual(
					frappe.db.get_value("Medical Department", doc.name, "department"), department
				)

	def test_preserves_normalized_department_and_internal_whitespace(self):
		for prefix in ("Department", "Medical  Department"):
			with self.subTest(prefix=prefix):
				department = f"{prefix} {frappe.generate_hash(length=10)}"
				doc = frappe.get_doc({"doctype": "Medical Department", "department": department}).insert()
				doc.reload()
				self.assertEqual(doc.department, department)
				self.assertEqual(doc.name, department)
				doc.save()
				doc.reload()
				self.assertEqual(doc.department, department)
