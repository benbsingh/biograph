// Copyright (c) 2023, healthcare and contributors
// For license information, please see license.txt

frappe.ui.form.on("Specimen", {
	refresh(frm) {
		if (frm.is_new() || ["Accepted", "Rejected"].includes(frm.doc.accession_status)) {
			return;
		}
		frm.add_custom_button(__("Accept"), () => accession(frm, "Accept"), __("Accession"));
		frm.add_custom_button(__("Reject"), () => reject(frm), __("Accession"));
	},
});

function accession(frm, decision, reason) {
	frappe.call({
		method: "healthcare.healthcare.doctype.specimen.specimen.accession_specimen",
		args: { specimen: frm.doc.name, decision, reason },
		callback() {
			frm.reload_doc();
		},
	});
}

function reject(frm) {
	frappe.prompt(
		{ fieldname: "reason", fieldtype: "Small Text", label: __("Rejection Reason"), reqd: 1 },
		(values) => accession(frm, "Reject", values.reason),
		__("Reject Specimen"),
		__("Reject")
	);
}
