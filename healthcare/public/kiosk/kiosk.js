/*
 * Patient registration kiosk UI (served at /kiosk). Plain JS, no build step.
 *
 * Rules this file keeps:
 *  - It only calls /api/method/healthcare.api.kiosk.* and healthcare.api.kiosk_abha.*.
 *  - Patient details live in the `S` object only. Nothing goes to web storage, cookies or the URL, and
 *    every reset (done, idle timeout) drops `S` and redraws from nothing.
 *  - Server text is only ever put into the page with textContent, never as HTML.
 */
(function () {
	"use strict";

	var BOOT = JSON.parse(document.getElementById("kiosk-boot").textContent);
	var ROOT = document.getElementById("kiosk-root");
	var KIOSK = "/api/method/healthcare.api.kiosk.";
	var ABHA = "/api/method/healthcare.api.kiosk_abha.";
	var GENDERS = ["Male", "Female", "Other"];

	var S = fresh(); // everything entered or received for the current patient
	var busy = false;
	var idleTimer = null;
	var lastPing = 0;

	function fresh() {
		return {
			screen: "welcome",
			patient: null,
			mobile: "",
			abha: "",
			searchBy: "mobile",
			txn: null,
			flow: null,
			consent: null,
			error: "",
			notice: "",
		};
	}

	// ----------------------------------------------------------------------------------------------
	// Tiny DOM helper
	// ----------------------------------------------------------------------------------------------

	function h(tag, attrs, children) {
		var el = document.createElement(tag);
		Object.keys(attrs || {}).forEach(function (key) {
			var value = attrs[key];
			if (value === null || value === undefined || value === false) return;
			if (key === "class") el.className = value;
			else if (key.indexOf("on") === 0) el.addEventListener(key.slice(2), value);
			else el.setAttribute(key, value === true ? "" : value);
		});
		[].concat(children === undefined ? [] : children).forEach(function (child) {
			if (child === null || child === undefined || child === false) return;
			el.appendChild(
				typeof child === "string" ? document.createTextNode(child) : child,
			);
		});
		return el;
	}

	function button(label, onclick, kind) {
		return h(
			"button",
			{ type: "button", class: "btn " + (kind || "primary"), onclick: onclick },
			label,
		);
	}

	function field(id, label, attrs) {
		return h("div", { class: "field" }, [
			h("label", { for: id }, label),
			h("input", Object.assign({ id: id, name: id, autocomplete: "off" }, attrs)),
		]);
	}

	function value(id) {
		var el = document.getElementById(id);
		return el ? el.value.trim() : "";
	}

	// ----------------------------------------------------------------------------------------------
	// Server calls
	// ----------------------------------------------------------------------------------------------

	function call(base, method, args) {
		return fetch(base + method, {
			method: "POST",
			credentials: "same-origin",
			headers: {
				"Content-Type": "application/json",
				Accept: "application/json",
				"X-Frappe-CSRF-Token": BOOT.csrf_token,
			},
			body: JSON.stringify(args || {}),
		}).then(function (response) {
			return response.text().then(function (text) {
				var body = {};
				try {
					body = JSON.parse(text);
				} catch (e) {
					/* not JSON: fall through to the generic error */
				}
				if (response.ok) return body.message;
				var error = new Error(serverMessage(body, response.status));
				error.status = response.status;
				throw error;
			});
		});
	}

	function serverMessage(body, status) {
		if (status === 429)
			return "Too many requests. Please wait a minute and try again.";
		if (status === 401)
			return "This kiosk is not signed in. Please ask for help at the desk.";
		// A 403 is usually this session's registration expiring (start over), but it is also what a kiosk login
		// that has expired or lost its role gets, which starting over does not fix, so the desk is named too.
		if (status === 403)
			return "Your session has ended. Please start again, or ask for help at the desk if this keeps happening.";
		try {
			var messages = JSON.parse(body._server_messages || "[]");
			if (messages.length) {
				var text = JSON.parse(messages[0]).message || "";
				var doc = new DOMParser().parseFromString(text, "text/html");
				if (doc.body.textContent.trim()) return doc.body.textContent.trim();
			}
		} catch (e) {
			/* use the generic text */
		}
		return "Something went wrong. Please ask for help at the desk.";
	}

	// Run a server step: show a busy state, put any error on the current screen, never leave it stuck
	function run(promise, onDone) {
		if (busy) return;
		busy = true;
		S.error = "";
		render();
		promise
			.then(function (result) {
				busy = false;
				onDone(result);
			})
			.catch(function (error) {
				busy = false;
				S.error = error.message;
				render();
			});
	}

	// ----------------------------------------------------------------------------------------------
	// Idle reset: a patient walks away, the next one must never see their details
	// ----------------------------------------------------------------------------------------------

	function touch() {
		clearTimeout(idleTimer);
		if (S.screen === "welcome") return;
		idleTimer = setTimeout(reset, BOOT.idle_timeout * 1000);
		keepSessionAlive();
	}

	// Reading the terms takes longer than the server keeps a registration without a request, so while the patient
	// is actively touching or scrolling that screen, tell the server. Stopping touching stops this too.
	function keepSessionAlive() {
		var now = Date.now();
		if (S.screen !== "abhaConsent" || !S.patient || now - lastPing < 15000) return;
		lastPing = now;
		call(ABHA, "keep_alive", { patient: S.patient.uhid }).catch(function () {});
	}

	function reset() {
		clearTimeout(idleTimer);
		stopQrScanner();
		var hadPatient = !!S.patient;
		S = fresh();
		busy = false;
		render();
		if (hadPatient && !BOOT.preview)
			call(KIOSK, "end_session").catch(function () {});
	}

	["pointerdown", "keydown", "input", "touchstart", "scroll"].forEach(function (name) {
		document.addEventListener(name, touch, true);
	});

	// ----------------------------------------------------------------------------------------------
	// Screens
	// ----------------------------------------------------------------------------------------------

	function go(screen) {
		S.screen = screen;
		S.error = "";
		render();
		touch();
	}

	function render() {
		var screens = {
			welcome: welcome,
			form: form,
			search: search,
			pick: pick,
			result: result,
			booked: booked,
			abhaMethod: abhaMethod,
			abhaConsent: abhaConsent,
			abhaAadhaar: abhaAadhaar,
			abhaOtp: abhaOtp,
			abhaAddress: abhaAddress,
			abhaDone: abhaDone,
			scan: scan,
		};
		var content = unavailable() || screens[S.screen]();
		var card = h("section", { class: "card" }, [
			content,
			S.error ? h("p", { class: "error", role: "alert" }, S.error) : null,
			busy ? h("p", { class: "busy" }, "Please wait...") : null,
		]);
		var parts = [];
		if (BOOT.preview)
			parts.push(
				h(
					"p",
					{ class: "banner" },
					"Preview: sign in as the Kiosk user to register patients.",
				),
			);
		if (BOOT.hospital)
			parts.push(h("header", { class: "brand" }, h("h2", {}, BOOT.hospital)));
		parts.push(card);
		// Not replaceChildren(null, card): the DOM turns a null argument into the text "null"
		ROOT.replaceChildren.apply(ROOT, parts);
		var first = ROOT.querySelector("input, select");
		if (first && !busy) first.focus({ preventScroll: true });
	}

	function unavailable() {
		var message = null;
		if (!BOOT.enabled) message = "Registration is not available right now.";
		else if (!BOOT.camp_active) message = "Registration for this camp is closed.";
		if (!message) return null;
		return h("div", { class: "screen" }, [
			h("h1", {}, message),
			h("p", { class: "lead" }, "Please ask for help at the desk."),
		]);
	}

	function welcome() {
		return h("div", { class: "screen" }, [
			h("h1", {}, "Welcome"),
			BOOT.camp ? h("p", { class: "lead" }, BOOT.camp) : null,
			h(
				"p",
				{ class: "lead" },
				"Touch the button to register and get your token.",
			),
			button("Start registration", function () {
				go("form");
			}),
			button(
				"I am already registered",
				function () {
					go("search");
				},
				"secondary",
			),
		]);
	}

	function search() {
		var byAbha = S.searchBy === "abha";
		return h(
			"form",
			{ class: "screen", novalidate: true, onsubmit: submitSearch },
			[
				h("h1", {}, "Find your record"),
				h(
					"div",
					{ class: "choices", role: "group", "aria-label": "Search by" },
					[
						button(
							"Mobile number",
							function () {
								S.searchBy = "mobile";
								render();
							},
							byAbha ? "secondary" : "primary",
						),
						button(
							"ABHA address or number",
							function () {
								S.searchBy = "abha";
								render();
							},
							byAbha ? "primary" : "secondary",
						),
					],
				),
				byAbha
					? field("abha", "ABHA address or 14 digit ABHA number", {
							value: S.abha || "",
							required: true,
							placeholder: "name@sbx or 14 digits",
							autocapitalize: "none",
							spellcheck: "false",
					  })
					: field("mobile", "Mobile number", {
							type: "tel",
							inputmode: "numeric",
							value: S.mobile || "",
							required: true,
							placeholder: "10 digit number",
					  }),
				h("div", { class: "actions" }, [
					button("Back", reset, "secondary"),
					h("button", { type: "submit", class: "btn primary" }, "Search"),
				]),
			],
		);
	}

	function submitSearch(event) {
		event.preventDefault();
		var byAbha = S.searchBy === "abha";
		var typed = value(byAbha ? "abha" : "mobile");
		var request = byAbha
			? call(KIOSK, "search_by_abha", { abha: typed })
			: call(KIOSK, "search_by_mobile", { mobile: typed });
		run(request, function (found) {
			if (byAbha) S.abha = typed;
			else S.mobile = typed;
			S.matches = found.matches;
			go("pick");
		});
	}

	function pick() {
		var matches = S.matches || [];
		if (!matches.length)
			return h("div", { class: "screen" }, [
				h("h1", {}, "We could not find a record"),
				h(
					"p",
					{ class: "lead" },
					"Check what you entered, or register as a new patient.",
				),
				h("div", { class: "actions stack" }, [
					button("Register as a new patient", function () {
						S.draft = { mobile: S.searchBy === "abha" ? "" : S.mobile };
						go("form");
					}),
					button(
						"Search again",
						function () {
							go("search");
						},
						"secondary",
					),
					button("Done", reset, "secondary"),
				]),
			]);
		return h("div", { class: "screen" }, [
			h("h1", {}, "Touch your name"),
			h(
				"div",
				{ class: "actions stack" },
				matches.map(function (m) {
					return button(
						m.name + (m.age ? ", " + m.age + " yrs" : ""),
						function () {
							selectPatient(m.ref);
						},
						"secondary",
					);
				}),
			),
			h("div", { class: "actions stack" }, [
				button(
					"I am not on this list",
					function () {
						S.draft = { mobile: S.searchBy === "abha" ? "" : S.mobile };
						go("form");
					},
					"secondary",
				),
				button("Back", reset, "secondary"),
			]),
		]);
	}

	function selectPatient(ref) {
		run(call(KIOSK, "select_patient", { ref: ref }), function (patient) {
			S.patient = patient;
			S.matches = null;
			go("result");
		});
	}

	function form() {
		var gender = S.draft && S.draft.sex;
		var draft = S.draft || {};
		return h("form", { class: "screen", novalidate: true, onsubmit: submitForm }, [
			h("h1", {}, "Your details"),
			S.notice ? h("p", { class: "notice" }, S.notice) : null,
			h("div", { class: "actions" }, [
				button("Scan Aadhaar QR", scanAadhaarQr, "secondary"),
			]),
			field("first_name", "First name", {
				value: draft.first_name || "",
				required: true,
				maxlength: 140,
			}),
			field("last_name", "Last name (optional)", {
				value: draft.last_name || "",
				maxlength: 140,
			}),
			h("fieldset", { class: "field" }, [
				h("legend", {}, "Gender"),
				h(
					"div",
					{ class: "choices" },
					GENDERS.map(function (name) {
						return h("label", { class: "choice" }, [
							h("input", {
								type: "radio",
								name: "sex",
								value: name,
								checked: gender === name,
								required: true,
							}),
							h("span", {}, name),
						]);
					}),
				),
			]),
			field("dob", "Date of birth", {
				type: "date",
				value: draft.dob || "",
				required: true,
			}),
			field("mobile", "Mobile number", {
				type: "tel",
				inputmode: "numeric",
				value: draft.mobile || "",
				required: true,
				placeholder: "10 digit number",
			}),
			field("email", "Email (optional)", {
				type: "email",
				value: draft.email || "",
			}),
			h("div", { class: "actions" }, [
				button(
					"Back",
					function () {
						S.draft = null;
						reset();
					},
					"secondary",
				),
				h("button", { type: "submit", class: "btn primary" }, "Register"),
			]),
		]);
	}

	function submitForm(event) {
		event.preventDefault();
		var checked = document.querySelector("input[name=sex]:checked");
		var details = {
			first_name: value("first_name"),
			last_name: value("last_name"),
			sex: checked ? checked.value : "",
			dob: value("dob"),
			mobile: value("mobile"),
			email: value("email"),
		};
		S.draft = details;
		run(call(KIOSK, "register_patient", details), function (patient) {
			S.patient = patient;
			S.mobile = details.mobile;
			S.draft = null;
			go("result");
		});
	}


// ----------------------------------------------------------------------------------------------
// Aadhaar QR Code Scanner
// ----------------------------------------------------------------------------------------------

var qrStream = null;
// Same-origin only: the kiosk page never loads script or calls out to another host.
var JSQR_URL = "/assets/healthcare/kiosk/jsQR.min.js";

// Use the browser's own QR reader when it has one; otherwise load the bundled jsQR the first time a scan starts.
function loadReader() {
	if (window.BarcodeDetector || window.jsQR) return Promise.resolve();
	return new Promise(function (resolve, reject) {
		var tag = document.createElement("script");
		tag.src = JSQR_URL;
		tag.onload = resolve;
		tag.onerror = function () { reject(new Error("QR reader could not be loaded")); };
		document.head.appendChild(tag);
	});
}

// The scanning screen: live camera preview with a viewfinder frame and a way out.
function scan() {
	var video = h("video", { id: "qr-video", class: "scan-video", playsinline: true, muted: true, autoplay: true });
	video.muted = true;
	if (qrStream) {
		video.srcObject = qrStream;
		video.play().catch(function () {});
	}
	return h("div", { class: "screen" }, [
		h("h1", {}, "Scan your Aadhaar QR"),
		h("p", { class: "lead" }, "Hold the QR code on your Aadhaar card inside the frame."),
		h("div", { class: "scan-frame" }, [
			video,
			h("div", { class: "scan-guide", "aria-hidden": "true" }),
			qrStream ? null : h("p", { class: "scan-status" }, "Starting camera..."),
		]),
		h("p", { class: "hint" }, "Your card is read on this screen only. Nothing is saved until you submit the form."),
		h("div", { class: "actions" }, [button("Cancel", cancelScan, "secondary")]),
	]);
}

function rememberDraft() {
	if (!document.getElementById("first_name")) return;
	var checked = document.querySelector("input[name=sex]:checked");
	S.draft = {
		first_name: value("first_name"),
		last_name: value("last_name"),
		sex: checked ? checked.value : "",
		dob: value("dob"),
		mobile: value("mobile"),
		email: value("email"),
	};
}

function scanAadhaarQr() {
	if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
		showNotice("Camera access is not supported on this device.");
		return;
	}
	rememberDraft();
	go("scan");

	Promise.all([
		navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } }),
		loadReader(),
	]).then(function (results) {
		if (S.screen !== "scan") {
			// the patient cancelled or the kiosk timed out while the camera was starting
			results[0].getTracks().forEach(function (t) { t.stop(); });
			return;
		}
		qrStream = results[0];
		render();
		scanQrFrame();
	}).catch(function (err) {
		stopQrScanner();
		go("form");
		S.error =
			err && err.name === "NotAllowedError"
				? "Camera permission was denied. Allow the camera or enter your details manually."
				: "Could not start the camera or QR reader. Please enter your details manually.";
		render();
	});
}

