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

	function fresh() {
		return {
			screen: "welcome",
			patient: null,
			mobile: "",
			abha: "",
			searchBy: "mobile",
			txn: null,
			flow: null,
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
	// QR Code generator (minimal, no dependencies)
	// ----------------------------------------------------------------------------------------------

	// Minimal QR code implementation based on kazuhikoarase/qrcode-generator-js
	// Generates a canvas with the QR code for the given text
	// QR Code constants (hoisted so makeQRCode and welcome() share them)
var QR_MODE = {
	MODE_NUMBER: 1 << 0,
	MODE_ALPHA_NUM: 1 << 1,
	MODE_8BIT_BYTE: 1 << 2,
	MODE_KANJI: 1 << 3,
};

var QR_ERROR_CORRECT_LEVEL = {
	L: 1,
	M: 0,
	Q: 3,
	H: 2,
};

function makeQRCode(text, size, errorCorrectLevel) {
		size = size || 180;
		errorCorrectLevel = errorCorrectLevel || QR_ERROR_CORRECT_LEVEL.L; // Use L for max capacity

		// QR Code constants
		var PAD0 = [0xec, 0x11];
		var PAD1 = [0x11, 0xec];

		function QRCodeModel(typeNumber, errorCorrectLevel) {
			this.typeNumber = typeNumber;
			this.errorCorrectLevel = errorCorrectLevel;
			this.modules = null;
			this.moduleCount = 0;
			this.dataCache = null;
			this.dataList = [];
		}

		QRCodeModel.prototype = {
			addData: function (data) {
				var newData = QR8bitByte(data);
				this.dataList.push(newData);
				this.dataCache = null;
			},
			isDark: function (row, col) {
				if (row < 0 || this.moduleCount <= row || col < 0 || this.moduleCount <= col) {
					return false;
				}
				return this.modules[row][col];
			},
			getModuleCount: function () {
				return this.moduleCount;
			},
			make: function () {
				this.makeImpl(false, this.getBestMaskPattern());
			},
			makeImpl: function (test, maskPattern) {
				this.moduleCount = this.typeNumber * 4 + 17;
				this.modules = new Array(this.moduleCount);
				for (var row = 0; row < this.moduleCount; row++) {
					this.modules[row] = new Array(this.moduleCount);
					for (var col = 0; col < this.moduleCount; col++) {
						this.modules[row][col] = false;
					}
				}
				this.setupPositionProbePattern(0, 0);
				this.setupPositionProbePattern(this.moduleCount - 7, 0);
				this.setupPositionProbePattern(0, this.moduleCount - 7);
				this.setupPositionAdjustPattern();
				this.setupTimingPattern();
				this.setupTypeInfo(test, maskPattern);
				if (this.typeNumber >= 7) {
					this.setupTypeNumber(test);
				}
				if (this.dataCache == null) {
					this.dataCache = this.createData(this.typeNumber, this.errorCorrectLevel, this.dataList);
				}
				this.mapData(this.dataCache, maskPattern);
			},
			setupPositionProbePattern: function (row, col) {
				for (var r = -1; r <= 7; r++) {
					if (row + r <= -1 || this.moduleCount <= row + r) continue;
					for (var c = -1; c <= 7; c++) {
						if (col + c <= -1 || this.moduleCount <= col + c) continue;
						if (
							(0 <= r && r <= 6 && (c == 0 || c == 6)) ||
							(0 <= c && c <= 6 && (r == 0 || r == 6)) ||
							(2 <= r && r <= 4 && 2 <= c && c <= 4)
						) {
							this.modules[row + r][col + c] = true;
						} else {
							this.modules[row + r][col + c] = false;
						}
					}
				}
			},
			getBestMaskPattern: function () {
				var minLostPoint = 0;
				var pattern = 0;
				for (var i = 0; i < 8; i++) {
					this.makeImpl(true, i);
					var lostPoint = this.getLostPoint();
					if (i == 0 || minLostPoint > lostPoint) {
						minLostPoint = lostPoint;
						pattern = i;
					}
				}
				return pattern;
			},
			getLostPoint: function () {
				var moduleCount = this.getModuleCount();
				var lostPoint = 0;
				// LEVEL1
				for (var row = 0; row < moduleCount; row++) {
					for (var col = 0; col < moduleCount; col++) {
						var sameCount = 0;
						var dark = this.isDark(row, col);
						for (var r = -1; r <= 1; r++) {
							if (row + r < 0 || moduleCount <= row + r) continue;
							for (var c = -1; c <= 1; c++) {
								if (col + c < 0 || moduleCount <= col + c) continue;
								if (r == 0 && c == 0) continue;
								if (dark == this.isDark(row + r, col + c)) sameCount++;
							}
						}
						if (sameCount > 5) lostPoint += 3 + sameCount - 5;
					}
				}
				// LEVEL2
				for (var row = 0; row < moduleCount - 1; row++) {
					for (var col = 0; col < moduleCount - 1; col++) {
						var count = 0;
						if (this.isDark(row, col)) count++;
						if (this.isDark(row + 1, col)) count++;
						if (this.isDark(row, col + 1)) count++;
						if (this.isDark(row + 1, col + 1)) count++;
						if (count == 0 || count == 4) lostPoint += 3;
					}
				}
				// LEVEL3
				for (var row = 0; row < moduleCount; row++) {
					for (var col = 0; col < moduleCount - 6; col++) {
						if (
							this.isDark(row, col) &&
							!this.isDark(row, col + 1) &&
							this.isDark(row, col + 2) &&
							this.isDark(row, col + 3) &&
							this.isDark(row, col + 4) &&
							!this.isDark(row, col + 5) &&
							this.isDark(row, col + 6)
						) {
							lostPoint += 40;
						}
					}
				}
				for (var col = 0; col < moduleCount; col++) {
					for (var row = 0; row < moduleCount - 6; row++) {
						if (
							this.isDark(row, col) &&
							!this.isDark(row + 1, col) &&
							this.isDark(row + 2, col) &&
							this.isDark(row + 3, col) &&
							this.isDark(row + 4, col) &&
							!this.isDark(row + 5, col) &&
							this.isDark(row + 6, col)
						) {
							lostPoint += 40;
						}
					}
				}
				// LEVEL4
				var darkCount = 0;
				for (var col = 0; col < moduleCount; col++) {
					for (var row = 0; row < moduleCount; row++) {
						if (this.isDark(row, col)) darkCount++;
					}
				}
				var ratio = Math.abs(100 * darkCount / moduleCount / moduleCount - 50) / 5;
				lostPoint += ratio * 10;
				return lostPoint;
			},
			createData: function (typeNumber, errorCorrectLevel, dataList) {
				var rsBlocks = QRRSBlock.getRSBlocks(typeNumber, errorCorrectLevel);
				var buffer = QRBitBuffer();
				for (var i = 0; i < dataList.length; i++) {
					var data = dataList[i];
					buffer.put(data.mode, 4);
					buffer.put(data.getLength(), QRUtil.getLengthInBits(data.mode, typeNumber));
					data.write(buffer);
				}
				var totalDataCount = 0;
				for (var i = 0; i < rsBlocks.length; i++) {
					totalDataCount += rsBlocks[i].dataCount;
				}
				if (buffer.getLengthInBits() > totalDataCount * 8) {
					throw new Error("code length overflow. (" + buffer.getLengthInBits() + ">" + totalDataCount * 8 + ")");
				}
				if (buffer.getLengthInBits() + 4 <= totalDataCount * 8) {
					buffer.put(0, 4);
				}
				while (buffer.getLengthInBits() % 8 != 0) {
					buffer.putBit(false);
				}
				while (true) {
					if (buffer.getLengthInBits() >= totalDataCount * 8) break;
					buffer.put(PAD0[0], 8);
					if (buffer.getLengthInBits() >= totalDataCount * 8) break;
					buffer.put(PAD1[0], 8);
				}
				return this.createBytes(buffer, rsBlocks);
			},
			createBytes: function (buffer, rsBlocks) {
				var offset = 0;
				var maxDcCount = 0;
				var maxEcCount = 0;
				var dcdata = new Array(rsBlocks.length);
				var ecdata = new Array(rsBlocks.length);
				for (var r = 0; r < rsBlocks.length; r++) {
					var dcCount = rsBlocks[r].dataCount;
					var ecCount = rsBlocks[r].totalCount - dcCount;
					maxDcCount = Math.max(maxDcCount, dcCount);
					maxEcCount = Math.max(maxEcCount, ecCount);
					dcdata[r] = new Array(dcCount);
					for (var i = 0; i < dcdata[r].length; i++) {
						dcdata[r][i] = 0xff & buffer.buffer[i + offset];
					}
					offset += dcCount;
					var rsPoly = QRUtil.getErrorCorrectPolynomial(ecCount);
					var rawPoly = QRPolynomial(dcdata[r], rsPoly.getLength() - 1);
					var modPoly = rawPoly.mod(rsPoly);
					ecdata[r] = new Array(rsPoly.getLength() - 1);
					for (var i = 0; i < ecdata[r].length; i++) {
						var modIndex = i + modPoly.getLength() - ecdata[r].length;
						ecdata[r][i] = modIndex >= 0 ? modPoly.get(modIndex) : 0;
					}
				}
				var totalCodeCount = 0;
				for (var i = 0; i < rsBlocks.length; i++) {
					totalCodeCount += rsBlocks[i].totalCount;
				}
				var data = new Array(totalCodeCount);
				var index = 0;
				for (var i = 0; i < maxDcCount; i++) {
					for (var r = 0; r < rsBlocks.length; r++) {
						if (i < dcdata[r].length) {
							data[index++] = dcdata[r][i];
						}
					}
				}
				for (var i = 0; i < maxEcCount; i++) {
					for (var r = 0; r < rsBlocks.length; r++) {
						if (i < ecdata[r].length) {
							data[index++] = ecdata[r][i];
						}
					}
				}
				return data;
			},
			mapData: function (data, maskPattern) {
				var inc = -1;
				var row = this.moduleCount - 1;
				var bitIndex = 7;
				var byteIndex = 0;
				for (var col = this.moduleCount - 1; col > 0; col -= 2) {
					if (col == 6) col--;
					while (true) {
						for (var c = 0; c < 2; c++) {
							if (this.modules[row][col - c] == null) {
								var dark = false;
								if (byteIndex < data.length) {
									dark = (data[byteIndex] >>> bitIndex & 1) == 1;
								}
								var mask = QRUtil.getMask(maskPattern, row, col - c);
								if (mask) dark = !dark;
								this.modules[row][col - c] = dark;
								bitIndex--;
								if (bitIndex == -1) {
									byteIndex++;
									bitIndex = 7;
								}
							}
						}
						row += inc;
						if (row < 0 || this.moduleCount <= row) {
							row -= inc;
							inc = -inc;
							break;
						}
					}
				}
			},
			setupPositionAdjustPattern: function () {
				var pos = QRUtil.getPatternPosition(this.typeNumber);
				for (var i = 0; i < pos.length; i++) {
					for (var j = 0; j < pos.length; j++) {
						var row = pos[i];
						var col = pos[j];
						if (this.modules[row][col] != null) continue;
						for (var r = -2; r <= 2; r++) {
							for (var c = -2; c <= 2; c++) {
								if (r == -2 || r == 2 || c == -2 || c == 2 || (r == 0 && c == 0)) {
									this.modules[row + r][col + c] = true;
								} else {
									this.modules[row + r][col + c] = false;
								}
							}
						}
					}
				}
			},
			setupTimingPattern: function () {
				for (var r = 8; r < this.moduleCount - 8; r++) {
					if (this.modules[r][6] != null) continue;
					this.modules[r][6] = r % 2 == 0;
				}
				for (var c = 8; c < this.moduleCount - 8; c++) {
					if (this.modules[6][c] != null) continue;
					this.modules[6][c] = c % 2 == 0;
				}
			},
			setupTypeNumber: function (test) {
				var bits = QRUtil.getBCHTypeNumber(this.typeNumber);
				for (var i = 0; i < 18; i++) {
					var mod = !test && (bits >> i & 1) == 1;
					this.modules[Math.floor(i / 3)][i % 3 + this.moduleCount - 8 - 3] = mod;
				}
				for (var i = 0; i < 18; i++) {
					var mod = !test && (bits >> i & 1) == 1;
					this.modules[i % 3 + this.moduleCount - 8 - 3][Math.floor(i / 3)] = mod;
				}
			},
			setupTypeInfo: function (test, maskPattern) {
				var data = (this.errorCorrectLevel << 3) | maskPattern;
				var bits = QRUtil.getBCHTypeInfo(data);
				for (var i = 0; i < 15; i++) {
					var mod = !test && (bits >> i & 1) == 1;
					if (i < 6) this.modules[i][8] = mod;
					else if (i < 8) this.modules[i + 1][8] = mod;
					else this.modules[this.moduleCount - 15 + i][8] = mod;
				}
				for (var i = 0; i < 15; i++) {
					var mod = !test && (bits >> i & 1) == 1;
					if (i < 8) this.modules[8][this.moduleCount - i - 1] = mod;
					else if (i < 9) this.modules[8][15 - i + 1] = mod;
					else this.modules[8][15 - i] = mod;
				}
				this.modules[this.moduleCount - 8][8] = !test;
			},
		};

		function QRBitBuffer() {
			this.buffer = [];
			this.length = 0;
		}
		QRBitBuffer.prototype = {
			get: function (index) {
				var bufIndex = Math.floor(index / 8);
				return ((this.buffer[bufIndex] >>> (7 - index % 8)) & 1) == 1;
			},
			put: function (num, length) {
				for (var i = 0; i < length; i++) {
					this.putBit((num >>> (length - i - 1)) & 1);
				}
			},
			putBit: function (bit) {
				var bufIndex = Math.floor(this.length / 8);
				if (this.buffer.length <= bufIndex) this.buffer.push(0);
				if (bit) this.buffer[bufIndex] |= 0x80 >>> this.length % 8;
				this.length++;
			},
			getLengthInBits: function () {
				return this.length;
			},
		};

		var QRUtil = {
			PATTERN_POSITION_TABLE: [
				[],
				[6, 18],
				[6, 22],
				[6, 26],
				[6, 30],
				[6, 34],
				[6, 22, 38],
				[6, 24, 42],
				[6, 26, 46],
				[6, 28, 50],
				[6, 30, 54],
				[6, 32, 58],
				[6, 34, 62],
				[6, 26, 46, 66],
				[6, 26, 48, 70],
				[6, 26, 50, 74],
				[6, 30, 54, 78],
				[6, 30, 56, 82],
				[6, 30, 58, 86],
				[6, 34, 62, 90],
				[6, 28, 50, 72, 94],
				[6, 26, 50, 74, 98],
				[6, 30, 54, 78, 102],
				[6, 28, 54, 80, 106],
				[6, 32, 58, 84, 110],
				[6, 30, 58, 86, 114],
				[6, 34, 62, 90, 118],
				[6, 26, 50, 74, 98, 122],
				[6, 30, 54, 78, 102, 126],
				[6, 26, 52, 78, 104, 130],
				[6, 30, 56, 82, 108, 134],
				[6, 34, 60, 86, 112, 138],
				[6, 30, 58, 86, 114, 142],
				[6, 34, 62, 90, 118, 146],
				[6, 30, 54, 78, 102, 126, 150],
				[6, 24, 50, 76, 102, 128, 154],
				[6, 28, 54, 80, 106, 132, 158],
				[6, 32, 58, 84, 110, 136, 162],
				[6, 26, 54, 82, 110, 138, 166],
				[6, 30, 58, 86, 114, 142, 170],
			],
			G15: (1 << 10) | (1 << 8) | (1 << 5) | (1 << 4) | (1 << 2) | (1 << 1) | (1 << 0),
			G18: (1 << 12) | (1 << 11) | (1 << 10) | (1 << 9) | (1 << 8) | (1 << 5) | (1 << 2) | (1 << 0),
			G15_MASK: (1 << 14) | (1 << 12) | (1 << 10) | (1 << 4) | (1 << 1),
			getBCHTypeInfo: function (data) {
				var d = data << 10;
				while (QRUtil.getBCHDigit(d) - QRUtil.getBCHDigit(QRUtil.G15) >= 0) {
					d ^= QRUtil.G15 << (QRUtil.getBCHDigit(d) - QRUtil.getBCHDigit(QRUtil.G15));
				}
				return ((data << 10) | d) ^ QRUtil.G15_MASK;
			},
			getBCHTypeNumber: function (data) {
				var d = data << 12;
				while (QRUtil.getBCHDigit(d) - QRUtil.getBCHDigit(QRUtil.G18) >= 0) {
					d ^= QRUtil.G18 << (QRUtil.getBCHDigit(d) - QRUtil.getBCHDigit(QRUtil.G18));
				}
				return (data << 12) | d;
			},
			getBCHDigit: function (data) {
				var digit = 0;
				while (data != 0) {
					digit++;
					data >>>= 1;
				}
				return digit;
			},
			getPatternPosition: function (typeNumber) {
				return QRUtil.PATTERN_POSITION_TABLE[typeNumber - 1];
			},
			getMask: function (maskPattern, i, j) {
				switch (maskPattern) {
					case 0: return (i + j) % 2 == 0;
					case 1: return i % 2 == 0;
					case 2: return j % 3 == 0;
					case 3: return (i + j) % 3 == 0;
					case 4: return (Math.floor(i / 2) + Math.floor(j / 3)) % 2 == 0;
					case 5: return (i * j) % 2 + (i * j) % 3 == 0;
					case 6: return ((i * j) % 2 + (i * j) % 3) % 2 == 0;
					case 7: return ((i * j) % 3 + (i + j) % 2) % 2 == 0;
					default: throw new Error("bad maskPattern:" + maskPattern);
				}
			},
			getErrorCorrectPolynomial: function (errorCorrectLength) {
				var a = QRPolynomial([1], 0);
				for (var i = 0; i < errorCorrectLength; i++) {
					a = a.multiply(QRPolynomial([1, QRMath.gexp(i)], 0));
				}
				return a;
			},
			getLengthInBits: function (mode, type) {
				if (1 <= type && type < 10) {
					if (mode == QR_MODE.MODE_NUMBER) return 10;
					if (mode == QR_MODE.MODE_ALPHA_NUM) return 9;
					if (mode == QR_MODE.MODE_8BIT_BYTE) return 8;
					if (mode == QR_MODE.MODE_KANJI) return 8;
				} else if (type < 27) {
					if (mode == QR_MODE.MODE_NUMBER) return 12;
					if (mode == QR_MODE.MODE_ALPHA_NUM) return 11;
					if (mode == QR_MODE.MODE_8BIT_BYTE) return 16;
					if (mode == QR_MODE.MODE_KANJI) return 10;
				} else if (type < 41) {
					if (mode == QR_MODE.MODE_NUMBER) return 14;
					if (mode == QR_MODE.MODE_ALPHA_NUM) return 13;
					if (mode == QR_MODE.MODE_8BIT_BYTE) return 16;
					if (mode == QR_MODE.MODE_KANJI) return 12;
				} else {
					throw new Error("type:" + type);
				}
				throw new Error("mode:" + mode);
			},
		};

		var QRMath = {
			gexp: new Array(256),
			glog: new Array(256),
			init: function () {
				for (var i = 0; i < 8; i++) QRMath.gexp[i] = 1 << i;
				for (var i = 8; i < 256; i++) {
					QRMath.gexp[i] = QRMath.gexp[i - 4] ^ QRMath.gexp[i - 5] ^ QRMath.gexp[i - 6] ^ QRMath.gexp[i - 8];
				}
				for (var i = 0; i < 255; i++) QRMath.glog[QRMath.gexp[i]] = i;
			},
		};
		QRMath.init();

		function QRPolynomial(num, shift) {
			if (num.length == void 0) throw new Error(num.length + "/" + shift);
			var offset = 0;
			while (offset < num.length && num[offset] == 0) offset++;
			this.num = new Array(num.length - offset + shift);
			for (var i = 0; i < num.length - offset; i++) {
				this.num[i] = num[i + offset];
			}
		}
		QRPolynomial.prototype = {
			get: function (index) {
				return this.num[index];
			},
			getLength: function () {
				return this.num.length;
			},
			multiply: function (e) {
				var num = new Array(this.getLength() + e.getLength() - 1);
				for (var i = 0; i < this.getLength(); i++) {
					for (var j = 0; j < e.getLength(); j++) {
						num[i + j] ^= QRMath.gexp[QRMath.glog[this.get(i)] + QRMath.glog[e.get(j)]];
					}
				}
				return QRPolynomial(num, 0);
			},
			mod: function (e) {
				if (this.getLength() - e.getLength() < 0) return this;
				var ratio = QRMath.gexp[QRMath.glog[this.get(0)] - QRMath.glog[e.get(0)]];
				var num = new Array(this.getLength());
				for (var i = 0; i < this.getLength(); i++) num[i] = this.get(i);
				for (var i = 0; i < e.getLength(); i++) num[i] ^= QRMath.gexp[QRMath.glog[e.get(i)] + ratio];
				return QRPolynomial(num, 0).mod(e);
			},
		};

		function QRRSBlock(totalCount, dataCount) {
			this.totalCount = totalCount;
			this.dataCount = dataCount;
		}
		QRRSBlock.RS_BLOCK_TABLE = [
			[1, 26, 19], [1, 26, 16], [1, 26, 13], [1, 26, 9],
			[1, 44, 34], [1, 44, 28], [1, 44, 22], [1, 44, 16],
			[1, 70, 55], [1, 70, 44], [2, 35, 17], [2, 35, 13],
			[1, 100, 80], [2, 50, 32], [2, 50, 24], [4, 25, 9],
			[1, 134, 108], [2, 67, 43], [2, 33, 15, 2, 34, 16], [2, 33, 11, 2, 34, 12],
			[2, 86, 68], [4, 43, 27], [4, 43, 19], [4, 43, 15],
			[2, 98, 78], [4, 49, 31], [2, 32, 14, 4, 33, 15], [4, 39, 13, 1, 40, 14],
			[2, 121, 97], [2, 60, 38, 2, 61, 39], [4, 40, 18, 2, 41, 19], [4, 40, 14, 2, 41, 13],
			[2, 146, 116], [3, 58, 36, 2, 59, 37], [4, 36, 16, 4, 37, 17], [4, 36, 12, 4, 37, 11],
			[2, 86, 68, 2, 87, 69], [4, 69, 43, 1, 70, 44], [6, 43, 19, 2, 44, 20], [6, 43, 15, 2, 44, 14],
		];
		QRRSBlock.getRSBlocks = function (typeNumber, errorCorrectLevel) {
			var rsBlock = QRRSBlock.RS_BLOCK_TABLE[(typeNumber - 1) * 4 + errorCorrectLevel];
			if (rsBlock == void 0) throw new Error("bad rs block @ typeNumber:" + typeNumber + "/errorCorrectLevel:" + errorCorrectLevel);
			var r = [];
			for (var i = 0; i < rsBlock.length; i += 3) {
				var count = rsBlock[i];
				var totalCount = rsBlock[i + 1];
				var dataCount = rsBlock[i + 2];
				for (var j = 0; j < count; j++) r.push(new QRRSBlock(totalCount, dataCount));
			}
			return r;
		};

		function QR8bitByte(data) {
			this.mode = QR_MODE.MODE_8BIT_BYTE;
			this.data = data;
		}
		QR8bitByte.prototype = {
			getLength: function () {
				return this.data.length;
			},
			write: function (buffer) {
				for (var i = 0; i < this.data.length; i++) {
					buffer.put(this.data.charCodeAt(i), 8);
				}
			},
		};

		// Find smallest version that fits (limit to version 5 to guarantee capacity for /kiosk)
		var typeNumber = 1;
		var testQR = null;
		while (typeNumber <= 10) {
			try {
				testQR = new QRCodeModel(typeNumber, errorCorrectLevel);
				testQR.addData(text);
				testQR.make();
				break;
			} catch (e) {
				typeNumber++;
			}
		}
		if (!testQR) throw new Error("Data too long for QR code");

		// Create canvas
		var moduleCount = testQR.getModuleCount();
		var canvas = document.createElement("canvas");
		var ctx = canvas.getContext("2d");
		var cellSize = Math.floor(size / moduleCount);
		canvas.width = canvas.height = cellSize * moduleCount;

		ctx.fillStyle = "#ffffff";
		ctx.fillRect(0, 0, canvas.width, canvas.height);
		ctx.fillStyle = "#000000";

		for (var row = 0; row < moduleCount; row++) {
			for (var col = 0; col < moduleCount; col++) {
				if (testQR.isDark(row, col)) {
					ctx.fillRect(col * cellSize, row * cellSize, cellSize, cellSize);
				}
			}
		}

		return canvas;
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
	}

	function reset() {
		clearTimeout(idleTimer);
		var hadPatient = !!S.patient;
		S = fresh();
		busy = false;
		render();
		if (hadPatient && !BOOT.preview)
			call(KIOSK, "end_session").catch(function () {});
	}

	["pointerdown", "keydown", "input", "touchstart"].forEach(function (name) {
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
			abhaAadhaar: abhaAadhaar,
			abhaOtp: abhaOtp,
			abhaAddress: abhaAddress,
			abhaDone: abhaDone,
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

	function result() {
		var p = S.patient;
		var actions = [button("Print my token", printSlip)];
		if (BOOT.enable_appointment)
			actions.push(button("Book my appointment", bookAppointment, "secondary"));
		if (BOOT.enable_abha && !p.abha_linked)
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
	// ABHA: create by Aadhaar OTP, create by mobile OTP, or link an ABHA the patient already has
	// ----------------------------------------------------------------------------------------------

	function abhaMethod() {
		return h("div", { class: "screen" }, [
			h("h1", {}, "ABHA"),
			h("p", { class: "lead" }, "Choose how you would like to continue."),
			h("div", { class: "actions stack" }, [
				button("Create ABHA with Aadhaar", function () {
					S.flow = "aadhaar";
					go("abhaAadhaar");
				}),
				button(
					"Create ABHA with mobile number",
					function () {
						S.flow = "mobile";
						go("abhaAadhaar");
					},
					"secondary",
				),
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

	// First input of every flow: Aadhaar number, mobile number or ABHA address
	function abhaAadhaar() {
		var cfg = {
			aadhaar: [
				"Aadhaar number",
				{ inputmode: "numeric", maxlength: 14, placeholder: "12 digit number" },
			],
			mobile: [
				"Mobile number",
				{ type: "tel", inputmode: "numeric", value: S.mobile },
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
			mobile: function () {
				S.mobile = input;
				return call(ABHA, "send_mobile_otp_create", {
					patient: p,
					mobile: input,
				});
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
		if (S.flow === "mobile") {
			return run(
				call(ABHA, "verify_mobile_otp_create", {
					patient: p,
					txn_id: t,
					otp: otp,
				}),
				function (verified) {
					S.txn = verified.txn_id;
					loadSuggestions();
				},
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
