# Copyright (c) 2015, ESS and Contributors
# See license.txt


import frappe
from frappe.tests import IntegrationTestCase


class TestLabTestTemplate(IntegrationTestCase):
	def _make_item(self, code, is_stock_item, disabled=0):
		item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": code,
				"item_name": code,
				"item_group": "All Item Groups",
				"stock_uom": "Nos",
				"is_stock_item": is_stock_item,
				"is_sales_item": 0 if is_stock_item else 1,
				"is_service_item": 0 if is_stock_item else 1,
				"disabled": disabled,
			}
		)
		return item.insert(ignore_permissions=True, ignore_mandatory=True).name

	def _make_department(self):
		if not frappe.db.exists("Medical Department", "TEST-DEPT-CONS"):
			frappe.get_doc({"doctype": "Medical Department", "department": "TEST-DEPT-CONS"}).insert(
				ignore_permissions=True
			)
		return "TEST-DEPT-CONS"

	def _make_patient(self):
		customer_group = frappe.get_all("Customer Group", filters={"is_group": 0}, pluck="name", limit=1)[0]
		patient = frappe.get_doc(
			{
				"doctype": "Patient",
				"first_name": "_TPL CONS Patient",
				"sex": "Female",
				"customer_group": customer_group,
			}
		)
		return patient.insert(ignore_permissions=True).name

	def _make_template(self, code, consumables):
		doc = frappe.get_doc(
			{
				"doctype": "Lab Test Template",
				"lab_test_code": code,
				"lab_test_name": code,
				"lab_test_group": "All Item Groups",
				"department": self._make_department(),
				"lab_test_template_type": "Single",
				"is_billable": 0,
				"lab_test_consumables": [{"item": item, "qty": qty} for item, qty in consumables],
			}
		)
		return doc

	def test_consumable_must_be_stock_item(self):
		service_item = self._make_item("TEST-SVC-CONS-1", is_stock_item=0)
		doc = self._make_template("TEST-TPL-CONS-1", [(service_item, 1)])
		self.assertRaises(frappe.ValidationError, doc.insert, ignore_permissions=True)

	def test_consumable_qty_must_be_positive(self):
		stock_item = self._make_item("TEST-STK-CONS-1", is_stock_item=1)
		doc = self._make_template("TEST-TPL-CONS-2", [(stock_item, 0)])
		self.assertRaises(frappe.ValidationError, doc.insert, ignore_permissions=True)

	def test_stock_item_consumable_saves(self):
		stock_item = self._make_item("TEST-STK-CONS-2", is_stock_item=1)
		doc = self._make_template("TEST-TPL-CONS-3", [(stock_item, 2)])
		doc.insert(ignore_permissions=True)
		self.assertEqual(len(doc.lab_test_consumables), 1)
		self.assertEqual(doc.lab_test_consumables[0].item, stock_item)

	def test_disabled_stock_item_consumable_rejected(self):
		stock_item = self._make_item("TEST-STK-CONS-3", is_stock_item=1, disabled=1)
		doc = self._make_template("TEST-TPL-CONS-4", [(stock_item, 1)])
		with self.assertRaisesRegex(frappe.ValidationError, "Row #1"):
			doc.insert(ignore_permissions=True)

	def test_consumable_round_trip_fills_item_fields(self):
		stock_item = self._make_item("TEST-STK-CONS-4", is_stock_item=1)
		doc = self._make_template("TEST-TPL-CONS-5", [(stock_item, 3)])
		doc.insert(ignore_permissions=True)
		doc.reload()
		row = doc.lab_test_consumables[0]
		self.assertEqual((row.item, row.qty), (stock_item, 3))
		self.assertEqual(row.item_name, stock_item)
		self.assertEqual(row.uom, "Nos")
		self.assertTrue(frappe.get_meta("Lab Test Consumable").get_field("item_name").read_only)

	def test_lab_test_from_template_with_consumables_creates_no_sle(self):
		stock_item = self._make_item("TEST-STK-CONS-5", is_stock_item=1)
		template = self._make_template("TEST-TPL-CONS-6", [(stock_item, 1)])
		template.lab_test_template_type = "Descriptive"
		template.append("descriptive_test_templates", {"particulars": "Result"})
		template.insert(ignore_permissions=True)

		sle_before = frappe.db.count("Stock Ledger Entry")
		stock_entry_before = frappe.db.count("Stock Entry")

		lab_test = frappe.new_doc("Lab Test")
		lab_test.template = template.name
		lab_test.patient = self._make_patient()
		lab_test.patient_sex = "Female"
		lab_test.save()
		for row in lab_test.descriptive_test_items:
			row.result_value = "Positive"
		lab_test.save()
		lab_test.submit()

		self.assertEqual(frappe.db.count("Stock Ledger Entry"), sle_before)
		self.assertEqual(frappe.db.count("Stock Entry"), stock_entry_before)