var detector = null;

function readFrame(video) {
	if (window.BarcodeDetector) {
		detector = detector || new window.BarcodeDetector({ formats: ["qr_code"] });
		return detector.detect(video).then(function (codes) {
			return codes.length ? codes[0].rawValue : null;
		});
	}
	var canvas = document.createElement("canvas");
	canvas.width = video.videoWidth;
	canvas.height = video.videoHeight;
	var ctx = canvas.getContext("2d", { willReadFrequently: true });
	ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
	var image = ctx.getImageData(0, 0, canvas.width, canvas.height);
	var code = window.jsQR(image.data, canvas.width, canvas.height);
	return Promise.resolve(code && code.data ? code.data : null);
}

function scanQrFrame() {
	if (S.screen !== "scan" || !qrStream) return;
	var video = document.getElementById("qr-video");
	var next = function () { requestAnimationFrame(scanQrFrame); };
	if (!video || video.readyState < 2 || !video.videoWidth) return next();
	readFrame(video)
		.then(function (data) {
			if (S.screen !== "scan") return;
			if (data) {
				stopQrScanner();
				processAadhaarQr(data);
			} else {
				next();
			}
		})
		.catch(next);
}

function processAadhaarQr(qrData) {
	go("form");
	run(call(KIOSK, "parse_aadhaar_qr", { qr_data: qrData }), function (result) {
		var old = S.draft || {};
		S.draft = {
			first_name: result.first_name || "",
			last_name: result.last_name || "",
			sex: result.sex || "",
			dob: result.dob || "",
			mobile: old.mobile || "",
			email: old.email || "",
			address: result.address || "",
			pincode: result.pincode || "",
		};
		S.qrMaskedAadhaar = result.masked_aadhaar;
		S.notice = "Aadhaar QR scanned. Please review and complete the form.";
		render();
	});
}

