# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# See license.txt

from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from healthcare.healthcare.doctype.kiosk_token.kiosk_token import KioskToken

# The token only names itself from the date; no linked records are needed to test that
IGNORE_TEST_RECORD_DEPENDENCIES = ["Patient", "Medical Camp"]


class TestKioskToken(IntegrationTestCase):
	def test_name_is_the_per_day_series_and_carries_the_number(self):
		doc = KioskToken({"doctype": "Kiosk Token", "token_date": "2026-03-04"})
		doc.autoname()
		prefix, number = doc.name.rsplit("-", 1)
		self.assertEqual(prefix, "KT-20260304")
		self.assertEqual(doc.token_number, int(number))

	def test_date_defaults_to_today(self):
		doc = KioskToken({"doctype": "Kiosk Token"})
		doc.autoname()
		self.assertEqual(str(doc.token_date), nowdate())