function cancelScan() {
	stopQrScanner();
	go("form");
}

function stopQrScanner() {
	if (qrStream) {
		qrStream.getTracks().forEach(function (track) { track.stop(); });
		qrStream = null;
	}
}

function showNotice(message) {
	S.notice = message;
	render();
	setTimeout(function () {
		S.notice = "";
		render();
	}, 5000);
}

	function result() {
		var p = S.patient;
		var actions = [button("Print my token", printSlip)];
		if (BOOT.enable_appointment)
			actions.push(button("Book my appointment", bookAppointment, "secondary"));
		// Only for a patient registered just now: a returning patient's ABHA is handled at the desk, because the
		// kiosk cannot tell that the person typing a matching mobile number is that patient.
		if (BOOT.enable_abha && !p.abha_linked && !p.already_registered)
			actions.push(
				button(
					"Create or link my ABHA",
					function () {
						go("abhaMethod");
					},
					"secondary",
				),
			);
		actions.push(button("Done", reset, "secondary"));

		return h("div", { class: "screen" }, [
			h(
				"h1",
				{},
				p.already_registered
					? "Welcome back, " + p.patient_name
					: "You are registered",
			),
			h("p", { class: "token-label" }, "Your token"),
			h("p", { class: "token" }, String(p.token_number)),
			// A returning patient (matched at registration, or picked from the mobile search) sees limited details.
			h("dl", { class: "facts" }, [
				h("dt", {}, "Name"),
				h("dd", {}, p.patient_name),
				h("dt", {}, "UHID"),
				h("dd", {}, p.uhid),
				p.age ? h("dt", {}, "Age") : null,
				p.age ? h("dd", {}, p.age + (p.sex ? ", " + p.sex : "")) : null,
				h("dt", {}, "Mobile"),
				h("dd", {}, p.masked_mobile),
			]),
			S.notice ? h("p", { class: "notice" }, S.notice) : null,
			h("div", { class: "actions stack" }, actions),
		]);
	}

	function printSlip() {
		run(call(KIOSK, "get_token_slip", { token: S.patient.token }), function (slip) {
			var frame = h("iframe", { class: "print-frame", title: "Token slip" });
			frame.addEventListener("load", function () {
				frame.contentWindow.focus();
				frame.contentWindow.print();
				setTimeout(function () {
					frame.remove();
				}, 60000);
			});
			document.body.appendChild(frame);
			frame.srcdoc = slip.html;
			S.notice = "Your token is printing.";
			render();
		});
	}

	function bookAppointment() {
		run(
			call(KIOSK, "book_appointment", { token: S.patient.token }),
			function (appointment) {
				S.appointment = appointment;
				go("booked");
			},
		);
	}

	function booked() {
		var a = S.appointment;
		return h("div", { class: "screen" }, [
			h("h1", {}, "Appointment booked"),
			h("dl", { class: "facts" }, [
				h("dt", {}, "Date"),
				h("dd", {}, a.date),
				a.time ? h("dt", {}, "Time") : null,
				a.time ? h("dd", {}, a.time) : null,
				h("dt", {}, "Token"),
				h("dd", {}, String(a.token_number)),
			]),
			h("div", { class: "actions" }, [
				button(
					"Back",
					function () {
						go("result");
					},
					"secondary",
				),
				button("Done", reset),
			]),
		]);
	}

	// ----------------------------------------------------------------------------------------------
	// ABHA: create by Aadhaar OTP, or link an ABHA the patient already has
	// ----------------------------------------------------------------------------------------------

	function abhaMethod() {
		return h("div", { class: "screen" }, [
			h("h1", {}, "ABHA"),
			h("p", { class: "lead" }, "Choose how you would like to continue."),
			h("div", { class: "actions stack" }, [
				button("Create ABHA with Aadhaar", function () {
					S.flow = "aadhaar";
					showConsent();
				}),
				button(
					"I already have an ABHA",
					function () {
						S.flow = "link";
						go("abhaAadhaar");
					},
					"secondary",
				),
				button(
					"Back",
					function () {
						go("result");
					},
					"secondary",
				),
			]),
		]);
	}

	// Creating an ABHA needs the patient's agreement to ABDM's terms first. The server refuses to send any
	// create OTP until it has recorded it, so this screen cannot be skipped by going straight to the next one.
	function showConsent() {
		run(call(ABHA, "get_consent_text", { patient: S.patient.uhid }), function (found) {
			S.consent = found;
			go("abhaConsent");
		});
	}

	function abhaConsent() {
		var terms = S.consent || {};
		return h("div", { class: "screen" }, [
			h("h1", {}, terms.title || "Terms and Conditions"),
			h("p", { class: "lead" }, "Please read this before we continue."),
			h("div", { class: "terms", tabindex: "0" }, terms.text || ""),
			h("label", { class: "agree" }, [
				h("input", { type: "checkbox", id: "abha_agree" }),
				" I have read the above and I agree.",
			]),
			h("div", { class: "actions" }, [
				button(
					"Back",
					function () {
						go("abhaMethod");
					},
					"secondary",
				),
				button("Agree and continue", agreeToTerms),
			]),
		]);
	}

	function agreeToTerms() {
		var box = document.getElementById("abha_agree");
		if (!box || !box.checked) {
			S.error = "Please tick the box to agree before continuing.";
			render();
			return;
		}
		run(call(ABHA, "record_consent", { patient: S.patient.uhid }), function () {
			go("abhaAadhaar");
		});
	}

	// First input of every flow: Aadhaar number or ABHA address
	function abhaAadhaar() {
		var cfg = {
			aadhaar: [
				"Aadhaar number",
				{ inputmode: "numeric", maxlength: 14, placeholder: "12 digit number" },
			],
			link: ["ABHA address", { placeholder: "name@abdm" }],
		}[S.flow];
		return h(
			"form",
			{ class: "screen", novalidate: true, onsubmit: sendFirstOtp },
			[
				h("h1", {}, S.flow === "link" ? "Link your ABHA" : "Create your ABHA"),
				field("abha_first", cfg[0], Object.assign({ required: true }, cfg[1])),
				h(
					"p",
					{ class: "hint" },
					"We will send an OTP to the mobile number linked with it.",
				),
				h("div", { class: "actions" }, [
					button(
						"Back",
						function () {
							go("abhaMethod");
						},
						"secondary",
					),
					h("button", { type: "submit", class: "btn primary" }, "Send OTP"),
				]),
			],
		);
	}

	function sendFirstOtp(event) {
		event.preventDefault();
		var input = value("abha_first");
		var p = S.patient.uhid;
		var request = {
			aadhaar: function () {
				return call(ABHA, "send_aadhaar_otp", { patient: p, aadhaar: input });
			},
			link: function () {
				return call(ABHA, "send_link_otp", { patient: p, abha_address: input });
			},
		}[S.flow]();
		run(request, function (sent) {
			S.txn = sent.txn_id;
			S.stage = "first";
			go("abhaOtp");
		});
	}

	function abhaOtp() {
		var mobileStage = S.stage === "mobile";
		return h("form", { class: "screen", novalidate: true, onsubmit: verifyOtp }, [
			h("h1", {}, "Enter the OTP"),
			field("otp", "6 digit OTP", {
				inputmode: "numeric",
				maxlength: 6,
				required: true,
			}),
			S.flow === "aadhaar" && S.stage === "first"
				? field("abha_mobile", "Your mobile number", {
						type: "tel",
						inputmode: "numeric",
						value: S.mobile,
						required: true,
				  })
				: null,
			mobileStage
				? h("p", { class: "hint" }, "This OTP was sent to your mobile number.")
				: null,
			h("div", { class: "actions" }, [
				button(
					"Back",
					function () {
						go("abhaMethod");
					},
					"secondary",
				),
				h("button", { type: "submit", class: "btn primary" }, "Verify"),
			]),
		]);
	}

	function verifyOtp(event) {
		event.preventDefault();
		var otp = value("otp");
		var p = S.patient.uhid;
		var t = S.txn;

		if (S.flow === "link") {
			return run(
				call(ABHA, "verify_link_otp", { patient: p, txn_id: t, otp: otp }),
				function (linked) {
					S.abha = linked;
					go("abhaDone");
				},
			);
		}
		if (S.stage === "mobile") {
			return run(
				call(ABHA, "verify_enrol_mobile_otp", {
					patient: p,
					txn_id: t,
					otp: otp,
				}),
				loadSuggestions,
			);
		}
		S.mobile = value("abha_mobile") || S.mobile;
		run(
			call(ABHA, "verify_aadhaar_otp", {
				patient: p,
				txn_id: t,
				otp: otp,
				mobile: S.mobile,
			}),
			function (created) {
				S.abha = created;
				S.txn = created.txn_id || t;
				if (created.mobile_linked) return loadSuggestions();
				// The mobile given is not the Aadhaar-linked one: ABDM sends a second OTP to verify it
				run(
					call(ABHA, "send_enrol_mobile_otp", {
						patient: p,
						txn_id: S.txn,
						mobile: S.mobile,
					}),
					function (sent) {
						S.txn = sent.txn_id;
						S.stage = "mobile";
						go("abhaOtp");
					},
				);
			},
		);
	}

	function loadSuggestions() {
		busy = false;
		run(
			call(ABHA, "get_address_suggestions", { patient: S.patient.uhid }),
			function (found) {
				S.suggestions = found.suggestions || [];
				go("abhaAddress");
			},
		);
	}

	function abhaAddress() {
		return h("div", { class: "screen" }, [
			h("h1", {}, "Choose your ABHA address"),
			S.suggestions.length
				? h(
						"div",
						{ class: "actions stack" },
						S.suggestions.map(function (address) {
							return button(
								address,
								function () {
									chooseAddress(address);
								},
								"secondary",
							);
						}),
				  )
				: h(
						"p",
						{ class: "lead" },
						"No suggestions are available. You can set your address later at the desk.",
				  ),
			button(
				"Skip for now",
				function () {
					S.abha = Object.assign({}, S.abha, { status: "created" });
					go("abhaDone");
				},
				"secondary",
			),
		]);
	}

	function chooseAddress(address) {
		run(
			call(ABHA, "choose_address", {
				patient: S.patient.uhid,
				abha_address: address,
			}),
			function (saved) {
				S.abha = Object.assign({}, S.abha, saved);
				go("abhaDone");
			},
		);
	}

	function abhaDone() {
		var a = S.abha || {};
		return h("div", { class: "screen" }, [
			h(
				"h1",
				{},
				S.flow === "link" ? "Your ABHA is linked" : "Your ABHA is ready",
			),
			h("dl", { class: "facts" }, [
				a.masked_abha_number ? h("dt", {}, "ABHA number") : null,
				a.masked_abha_number ? h("dd", {}, a.masked_abha_number) : null,
				a.masked_abha_address ? h("dt", {}, "ABHA address") : null,
				a.masked_abha_address ? h("dd", {}, a.masked_abha_address) : null,
			]),
			h("div", { class: "actions" }, [
				button(
					"Back to my token",
					function () {
						go("result");
					},
					"secondary",
				),
				button("Done", reset),
			]),
		]);
	}

	render();
})();
